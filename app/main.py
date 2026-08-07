from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.routers import auth, webhook, inbox, superadmin
from app.services.websocket_manager import manager
from app.database import Base, engine
from app import models

from sqlalchemy import inspect, text

# Ensure database tables exist on startup without crashing the container
try:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    
    # Check qa_bot_configs columns
    columns_bot = [col["name"] for col in inspector.get_columns("qa_bot_configs")]
    if "n8n_webhook_url" not in columns_bot:
        print("[Database] Adding n8n_webhook_url column to qa_bot_configs table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_bot_configs ADD COLUMN n8n_webhook_url TEXT"))
    if "flow_data" not in columns_bot:
        print("[Database] Adding flow_data column to qa_bot_configs table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_bot_configs ADD COLUMN flow_data JSON"))
            
    # Check qa_contacts columns
    columns_contacts = [col["name"] for col in inspector.get_columns("qa_contacts")]
    if "is_list_contact" not in columns_contacts:
        print("[Database] Adding is_list_contact column to qa_contacts table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_contacts ADD COLUMN is_list_contact BOOLEAN DEFAULT FALSE"))

    # Check qa_conversations columns
    columns_conv = [col["name"] for col in inspector.get_columns("qa_conversations")]
    if "is_flagged" not in columns_conv:
        print("[Database] Adding is_flagged column to qa_conversations table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_conversations ADD COLUMN is_flagged BOOLEAN DEFAULT FALSE"))
    if "flag_type" not in columns_conv:
        print("[Database] Adding flag_type column to qa_conversations table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_conversations ADD COLUMN flag_type TEXT DEFAULT 'none'"))

    # Ensure indexes for high performance queries
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qa_conv_tenant_status ON qa_conversations (tenant_id, status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qa_conv_last_msg_at ON qa_conversations (last_message_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qa_msg_conv_sender ON qa_messages (conversation_id, sender_type)"))
    except Exception as idx_err:
        print(f"[Database] Index creation notice: {idx_err}")

    # Check qa_messages columns
    columns_msg = [col["name"] for col in inspector.get_columns("qa_messages")]
    if "internal_note" not in columns_msg:
        print("[Database] Adding internal_note column to qa_messages table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_messages ADD COLUMN internal_note BOOLEAN DEFAULT FALSE"))

    # Check qa_tenants columns for all tenant fields and billing
    if inspector.has_table("qa_tenants"):
        columns_tenants = [col["name"] for col in inspector.get_columns("qa_tenants")]
        tenant_cols_to_add = {
            "billing_mode": "VARCHAR(20) DEFAULT 'prepaid'",
            "balance": "NUMERIC(10, 2) DEFAULT 0.00",
            "postpaid_limit": "NUMERIC(10, 2) DEFAULT 100.00",
            "cnpj": "VARCHAR(20)",
            "segment": "VARCHAR(100) DEFAULT 'hotel'",
            "status": "VARCHAR(50) DEFAULT 'active'",
            "plan_type": "VARCHAR(50) DEFAULT 'free'",
            "plan_id": "VARCHAR(36)",
            "max_users": "INTEGER DEFAULT 5",
            "logo_url": "TEXT"
        }
        for col_name, col_def in tenant_cols_to_add.items():
            if col_name not in columns_tenants:
                print(f"[Database] Adding {col_name} column to qa_tenants table...")
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE qa_tenants ADD COLUMN {col_name} {col_def}"))
                except Exception as col_err:
                    print(f"[Database] Could not add column {col_name}: {col_err}")

        # Drop legacy restrictive plan_type check constraint if present on PostgreSQL
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE qa_tenants DROP CONSTRAINT IF EXISTS qa_tenants_plan_type_check"))
        except Exception as constraint_err:
            print(f"[Database] Could not drop constraint qa_tenants_plan_type_check: {constraint_err}")

    # Auto-migration check: If target DB has 0 tenants, automatically pull and import historical data from Supabase!
    try:
        with engine.begin() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM qa_tenants")).scalar() or 0
            if cnt == 0:
                print("[Auto-Migration] Target database has 0 tenants. Triggering automated migration from Supabase...")
                try:
                    from migrate_supabase_to_easypanel import run_migration
                    run_migration(target_engine=engine)
                except Exception as mig_err:
                    print(f"[Auto-Migration] Migration process error: {mig_err}")
    except Exception as check_err:
        print(f"[Auto-Migration] Could not check tenant count: {check_err}")

    # Deduplicação de contatos e conversas legadas no startup (somente referência, sem purge automático)
    try:
        from app.database import SessionLocal
        from app.services.webhook_processor import deduplicate_all_contacts_and_conversations
        from sqlalchemy import text as sql_text
        db_cleanup = SessionLocal()
        deduplicate_all_contacts_and_conversations(db_cleanup)
        db_cleanup.execute(sql_text("UPDATE qa_contacts SET is_list_contact = FALSE"))
        db_cleanup.commit()
        db_cleanup.close()
        print("[Startup] Deduplication completed successfully.")
    except Exception as cleanup_err:
        print(f"[Startup Cleanup Notice] {cleanup_err}")
except Exception as e:
    print(f"[Database] Error creating/updating tables on startup: {e}")

app = FastAPI(
    title="Q-aura Atendimentos API",
    description="Multi-tenant backend for Omnichannel Customer Service Platform",
    version="1.0.0"
)

# Configure CORS for multi-tenant subdomains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/{tenant_id}")
async def websocket_endpoint(websocket: WebSocket, tenant_id: str):
    await manager.connect(tenant_id, websocket)
    try:
        while True:
            # Keep connection alive; receive ping/pong from client
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(tenant_id, websocket)
    except Exception as err:
        print(f"[WS Notice] Conexão encerrada com exceção ({tenant_id}): {err}")
        manager.disconnect(tenant_id, websocket)

# Include Routers
from app.routers import billing
app.include_router(auth.router)
app.include_router(webhook.router)
app.include_router(inbox.router)
app.include_router(superadmin.router)
app.include_router(billing.router)

# Mount uploads static directory for media files
uploads_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Mount frontend static files at root
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
else:
    @app.get("/")
    def read_root():
        return {
            "status": "healthy",
            "service": "Q-aura Backend (Sem frontend)",
            "version": "1.0.0"
        }

