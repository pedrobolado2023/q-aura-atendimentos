from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from app.routers import auth, webhook, inbox
from app.services.websocket_manager import manager

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

