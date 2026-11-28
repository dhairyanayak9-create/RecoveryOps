from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/business_platform"
    jwt_secret: str = "change-this-in-production"
    jwt_exp_minutes: int = 720
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
