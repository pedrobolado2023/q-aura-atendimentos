import traceback
from app.database import SessionLocal, engine, Base
from app.models import Tenant, User
from app.auth import get_password_hash

def seed():
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        from app.models import Plan
        
        # 1. Create SaaS Plans
        plans_data = [
            {
                "name": "Básico",
                "description": "Plano de entrada com caixa de entrada WhatsApp e chatbot básico",
                "price_monthly": 99.00,
                "modules": ["inbox", "chatbot", "dashboard"],
                "max_users": 3
            },
            {
                "name": "Pro",
                "description": "Plano completo com CRM, marketing e gestão de equipe",
                "price_monthly": 249.00,
                "modules": ["inbox", "chatbot", "dashboard", "crm", "team"],
                "max_users": 10
            },
            {
                "name": "Enterprise",
                "description": "Plano avançado com todos os módulos e suporte à IA",
                "price_monthly": 499.00,
                "modules": ["inbox", "chatbot", "dashboard", "crm", "team", "meta_settings"],
                "max_users": 50
            }
        ]
        
        created_plans = {}
        for plan_info in plans_data:
            plan = db.query(Plan).filter(Plan.name == plan_info["name"]).first()
            if not plan:
                plan = Plan(
                    name=plan_info["name"],
                    description=plan_info["description"],
                    price_monthly=plan_info["price_monthly"],
                    modules=plan_info["modules"],
                    max_users=plan_info["max_users"],
                    is_active=True
                )
                db.add(plan)
                db.flush()
                print(f"Plano '{plan.name}' criado com sucesso.")
            created_plans[plan.name] = plan.id
            
        db.commit()

        # 2. Setup Demo Hotel (Tenant)
        tenant_id = "00000000-0000-0000-0000-000000000000"
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        pro_plan_id = created_plans.get("Pro")
        
        if not tenant:
            tenant = Tenant(
                id=tenant_id,
                name="Q-aura Demo Hotel",
                subdomain="demo",
                plan_type="pro",
                status="active",
                plan_id=pro_plan_id,
                max_users=10,
                custom_modules=[]
            )
            db.add(tenant)
            db.commit()
            print("Tenant 'Q-aura Demo Hotel' criado com sucesso com plano Pro.")
        else:
            # Upgrade existing demo tenant to use the Pro plan
            if not tenant.plan_id and pro_plan_id:
                tenant.plan_id = pro_plan_id
                db.commit()
                print("Tenant demo atualizado com o plano Pro.")
            else:
                print("Tenant demo já existe.")

        # 3. Create Tenant Administrator User
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

        # 4. Create Superadmin User
        superadmin = db.query(User).filter(User.email == "superadmin@qaura.com").first()
        if not superadmin:
            superadmin = User(
                email="superadmin@qaura.com",
                password_hash=get_password_hash("superadmin123"),
                name="Superadmin Global",
                role="superadmin",
                tenant_id=None,  # Superadmin has no tenant
                status="offline"
            )
            db.add(superadmin)
            db.commit()
            print("Superadmin 'superadmin@qaura.com' (senha: superadmin123) criado com sucesso.")
        else:
            print("Superadmin já existe.")
            
    except Exception as e:
        db.rollback()
        traceback.print_exc()
        print(f"Erro ao popular banco de dados: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
