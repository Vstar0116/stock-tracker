from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://stock:stock@localhost:5432/stock"
    # ponytail: insecure default for local dev only -- set JWT_SECRET_KEY in .env
    # for any real deployment, or every restart's tokens are guessable.
    jwt_secret_key: str = "dev-secret-change-me-dev-secret-change-me"
    jwt_expire_minutes: int = 480  # one workday

    # Natural-language screen translation (POST /api/screens/from-text). Leave
    # unset to disable the feature -- the endpoint 422s with a clear message
    # rather than crashing. Served via Groq's free OpenAI-compatible endpoint
    # -- get a key at https://console.groq.com/keys.
    #
    # Tried NVIDIA's nemotron-3.5-lightning-30b-a3b first (free, confirmed
    # function-calling) but it never produced crossed_above/crossed_below --
    # every crossover screen silently degraded to a plain (wrong) comparison.
    # Also tried Gemini 3.6 Flash, which rejects our recursive rule-tree
    # schema outright (its function-calling schema translator can't handle a
    # required, non-nullable $ref loop). GPT-OSS 120B on Groq is the only one
    # of the three that got every crossover right in testing.
    groq_api_key: str | None = None
    nl_screen_model: str = "openai/gpt-oss-120b"


settings = Settings()
