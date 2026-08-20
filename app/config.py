from pydantic import AliasChoices, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 (see app/security.py) wants a key at least as long as its hash output --
# PyJWT itself warns below this. Enforced here so a weak key fails at startup,
# not silently inside the first login request.
MIN_JWT_SECRET_KEY_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Only meaningful value today is "production" (see the database_url check
    # below) -- anything else, including unset, is treated as local dev.
    app_env: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"))

    # No default -- every deployment signing its own tokens with the same
    # placeholder is a shared-secret vulnerability, not a convenience.
    jwt_secret_key: str
    jwt_expire_minutes: int = 480  # one workday

    # Fine to default locally -- docker-compose ships the matching stock/stock
    # user. Refused below if this is still localhost with APP_ENV=production.
    database_url: str = "postgresql+psycopg2://stock:stock@localhost:5432/stock"

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

    # Slack-compatible incoming webhook for job-failure alerts (see
    # app/services/alerting.py). Leave unset to disable alerting entirely --
    # every alert call becomes a no-op, so local dev is unaffected.
    alert_webhook_url: str | None = None

    # Comma-separated allowed CORS origins. Defaults to the local Vite dev
    # server; a production deployment MUST override this to the real
    # deployed frontend origin(s) -- never "*", since requests carry a
    # bearer token via fetch (see frontend/src/lib/api.ts), not cookies, but
    # a wildcard origin is still a needless widening of what can even
    # attempt a cross-origin request against a logged-in browser.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Off-host destination for the scheduled logical backup (app/jobs/backup_db.py)
    # of the tables with no automated re-ingestion source -- see DEPLOYMENT.md
    # "What's genuinely irreplaceable". Any S3-compatible endpoint works: leave
    # backup_s3_endpoint_url unset for real AWS S3, or point it at Cloudflare
    # R2 / Backblaze B2 / DigitalOcean Spaces / etc. Leave backup_s3_bucket
    # unset to leave the job unconfigured -- it refuses to run rather than
    # silently doing nothing useful (see backup_db.run()).
    backup_s3_bucket: str | None = None
    backup_s3_endpoint_url: str | None = None
    backup_s3_region: str | None = None
    backup_s3_access_key_id: str | None = None
    backup_s3_secret_access_key: str | None = None
    backup_s3_prefix: str = "stock-tracker-backups"

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_secret_key_must_be_strong(cls, v: str) -> str:
        if len(v.encode()) < MIN_JWT_SECRET_KEY_BYTES:
            raise ValueError(
                f"is only {len(v.encode())} bytes -- HS256 needs at least {MIN_JWT_SECRET_KEY_BYTES}. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return v

    @model_validator(mode="after")
    def _no_localhost_database_in_production(self) -> "Settings":
        if self.app_env.lower() == "production" and (
            "localhost" in self.database_url or "127.0.0.1" in self.database_url
        ):
            raise ValueError(
                "database_url points at localhost but APP_ENV=production -- set DATABASE_URL "
                "to the real production database before starting"
            )
        return self


def _load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = "\n".join(f"  - {'.'.join(str(p) for p in e['loc']).upper()}: {e['msg']}" for e in exc.errors())
        raise SystemExit(
            "Refusing to start -- invalid configuration:\n"
            f"{problems}\n\n"
            "Set the required environment variables (see .env.example) and try again."
        ) from None


settings = _load_settings()
