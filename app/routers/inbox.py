import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional
from app.database import get_db
from app.models import User, Tenant, Conversation, Message, Contact, MetaCredential
from app.schemas import ConversationResponse, MessageResponse
from app.auth import get_current_user, get_current_tenant
from app.config import settings

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    query = db.query(Conversation).filter(Conversation.tenant_id == current_tenant.id)
    if status_filter:
        query = query.filter(Conversation.status == status_filter)
    return query.order_by(Conversation.last_message_at.desc()).all()

@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
def get_messages(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    # Verify conversation belongs to tenant
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    return db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).all()

@router.post("/send-message", response_model=MessageResponse)
async def send_message(
    conversation_id: UUID,
    body: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    # 1. Verify conversation
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    contact = db.query(Contact).filter(Contact.id == convo.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # 2. Get Meta API credentials for tenant
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Meta credentials not configured for this tenant")

    # 3. Post to Meta API (WhatsApp Cloud API)
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": contact.phone_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body
        }
    }

    meta_message_id = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(meta_url, headers=headers, json=payload)
            if response.status_code == 200:
                res_data = response.json()
                meta_message_id = res_data.get("messages", [{}])[0].get("id")
            else:
                # Handle/log failure, keep status as failed
                pass
        except Exception as e:
            # log exception
            pass

    # 4. Save to Database
    msg = Message(
        conversation_id=conversation_id,
        sender_type="agent",
        sender_id=current_user.id,
        message_type="text",
        body=body,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)
    
    # Update last message timestamp
    convo.last_message_at = convo.updated_at
    db.commit()
    db.refresh(msg)
    return msg

@router.post("/conversations/{conversation_id}/assign", response_model=ConversationResponse)
def assign_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Assigns the conversation to the currently logged in user and marks it active.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.assigned_user_id = current_user.id
    convo.status = "active"
    db.commit()
    db.refresh(convo)
    return convo

@router.post("/conversations/{conversation_id}/resolve", response_model=ConversationResponse)
def resolve_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Marks the conversation as resolved.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.status = "resolved"
    db.commit()
    db.refresh(convo)
    return convo

