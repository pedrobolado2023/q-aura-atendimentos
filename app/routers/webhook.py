import hmac
import hashlib
import json
from fastapi import APIRouter, Depends, HTTPException, Request, Response, BackgroundTasks, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import MetaCredential, WebhookEvent
from app.config import settings
from app.services.webhook_processor import process_webhook_payload
from app.services.websocket_manager import manager

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

@router.get("/{tenant_id}")
def verify_webhook(tenant_id: str, request: Request, db: Session = Depends(get_db)):
    """
    Webhook verification request sent by Meta to validate the endpoint.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe":
            # Retrieve the correct verify token from DB
            creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
            if creds and creds.verify_token == token:
                return Response(content=challenge, media_type="text/plain")
            else:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token mismatch")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing parameter")

@router.post("/{tenant_id}")
async def receive_webhook(tenant_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Receives incoming webhook payloads (messages, status updates) from Meta WhatsApp Cloud API.
    Uses SHA256 Signature verification.
    """
    # 1. Read raw body payload
    body = await request.body()
    
    # 2. Signature Validation (if App Secret is configured and not default)
    if settings.META_APP_SECRET and settings.META_APP_SECRET != "your_meta_app_secret":
        signature_header = request.headers.get("X-Hub-Signature-256")
        if not signature_header:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature header")
        
        # Header starts with sha256=
        sha_name, signature = signature_header.split("=")
        if sha_name != "sha256":
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Signature format unsupported")
            
        mac = hmac.new(settings.META_APP_SECRET.encode('utf-8'), msg=body, digestmod=hashlib.sha256)
        expected_signature = mac.hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid request signature")

    # 3. Parse payload JSON
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    # 4. Save Event to DB for auditing
    event = WebhookEvent(
        tenant_id=tenant_id,
        provider="meta",
        payload=payload,
        processed=False
    )
    db.add(event)
    db.commit()

    # 5. Process Webhook in background task (async processing)
    background_tasks.add_task(
        process_webhook_payload,
        tenant_id=tenant_id,
        payload=payload,
        websocket_broadcast_fn=manager.broadcast_to_tenant
    )

    return {"status": "event_queued"}
