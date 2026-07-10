from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

from urllib.parse import urlparse, quote_plus

# Normaliza a URL do banco (SQLAlchemy exige postgresql:// em vez de postgres://)
db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# Faz o encoding de caracteres especiais na senha caso existam
if not db_url.startswith("sqlite"):
    try:
        parsed = urlparse(db_url)
        if parsed.password:
            # Se a senha contiver caracteres especiais não codificados, codifica-os
            encoded_password = quote_plus(parsed.password)
            
            # Reconstrói a parte do usuário e senha
            netloc = parsed.username
            if encoded_password:
                netloc += f":{encoded_password}"
            if parsed.hostname:
                netloc += f"@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
                
            db_url = parsed._replace(netloc=netloc).geturl()
    except Exception as e:
        print(f"Erro ao tratar URL do banco de dados: {e}")

# Configure SQLite check_same_thread if applicable
if db_url.startswith("sqlite"):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
else:
    engine = create_engine(db_url)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
