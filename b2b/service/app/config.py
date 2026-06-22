from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./b2b.db"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    MODERATION_URL: str = "http://moderation:8000"
    B2B_TO_MOD_KEY: str = "dev-b2b-to-mod-key"
    B2C_URL: str = "http://b2c:8000"
    B2B_TO_B2C_KEY: str = "dev-b2b-to-b2c-key"

    model_config = {"env_prefix": "B2B_"}


settings = Settings()
