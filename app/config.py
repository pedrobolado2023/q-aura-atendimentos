import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Use local SQLite database by default for easy testing without needing Supabase credentials
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./q_aura.db")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-jwt-key-replace-in-production-1234567890")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v18.0")

settings = Settings()
