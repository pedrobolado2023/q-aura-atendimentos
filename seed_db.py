import traceback
from app.database import SessionLocal, engine, Base
from app.models import Tenant, User
from app.auth import get_password_hash

def seed():
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        tenant_id = "00000000-0000-0000-0000-000000000000"
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            tenant = Tenant(
                id=tenant_id,
                name="Q-aura Demo Hotel",
                subdomain="demo",
                plan_type="pro",
                status="active"
            )
            db.add(tenant)
            db.commit()
            print("Tenant 'Q-aura Demo Hotel' criado com sucesso.")
        else:
            print("Tenant demo já existe.")

        user = db.query(User).filter(User.email == "admin@qaura.com").first()
        if not user:
            user = User(
                email="admin@qaura.com",
                password_hash=get_password_hash("admin123"),
                name="Administrador Q-aura",
                role="administrator",
                tenant_id=tenant_id,
                status="offline"
            )
            db.add(user)
            db.commit()
            print("Usuário 'admin@qaura.com' (senha: admin123) criado com sucesso.")
        else:
            print("Usuário admin já existe.")
            
    except Exception as e:
        traceback.print_exc()
        print(f"Erro ao popular banco de dados: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
