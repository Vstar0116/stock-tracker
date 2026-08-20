"""Translate a plain-English screen description into our ScreenRule JSON tree.

Design (extends CLAUDE.md's "SQL not LLM" principle to screening): the LLM's
only job is syntactic translation, English -> our JSON rule format. It never
sees market data and never produces a match or an answer -- only
services/screening.py's compiled SQL does that.

Served via Groq's free OpenAI-compatible endpoint (openai/gpt-oss-120b) --
picked after comparing it against NVIDIA's nemotron-3.5-lightning-30b-a3b and
Gemini 3.6 Flash on crossover-detection accuracy; it was the only one of the
three that reliably produced crossed_above/crossed_below instead of silently
degrading a crossover into a plain comparison, or (Gemini) rejecting our
recursive rule-tree schema outright.

The field/operator vocabulary given to the model comes straight from
schemas/screen.py's own registries, not a hand-copied list, so the prompt
can't drift from what the schema actually accepts. And the model's raw output
is *always* revalidated against our Pydantic schema before this function
returns -- if it free-forms a field name or malforms the shape, that fails
here rather than reaching compile_screen().
"""

from __future__ import annotations

import json
import logging

import openai
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas.screen import CATEGORICAL_FIELDS, DAILY_NUMERIC_FIELDS, FUNDAMENTAL_FIELDS, ScreenRule

logger = logging.getLogger("nl_screen")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
TOOL_NAME = "build_screen_rule"


class NlScreenError(Exception):
    """Not configured, unreachable, refused, or produced a rule that fails
    our schema -- callers turn this into a 422, never into a screen."""


class _ToolInput(BaseModel):
    rule: ScreenRule


SYSTEM_PROMPT = f"""You translate a plain-English stock screen description into a JSON rule tree. \
You never answer the question, never see market data, and never invent fields outside this list.

Daily price/indicator fields (numeric, support "yesterday" and crossovers):
{", ".join(sorted(DAILY_NUMERIC_FIELDS))}

Fundamental fields (numeric, manually entered snapshots -- no "yesterday", no crossover):
{", ".join(sorted(FUNDAMENTAL_FIELDS))}

Categorical fields (use with "in", or a single-value "in" for equality):
{", ".join(sorted(CATEGORICAL_FIELDS))}

Rule grammar:
- compare: field <op> value, op is one of gt/gte/lt/lte/eq (never "ne" -- there's no way to \
display it), value is a number or {{"field": "<other field>", "multiplier": <number>, \
"when": "today"|"yesterday"}}
- between: field between low and high
- in: categorical field in a list of string values (use a single-element list for equality)
- crossed_above / crossed_below: field crossed a value today
- group: {{"op": "and"|"or", "rules": [...]}}, nest freely

"Midcap"/"largecap" and similar company-size language has no backing field -- drop it rather \
than inventing one. Call {TOOL_NAME} exactly once with the translated rule.

CRITICAL OUTPUT SHAPE: your tool call's top-level argument must be a single \
object {{"rule": <node>}}. Every <node> -- including ones nested inside a \
group's "rules" list -- MUST include a "type" field naming which kind of \
node it is. Never nest the type name as a JSON key instead of a "type" value.

Example -- "close above 100 and sector is Pharma":
{{"rule": {{"type": "group", "op": "and", "rules": [
  {{"type": "compare", "op": "gt", "field": "close", "value": 100}},
  {{"type": "in", "field": "sector", "values": ["Pharma"]}}
]}}}}"""


def nl_screen_status() -> tuple[bool, bool]:
    """(configured, reachable) for the admin status page. A short-timeout
    models-list call is cheap enough for an on-demand page load; not meant
    to be polled."""
    if not settings.groq_api_key:
        return False, False
    try:
        openai.OpenAI(base_url=GROQ_BASE_URL, api_key=settings.groq_api_key, timeout=3.0).models.list()
        return True, True
    except Exception:
        return True, False


def translate_to_rule(text: str) -> ScreenRule:
    if not settings.groq_api_key:
        raise NlScreenError("natural-language screening is not configured (set GROQ_API_KEY)")

    client = openai.OpenAI(base_url=GROQ_BASE_URL, api_key=settings.groq_api_key)
    try:
        response = client.chat.completions.create(
            model=settings.nl_screen_model,
            temperature=0,  # a translation task wants the same input to map the same way, not variety
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": TOOL_NAME,
                        "description": "Return the translated screen as a rule tree.",
                        "parameters": _ToolInput.model_json_schema(),
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        )
    except openai.APIError as exc:
        # exc's text is Groq's own raw error response -- not ours to hand an
        # authenticated app user verbatim (rate-limit/account details, etc.
        # aren't secrets, but aren't this app's business to relay either).
        # Logged here for real debugging; the client gets a fixed message.
        logger.error("nl_screen: translation model request failed: %s", exc)
        raise NlScreenError("could not reach the translation model, try again shortly") from exc

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise NlScreenError("the model did not return a rule tree")

    try:
        parsed = json.loads(tool_calls[0].function.arguments)
    except json.JSONDecodeError as exc:
        raise NlScreenError(f"the model's rule tree was not valid JSON: {exc}") from exc

    try:
        return _ToolInput.model_validate(parsed).rule
    except ValidationError as exc:
        raise NlScreenError(f"generated rule failed validation: {exc}") from exc
