from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.routers import auth, webhook, inbox, superadmin
from app.services.websocket_manager import manager
from app.database import Base, engine

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

    # Check qa_messages columns
    columns_msg = [col["name"] for col in inspector.get_columns("qa_messages")]
    if "internal_note" not in columns_msg:
        print("[Database] Adding internal_note column to qa_messages table...")
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE qa_messages ADD COLUMN internal_note BOOLEAN DEFAULT FALSE"))
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
            # Keep connection alive; receive messages from client if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(tenant_id, websocket)

# Include Routers
app.include_router(auth.router)
app.include_router(webhook.router)
app.include_router(inbox.router)
app.include_router(superadmin.router)

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

