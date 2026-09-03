import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Use local SQLite database by default for easy testing without needing Supabase credentials
    _db_url: str = os.getenv("DATABASE_URL", "sqlite:///./q_aura.db")
    if os.name != "nt" and "C:/" in _db_url:
        _db_url = "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "q_aura.db")
    DATABASE_URL: str = _db_url
    JWT_SECRET: str = os.getenv("JWT_SECRET", "super-secret-jwt-key-replace-in-production-1234567890")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v18.0")
    N8N_WEBHOOK_URL: str = os.getenv("N8N_WEBHOOK_URL", "")
    MP_ACCESS_TOKEN: str = os.getenv("MP_ACCESS_TOKEN", "APP_USR-4192080643307351-090116-f2200201b0ab00c1d5159973ccee1041-142018015")
    MP_PUBLIC_KEY: str = os.getenv("MP_PUBLIC_KEY", "APP_USR-f2d1d76a-b328-4d97-8de9-82fdefddcd0b")
    MP_CLIENT_ID: str = os.getenv("MP_CLIENT_ID", "4192080643307351")
    MP_CLIENT_SECRET: str = os.getenv("MP_CLIENT_SECRET", "4PlkiNQX1kFxbZ1sQpJq0yNXaJd2qYVM")

settings = Settings()
