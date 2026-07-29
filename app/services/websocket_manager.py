from fastapi import WebSocket
from typing import Dict, List, Any

class ConnectionManager:
    def __init__(self):
        # Maps tenant_id (str) to a list of active WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, tenant_id: Any, websocket: WebSocket):
        tenant_key = str(tenant_id)
        await websocket.accept()
        if tenant_key not in self.active_connections:
            self.active_connections[tenant_key] = []
        self.active_connections[tenant_key].append(websocket)

    def disconnect(self, tenant_id: Any, websocket: WebSocket):
        tenant_key = str(tenant_id)
        if tenant_key in self.active_connections:
            if websocket in self.active_connections[tenant_key]:
                self.active_connections[tenant_key].remove(websocket)
            if not self.active_connections[tenant_key]:
                del self.active_connections[tenant_key]

    async def broadcast_to_tenant(self, tenant_id: Any, message: dict):
        """
        Broadcast updates to all agents logged in to the same tenant.
        Ensures tenant_id is cast to string so UUID objects and str match identically.
        """
        tenant_key = str(tenant_id)
        if tenant_key in self.active_connections:
            for connection in self.active_connections[tenant_key][:]:
                try:
                    await connection.send_json(message)
                except Exception:
                    try:
                        self.active_connections[tenant_key].remove(connection)
                    except ValueError:
                        pass

manager = ConnectionManager()

