from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.routers import auth, webhook, inbox, superadmin
from app.services.websocket_manager import manager
from app.database import Base, engine
from app import models

from sqlalchemy import inspect, text

# Ensure database tables and columns exist on startup without crashing the container
try:
    Base.metadata.create_all(bind=engine)
    
    with engine.begin() as conn:
        # 1. Ensure new tables exist
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS qa_tags (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                name VARCHAR(50) NOT NULL,
                color VARCHAR(20) DEFAULT '#6366f1',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS qa_conversation_tags (
                conversation_id VARCHAR(36) NOT NULL,
                tag_id VARCHAR(36) NOT NULL,
                PRIMARY KEY (conversation_id, tag_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS qa_message_templates (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                label VARCHAR(255) NOT NULL,
                language VARCHAR(20) DEFAULT 'pt_BR',
                category VARCHAR(50) DEFAULT 'UTILITY',
                body_text TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # 2. Add columns idempotently
        alter_statements = [
            "ALTER TABLE qa_conversations ADD COLUMN IF NOT EXISTS csat_score INTEGER",
            "ALTER TABLE qa_conversations ADD COLUMN IF NOT EXISTS csat_sent_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE qa_conversations ADD COLUMN IF NOT EXISTS is_flagged BOOLEAN DEFAULT FALSE",
            "ALTER TABLE qa_conversations ADD COLUMN IF NOT EXISTS flag_type TEXT DEFAULT 'none'",
            "ALTER TABLE qa_contacts ADD COLUMN IF NOT EXISTS deal_value DOUBLE PRECISION DEFAULT 0.0",
            "ALTER TABLE qa_contacts ADD COLUMN IF NOT EXISTS kanban_stage VARCHAR(50) DEFAULT 'lead'",
            "ALTER TABLE qa_contacts ADD COLUMN IF NOT EXISTS is_list_contact BOOLEAN DEFAULT FALSE",
            "ALTER TABLE qa_messages ADD COLUMN IF NOT EXISTS internal_note BOOLEAN DEFAULT FALSE",
            "ALTER TABLE qa_bot_configs ADD COLUMN IF NOT EXISTS n8n_webhook_url TEXT",
            "ALTER TABLE qa_bot_configs ADD COLUMN IF NOT EXISTS flow_data JSON",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS billing_mode VARCHAR(20) DEFAULT 'prepaid'",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS balance NUMERIC(10, 2) DEFAULT 0.00",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS postpaid_limit NUMERIC(10, 2) DEFAULT 100.00",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS cnpj VARCHAR(20)",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS segment VARCHAR(100) DEFAULT 'hotel'",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'active'",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS plan_type VARCHAR(50) DEFAULT 'free'",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS plan_id VARCHAR(36)",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS max_users INTEGER DEFAULT 5",
            "ALTER TABLE qa_tenants ADD COLUMN IF NOT EXISTS logo_url TEXT",
            "ALTER TABLE qa_tenants DROP CONSTRAINT IF EXISTS qa_tenants_plan_type_check"
        ]
        for stmt in alter_statements:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                print(f"[Database Migration Notice] {stmt}: {e}")

        # 3. High-performance indexes
        index_statements = [
            "CREATE INDEX IF NOT EXISTS idx_qa_conv_tenant_status ON qa_conversations (tenant_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_qa_conv_last_msg_at ON qa_conversations (last_message_at)",
            "CREATE INDEX IF NOT EXISTS idx_qa_msg_conv_sender ON qa_messages (conversation_id, sender_type)",
            "CREATE INDEX IF NOT EXISTS idx_qa_tags_tenant ON qa_tags(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_qa_msg_tpl_tenant ON qa_message_templates(tenant_id)"
        ]
        for idx in index_statements:
            try:
                conn.execute(text(idx))
            except Exception:
                pass

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

from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger("q_aura")

app = FastAPI(
    title="Q-aura Atendimentos API",
    description="Multi-tenant backend for Omnichannel Customer Service Platform",
    version="1.0.0"
)

# Enable GZip Compression for super fast response transfers (>1KB compressed automatically)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Configure CORS for multi-tenant subdomains
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"[Global Exception] Unhandled error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Ocorreu um erro interno no servidor. Por favor, tente novamente."}
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

