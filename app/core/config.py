from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized app configuration, loaded from environment variables / .env."""

    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"
    trusted_hosts: str = "localhost,127.0.0.1,testserver"

    mongodb_uri: str
    mongodb_db_name: str = "wazifny"

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    groq_api_key: str | None = None
    # Kept aligned with models currently available on the project's Groq key.
    # The previous llama-3.1-8b-instant model was retired and returns 404.
    groq_model: str = "openai/gpt-oss-20b"

    # Transactional mail is delivered through Resend's HTTPS API.
    resend_api_key: str | None = None
    resend_from_email: str = "notifications@wazifny.lb"
    resend_from_name: str = "Wazifny"
    resend_reply_to: str | None = None

    # Used to build links inside emails (password reset, etc.)
    frontend_url: str = "http://localhost:3000"

    # Ignore retired provider variables in existing local .env files. This
    # makes a Resend migration safe without requiring an all-at-once edit of
    # every developer/deployment environment.
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @field_validator("access_token_expire_minutes")
    @classmethod
    def validate_token_lifetime(cls, value: int) -> int:
        if not 1 <= value <= 1_440:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 1440")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.environment.lower() != "production":
            return self
        if len(self.jwt_secret_key) < 32 or self.jwt_secret_key.lower() in {"change-me", "secret", "password"}:
            raise ValueError("Production requires a unique JWT_SECRET_KEY of at least 32 characters")
        origins = self.cors_origin_list
        hosts = self.trusted_host_list
        if "*" in origins or any(not origin.startswith("https://") for origin in origins):
            raise ValueError("Production CORS_ORIGINS must contain explicit HTTPS origins")
        if "*" in hosts:
            raise ValueError("Production TRUSTED_HOSTS must not contain a wildcard")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]


settings = Settings()
