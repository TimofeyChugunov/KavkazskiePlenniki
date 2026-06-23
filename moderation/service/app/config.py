from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SECRET_KEY: str = "dev-moderation-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    B2B_URL: str = "http://b2b:8000"
    MOD_TO_B2B_KEY: str = "dev-mod-to-b2b-key"

    model_config = {"env_prefix": "MOD_"}


settings = Settings()
