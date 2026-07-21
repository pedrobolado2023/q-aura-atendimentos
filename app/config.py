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
    N8N_WEBHOOK_URL: str = os.getenv("N8N_WEBHOOK_URL", "")
    MP_ACCESS_TOKEN: str = os.getenv("MP_ACCESS_TOKEN", "APP_USR-1660401255426844-070508-f8eba77e1469c3cb8361607a324fd83e-142018015")
    MP_PUBLIC_KEY: str = os.getenv("MP_PUBLIC_KEY", "APP_USR-a1bae107-c4ca-4a3b-8bc2-370c4c25e986")

settings = Settings()
