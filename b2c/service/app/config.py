from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    B2B_URL: str = "http://b2b:8000"
    B2C_TO_B2B_KEY: str = "dev-b2c-to-b2b-key"
    SECRET_KEY: str = "dev-b2c-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    model_config = {"env_prefix": "B2C_"}


settings = Settings()
