import traceback
from app.database import SessionLocal, engine, Base
from app.models import Tenant, MetaCredential

def insert_meta_creds():
    # Make sure tables exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        tenant_id = "00000000-0000-0000-0000-000000000000"
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            print("Erro: Tenant demo não encontrado! Execute seed_db.py primeiro.")
            return

        phone_number_id = "1233641736489582"
        waba_id = "2491880031234542"
        access_token = "EAAe9px4cxNMBRxqjvnCOr040hKrwAWwUEpuGMan6Ofv0R5ogJfbzZCQPw39PHsoJAV738suLZA3lS61walswevVI6X1Uj4bvwgKyXm7kizoZCPRpqLD3WaWwRsF1CPOZCWyPOeoSO5oYUioZCy9ZBdtvnoWKrRV6VaFi5kBerb94jvzHZCZCHCqQSSphzQ7OTc4ZCwZCqeZALkvdL2XS1sZCFtyuxiFOdA6QxgmkTxF3ZBfZADsozQL1gkpEL0WomqsYCLDZCANhSHBKbvWZA1T5QcYU4cNhLldzZBSnTSvLgJ1lBDo8ZD"
        verify_token = "qaura_verify_token_2026"
        webhook_url = f"https://api.q-aura.com/api/webhook/{tenant_id}"

        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
        if creds:
            creds.phone_number_id = phone_number_id
            creds.waba_id = waba_id
            creds.permanent_access_token = access_token
            creds.verify_token = verify_token
            creds.webhook_url = webhook_url
            print("Credenciais Meta existentes atualizadas com sucesso.")
        else:
            creds = MetaCredential(
                tenant_id=tenant_id,
                phone_number_id=phone_number_id,
                waba_id=waba_id,
                permanent_access_token=access_token,
                verify_token=verify_token,
                webhook_url=webhook_url
            )
            db.add(creds)
            print("Novas credenciais Meta inseridas com sucesso.")
            
        db.commit()
    except Exception as e:
        traceback.print_exc()
        print(f"Erro ao inserir credenciais: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    insert_meta_creds()
