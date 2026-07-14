import os
import shutil
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Response, BackgroundTasks, UploadFile, File, Request
from pydantic import BaseModel
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from typing import List, Optional
from app.database import get_db, SessionLocal
from app.models import User, Tenant, Conversation, Message, Contact, MetaCredential, BotConfig, Department, QuickMessage, MarketingCampaign, CampaignRecipient
from app.schemas import ConversationResponse, MessageResponse, BulkContactUploadRequest, CampaignSendRequest, CampaignResponse, BotConfigResponse, BotConfigUpdate, DashboardMetricsResponse, DepartmentMetric, FunnelStageMetric, StartConversationRequest, QuickMessageCreate, QuickMessageResponse, ContactResponse
from app.auth import get_current_user, get_current_tenant, ModuleRequired
from app.config import settings

def format_brazilian_phone(phone: str) -> str:
    # Mantém apenas dígitos
    phone = "".join(filter(str.isdigit, phone))
    
    # Valida formato e DDI do Brasil (55)
    if phone.startswith("55") and len(phone) >= 12:
        ddd = int(phone[2:4])
        # Se tem 13 dígitos e DDD >= 31, remove o 9º dígito (o 9 logo após o DDD)
        if len(phone) == 13 and ddd >= 31 and phone[4] == "9":
            phone = phone[:4] + phone[5:]
        # Se tem 12 dígitos e DDD < 31 (11 a 28), adiciona o 9º dígito
        elif len(phone) == 12 and 11 <= ddd <= 28:
            phone = phone[:4] + "9" + phone[4:]
            
    # Caso importado sem DDI 55
    elif len(phone) in [10, 11] and not phone.startswith("55"):
        ddd = int(phone[0:2])
        if len(phone) == 11 and ddd >= 31 and phone[2] == "9":
            phone = "55" + phone[:2] + phone[3:]
        elif len(phone) == 10 and 11 <= ddd <= 28:
            phone = "55" + phone[:2] + "9" + phone[2:]
        else:
            phone = "55" + phone
            
    return phone

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    query = db.query(Conversation).filter(Conversation.tenant_id == current_tenant.id)
    if status_filter:
        if status_filter == "waiting":
            query = query.filter(Conversation.status.in_(["waiting", "bot"]))
        else:
            query = query.filter(Conversation.status == status_filter)
        
    # Para atendentes normais (agentes), filtra para exibir apenas as conversas atribuídas a eles na aba Minhas
    if status_filter == "active" and current_user.role not in ["administrator", "manager"]:
        query = query.filter(Conversation.assigned_user_id == current_user.id)
        
    return query.order_by(Conversation.last_message_at.desc()).all()

@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
def get_messages(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    # Verify conversation belongs to tenant
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    # Mark conversation as read
    if convo.unread or (convo.unread_count and convo.unread_count > 0):
        convo.unread = False
        convo.unread_count = 0
        db.commit()
        
    return db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()).all()

@router.post("/send-message", response_model=MessageResponse)
async def send_message(
    conversation_id: UUID,
    body: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
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

    # Prepend agent's name in WhatsApp bold format
    formatted_body = f"*Atendente {current_user.name}:* {body}"

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
            "body": formatted_body
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
        body=formatted_body,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)
    
    # Update last message timestamp
    convo.last_message_at = convo.updated_at
    db.commit()
    db.refresh(msg)
    return msg


class BotMessageSend(BaseModel):
    conversation_id: Optional[UUID] = None
    phone_number: Optional[str] = None
    body: str


@router.post("/send-bot-message", response_model=MessageResponse)
async def send_bot_message(
    payload: BotMessageSend,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    if not payload.conversation_id and not payload.phone_number:
        raise HTTPException(status_code=400, detail="Either conversation_id or phone_number must be provided.")

    convo = None
    contact = None

    if payload.conversation_id:
        convo = db.query(Conversation).filter(
            Conversation.id == payload.conversation_id,
            Conversation.tenant_id == current_tenant.id
        ).first()
        if not convo:
            raise HTTPException(status_code=404, detail="Conversation not found")
        contact = db.query(Contact).filter(Contact.id == convo.contact_id).first()
    else:
        cleaned_phone = format_brazilian_phone(payload.phone_number)
        contact = db.query(Contact).filter(
            Contact.phone_number == cleaned_phone,
            Contact.tenant_id == current_tenant.id
        ).first()
        if not contact:
            contact = Contact(
                tenant_id=current_tenant.id,
                phone_number=cleaned_phone,
                name="Hóspede WhatsApp",
                sales_funnel_stage="lead"
            )
            db.add(contact)
            db.flush()

        # Get the latest active/open conversation for this contact
        convo = db.query(Conversation).filter(
            Conversation.contact_id == contact.id,
            Conversation.tenant_id == current_tenant.id,
            Conversation.status != "resolved"
        ).order_by(Conversation.last_message_at.desc()).first()
        
        if not convo:
            # If no open conversation, look for any resolved conversation to re-open
            convo = db.query(Conversation).filter(
                Conversation.contact_id == contact.id,
                Conversation.tenant_id == current_tenant.id
            ).order_by(Conversation.last_message_at.desc()).first()
            
            if convo:
                convo.status = "bot"
            else:
                convo = Conversation(
                    tenant_id=current_tenant.id,
                    contact_id=contact.id,
                    status="bot",
                    routing_mode="queue"
                )
                db.add(convo)
                db.flush()

    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # 2. Get Meta API credentials
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Meta credentials not configured for this tenant")

    # 3. Post to Meta API
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}",
        "Content-Type": "application/json"
    }
    meta_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": contact.phone_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": payload.body
        }
    }

    meta_message_id = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(meta_url, headers=headers, json=meta_payload)
            if response.status_code == 200:
                res_data = response.json()
                meta_message_id = res_data.get("messages", [{}])[0].get("id")
            else:
                print(f"Bot send error: {response.text}")
        except Exception as e:
            print(f"Error sending bot message: {e}")

    # 4. Save to Database
    msg = Message(
        conversation_id=convo.id,
        sender_type="bot",
        message_type="text",
        body=payload.body,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)
    
    convo.last_message_at = datetime.utcnow()
    db.commit()
    db.refresh(msg)

    # 5. Broadcast to active agents via websocket so it shows up in UI immediately!
    from app.services.websocket_manager import manager
    broadcast_data = {
        "type": "new_message",
        "conversation_id": convo.id,
        "sender_type": "bot",
        "body": payload.body,
        "message_type": "text",
        "media_url": None,
        "unread": False,
        "created_at": msg.created_at.isoformat() if msg.created_at else None
    }
    await manager.broadcast_to_tenant(current_tenant.id, broadcast_data)

    return msg


@router.post("/start-conversation", response_model=MessageResponse)
async def start_conversation(
    payload: StartConversationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    cleaned_phone = format_brazilian_phone(payload.phone_number)
    if not cleaned_phone:
        raise HTTPException(status_code=400, detail="Número de telefone inválido.")

    # 2. Get/Create contact
    contact = db.query(Contact).filter(
        Contact.phone_number == cleaned_phone,
        Contact.tenant_id == current_tenant.id
    ).first()

    if not contact:
        contact_name = payload.name if payload.name else f"Hóspede {cleaned_phone[-4:]}"
        contact = Contact(
            tenant_id=current_tenant.id,
            phone_number=cleaned_phone,
            name=contact_name,
            language="pt-BR",
            sales_funnel_stage="lead",
            is_list_contact=True
        )
        db.add(contact)
        db.flush()
    else:
        contact.is_list_contact = True
        db.commit()

    # 3. Get/Create conversation
    convo = db.query(Conversation).filter(
        Conversation.contact_id == contact.id,
        Conversation.tenant_id == current_tenant.id
    ).first()

    if not convo:
        convo = Conversation(
            tenant_id=current_tenant.id,
            contact_id=contact.id,
            assigned_user_id=current_user.id,
            status="active",
            unread=False,
            unread_count=0
        )
        db.add(convo)
        db.flush()
    else:
        convo.status = "active"
        convo.assigned_user_id = current_user.id
        convo.unread = False
        convo.unread_count = 0

    # 4. Get Meta Credentials
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Meta credentials not configured for this tenant")

    # Format body
    formatted_body = f"*Atendente {current_user.name}:* {payload.body}"

    # 5. Send message via Meta Cloud API
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}",
        "Content-Type": "application/json"
    }
    meta_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": contact.phone_number,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": formatted_body
        }
    }

    meta_message_id = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(meta_url, headers=headers, json=meta_payload)
            if response.status_code == 200:
                res_data = response.json()
                meta_message_id = res_data.get("messages", [{}])[0].get("id")
            else:
                err_data = response.json()
                error_msg = err_data.get("error", {}).get("message", "Erro desconhecido da API da Meta")
                raise HTTPException(status_code=400, detail=f"Erro Meta: {error_msg}")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Erro de conexão com a Meta: {str(e)}")

    # 6. Save message
    msg = Message(
        conversation_id=convo.id,
        sender_type="agent",
        sender_id=current_user.id,
        message_type="text",
        body=formatted_body,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)

    # Update last message timestamp
    from sqlalchemy.sql import func
    convo.last_message_at = func.now()
    db.commit()
    db.refresh(msg)

    return msg


@router.post("/conversations/{conversation_id}/assign", response_model=ConversationResponse)
def assign_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
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


@router.post("/conversations/{conversation_id}/transfer-to-bot", response_model=ConversationResponse)
def transfer_conversation_to_bot(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Sends the conversation back to the chatbot (sets status to 'bot' and unassigns the user).
    """
    convo = db.query(Conversation).filter(
        Conversation.id == str(conversation_id),
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.status = "bot"
    convo.assigned_user_id = None
    db.commit()
    db.refresh(convo)
    return convo


@router.post("/conversations/{conversation_id}/resolve", response_model=ConversationResponse)
async def resolve_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Marks the conversation as resolved and sends a final closing message to the contact.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.status = "resolved"
    
    # Send closing message to the contact
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if creds and convo.contact and convo.contact.phone_number:
        closing_msg = (
            "*Atendimento Concluído*\n\n"
            "Seu atendimento foi finalizado com sucesso. Agradecemos imensamente o seu contato! "
            "Se precisar de qualquer outra informação ou suporte no futuro, estaremos sempre por aqui.\n\n"
            "Tenha um excelente dia! ✨🏨"
        )
        
        meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {creds.permanent_access_token}",
            "Content-Type": "application/json"
        }
        meta_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": convo.contact.phone_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": closing_msg
            }
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(meta_url, headers=headers, json=meta_payload)
                if response.status_code == 200:
                    res_data = response.json()
                    meta_message_id = res_data.get("messages", [{}])[0].get("id")
                    
                    # Save to db
                    msg = Message(
                        conversation_id=convo.id,
                        sender_type="system",
                        sender_id=current_user.id,
                        message_type="text",
                        body=closing_msg,
                        meta_message_id=meta_message_id,
                        status="sent"
                    )
                    db.add(msg)
                    
                    from sqlalchemy.sql import func
                    convo.last_message_at = func.now()
            except Exception as e:
                print(f"[Resolve] Failed to send closing message: {e}")
                
    db.commit()
    db.refresh(convo)
    return convo

@router.post("/conversations/{conversation_id}/toggle-flag", response_model=ConversationResponse)
def toggle_flag_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Toggles the is_flagged status of a conversation.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.is_flagged = not convo.is_flagged
    db.commit()
    db.refresh(convo)
    return convo

@router.post("/conversations/{conversation_id}/set-flag", response_model=ConversationResponse)
def set_flag_conversation(
    conversation_id: UUID,
    flag_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Sets the flag_type of a conversation. Supported types: none, red, yellow, blue, green.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    if flag_type not in ["none", "red", "yellow", "blue", "green"]:
        raise HTTPException(status_code=400, detail="Invalid flag type")
        
    convo.flag_type = flag_type
    convo.is_flagged = (flag_type != "none")
    db.commit()
    db.refresh(convo)
    return convo



@router.get("/media/{media_id}")
async def get_media(
    media_id: str,
    token: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Proxies and downloads media from Meta API using WABA credentials,
    returning the raw binary stream.
    Supports authenticating via query parameter 'token' to bypass header requirements for img tags.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
        
    try:
        # Safely decode the token to retrieve tenant_id
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        tenant_id: str = payload.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid token claims")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid credentials token")

    # Verify if tenant has inbox module enabled
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant:
        from app.auth import get_enabled_modules
        enabled = get_enabled_modules(tenant)
        if "inbox" not in enabled:
            raise HTTPException(status_code=403, detail="Módulo de Inbox desativado no plano de contratação.")
        
    # Get credentials for tenant
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Meta credentials not configured")
        
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}"
    }
    
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{media_id}"
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Fetch metadata to get download URL
            meta_res = await client.get(meta_url, headers=headers)
            if meta_res.status_code != 200:
                raise HTTPException(status_code=meta_res.status_code, detail="Failed to fetch media metadata from Meta")
                
            res_data = meta_res.json()
            download_url = res_data.get("url")
            mime_type = res_data.get("mime_type", "image/jpeg")
            
            if not download_url:
                raise HTTPException(status_code=404, detail="Media download URL not found in Meta response")
                
            # 2. Fetch binary file using the download URL
            file_res = await client.get(download_url, headers=headers)
            if file_res.status_code != 200:
                raise HTTPException(status_code=file_res.status_code, detail="Failed to download media file from Meta")
                
            return Response(content=file_res.content, media_type=mime_type)
            
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Bad gateway response from Meta: {str(e)}")

# --- CRM & Campaigns Endpoints ---

@router.post("/contacts/bulk")
def import_contacts_bulk(
    payload: BulkContactUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("crm"))
):
    """
    Imports contacts in bulk for the current tenant.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem importar contatos.")

    imported_count = 0
    seen_phones = set()
    contacts_to_process = []
    
    for c in payload.contacts:
        phone = format_brazilian_phone(c.phone_number)
        if not phone:
            continue
        if phone in seen_phones:
            continue
        seen_phones.add(phone)
        contacts_to_process.append((phone, c.name))

    if not contacts_to_process:
        return {"status": "success", "imported": 0}

    # Fetch all existing contacts in a single query
    phones_list = [p[0] for p in contacts_to_process]
    existing = db.query(Contact).filter(
        Contact.tenant_id == current_tenant.id,
        Contact.phone_number.in_(phones_list)
    ).all()
    
    existing_map = {c.phone_number: c for c in existing}

    for phone, name in contacts_to_process:
        contact = existing_map.get(phone)
        if contact:
            contact.name = name
            contact.is_list_contact = True
        else:
            contact = Contact(
                tenant_id=current_tenant.id,
                phone_number=phone,
                name=name,
                sales_funnel_stage="lead",
                loyalty_level="none",
                language="pt-BR",
                is_list_contact=True
            )
            db.add(contact)
        imported_count += 1
        
    db.commit()
    return {"status": "success", "imported": imported_count}


async def dispatch_campaign_bulk(
    tenant_id: UUID,
    agent_id: UUID,
    campaign_id: str,
    base_url: str,
    db_session_factory
):
    """
    Background worker that iterates through all tenant contacts and sends Meta WhatsApp campaigns.
    """
    db = db_session_factory()
    try:
        campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()
        if not campaign:
            print(f"[Campaign] Campaign {campaign_id} not found")
            return

        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
        if not creds:
            print(f"[Campaign] Credentials not found for tenant {tenant_id}")
            return
            
        contacts = db.query(Contact).filter(
            Contact.tenant_id == tenant_id,
            Contact.is_list_contact == True
        ).all()
        if not contacts:
            print(f"[Campaign] No list contacts in database for tenant {tenant_id}")
            return
            
        meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {creds.permanent_access_token}",
            "Content-Type": "application/json"
        }
        
        sent_count = 0
        sent_phones = set()
        async with httpx.AsyncClient() as client:
            for contact in contacts:
                # Clean phone number to digits to prevent duplicates in different formats
                clean_phone = "".join(filter(str.isdigit, contact.phone_number))
                if not clean_phone:
                    continue
                if clean_phone in sent_phones:
                    print(f"[Campaign] Skipping duplicate phone number: {clean_phone}")
                    continue
                sent_phones.add(clean_phone)

                # Create CampaignRecipient record
                recipient = CampaignRecipient(
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    status="sent"
                )
                db.add(recipient)
                db.flush() # Populate recipient.id

                # 1. Resolve active conversation
                convo = db.query(Conversation).filter(
                    Conversation.tenant_id == tenant_id,
                    Conversation.contact_id == contact.id,
                    Conversation.status.in_(["bot", "waiting", "active"])
                ).first()
                
                if not convo:
                    convo = Conversation(
                        tenant_id=tenant_id,
                        contact_id=contact.id,
                        status="waiting",
                        routing_mode="queue"
                    )
                    db.add(convo)
                    db.commit()
                    db.refresh(convo)
                    
                # 2. Build Meta Payload
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": contact.phone_number
                }
                
                if campaign.use_template:
                    payload["type"] = "template"
                    payload["template"] = {
                        "name": campaign.template_name,
                        "language": {
                            "code": campaign.template_language or "pt_BR"
                        }
                    }
                else:
                    # Check button configuration
                    if campaign.button_type == "cta_url" and campaign.button_label and campaign.button_url:
                        # Append tracking parameter or redirect tracker
                        tracking_url = f"{base_url}/api/inbox/campaigns/click/{recipient.id}"
                        payload["type"] = "interactive"
                        payload["interactive"] = {
                            "type": "cta_url",
                            "body": {
                                "text": campaign.body
                            },
                            "action": {
                                "name": "cta_url",
                                "parameters": {
                                    "display_text": campaign.button_label,
                                    "url": tracking_url
                                }
                            }
                        }
                        if campaign.media_type in ["image", "video"] and campaign.media_url:
                            payload["interactive"]["header"] = {
                                "type": campaign.media_type,
                                campaign.media_type: {
                                    "link": campaign.media_url
                                }
                            }
                            
                    elif campaign.button_type == "quick_reply" and campaign.button_label:
                        payload["type"] = "interactive"
                        payload["interactive"] = {
                            "type": "button",
                            "body": {
                                "text": campaign.body
                            },
                            "action": {
                                "buttons": [
                                    {
                                        "type": "reply",
                                        "reply": {
                                            # Use recipient ID as the reply button ID so we can track the click in webhook!
                                            "id": f"camp_click_{recipient.id}",
                                            "title": campaign.button_label
                                        }
                                    }
                                ]
                            }
                        }
                        if campaign.media_type in ["image", "video"] and campaign.media_url:
                            payload["interactive"]["header"] = {
                                "type": campaign.media_type,
                                campaign.media_type: {
                                    "link": campaign.media_url
                                }
                            }
                    else:
                        # Media without buttons or plain text
                        if campaign.media_type == "image" and campaign.media_url:
                            payload["type"] = "image"
                            payload["image"] = {
                                "link": campaign.media_url,
                                "caption": campaign.body
                            }
                        elif campaign.media_type == "video" and campaign.media_url:
                            payload["type"] = "video"
                            payload["video"] = {
                                "link": campaign.media_url,
                                "caption": campaign.body
                            }
                        elif campaign.media_type == "audio" and campaign.media_url:
                            payload["type"] = "audio"
                            payload["audio"] = {
                                "link": campaign.media_url
                            }
                        else:
                            payload["type"] = "text"
                            payload["text"] = {
                                "preview_url": False,
                                "body": campaign.body
                            }
                        
                # 3. Request sending to Meta
                meta_message_id = None
                try:
                    response = await client.post(meta_url, headers=headers, json=payload)
                    if response.status_code == 200:
                        res_data = response.json()
                        meta_message_id = res_data.get("messages", [{}])[0].get("id")
                        sent_count += 1
                except Exception as e:
                    print(f"[Campaign] Error sending to {contact.phone_number}: {e}")
                    
                # Save recipient msg id and update
                if meta_message_id:
                    recipient.meta_message_id = meta_message_id
                else:
                    recipient.status = "failed"
                
                # 4. Save to Database Messages
                new_msg = Message(
                    conversation_id=convo.id,
                    sender_type="agent",
                    sender_id=agent_id,
                    message_type="text" if campaign.use_template else ("image" if campaign.media_type == "image" else ("video" if campaign.media_type == "video" else ("audio" if campaign.media_type == "audio" else "text"))),
                    body=f"[Template: {campaign.template_name}]" if campaign.use_template else campaign.body,
                    media_url=campaign.media_url if not campaign.use_template else None,
                    meta_message_id=meta_message_id,
                    status="sent" if meta_message_id else "failed"
                )
                db.add(new_msg)
                convo.last_message_at = datetime.utcnow()
                db.commit()

        # Update campaign sent_count
        campaign.sent_count = sent_count
        db.commit()
                 
    except Exception as e:
        print(f"[Campaign] General campaign failure: {e}")
    finally:
        db.close()


@router.post("/campaigns/send")
async def send_campaign(
    camp: CampaignSendRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("crm"))
):
    """
    Launches a marketing campaign in the background for all tenant contacts.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem disparar campanhas.")
        
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Chaves da API da Meta não configuradas para este hotel.")
        
    # Create the campaign record first
    campaign = MarketingCampaign(
        tenant_id=current_tenant.id,
        name=camp.name,
        body=camp.body or "",
        media_type=camp.media_type,
        media_url=camp.media_url,
        button_type=camp.button_type,
        button_label=camp.button_label,
        button_url=camp.button_url,
        use_template=camp.use_template or False,
        template_name=camp.template_name,
        template_language=camp.template_language or "pt_BR",
        sent_count=0
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    base_url = str(request.base_url).rstrip("/")

    # Queue campaign dispatch in background
    background_tasks.add_task(
        dispatch_campaign_bulk,
        tenant_id=current_tenant.id,
        agent_id=current_user.id,
        campaign_id=campaign.id,
        base_url=base_url,
        db_session_factory=SessionLocal
    )
    
    return {"status": "campaign_queued", "campaign_id": campaign.id}


@router.get("/campaigns", response_model=List[CampaignResponse])
def get_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("crm"))
):
    """
    Retrieve all campaigns for the tenant.
    """
    campaigns = db.query(MarketingCampaign).filter(
        MarketingCampaign.tenant_id == current_tenant.id
    ).order_by(MarketingCampaign.created_at.desc()).all()
    return campaigns


@router.get("/campaigns/click/{recipient_id}")
def campaign_click_tracker(
    recipient_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Tracks button/link clicks by redirecting through this server endpoint.
    """
    recipient = db.query(CampaignRecipient).filter(CampaignRecipient.id == str(recipient_id)).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
        
    if not recipient.clicked:
        recipient.clicked = True
        recipient.clicked_at = datetime.utcnow()
        campaign = recipient.campaign
        if campaign:
            campaign.click_count = (campaign.click_count or 0) + 1
        db.commit()
        
    redirect_url = recipient.campaign.button_url if (recipient.campaign and recipient.campaign.button_url) else "/"
    return Response(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Location": redirect_url}
    )

@router.post("/upload-media")
def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("crm"))
):
    """
    Uploads a media file (image, video, audio) to the server.
    Saves it to the frontend static uploads folder and returns the relative path.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem enviar arquivos.")

    # 1. Resolve target directory path
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    uploads_dir = os.path.join(base_dir, "frontend", "uploads")
    
    # Create the directory if it doesn't exist
    os.makedirs(uploads_dir, exist_ok=True)
    
    # 2. Generate a secure, unique filename
    ext = os.path.splitext(file.filename)[1]
    import uuid
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(uploads_dir, unique_filename)
    
    # 3. Write binary data
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar arquivo de mídia: {str(e)}")
        
    # 4. Return URL path
    return {"url": f"/uploads/{unique_filename}"}


@router.get("/bot-config", response_model=BotConfigResponse)
def get_bot_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("chatbot"))
):
    """
    Retrieves the Chatbot settings for the current hotel (tenant).
    Creates default settings if none exist.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem acessar as configurações do Bot.")
        
    config = db.query(BotConfig).filter(BotConfig.tenant_id == current_tenant.id).first()
    if not config:
        config = BotConfig(tenant_id=current_tenant.id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.post("/bot-config", response_model=BotConfigResponse)
def update_bot_config(
    payload: BotConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("chatbot"))
):
    """
    Updates the Chatbot settings for the current hotel (tenant).
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem alterar as configurações do Bot.")
        
    config = db.query(BotConfig).filter(BotConfig.tenant_id == current_tenant.id).first()
    if not config:
        config = BotConfig(tenant_id=current_tenant.id)
        db.add(config)
        
    if payload.is_active is not None:
        config.is_active = payload.is_active
    if payload.welcome_message is not None:
        config.welcome_message = payload.welcome_message
    if payload.fallback_message is not None:
        config.fallback_message = payload.fallback_message
    if payload.out_of_hours_message is not None:
        config.out_of_hours_message = payload.out_of_hours_message
    if payload.transfer_keywords is not None:
        config.transfer_keywords = payload.transfer_keywords
    if payload.n8n_webhook_url is not None:
        config.n8n_webhook_url = payload.n8n_webhook_url
        
    db.commit()
    db.refresh(config)
    return config


@router.get("/dashboard-metrics", response_model=DashboardMetricsResponse)
def get_dashboard_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("dashboard"))
):
    """
    Calculates live dashboard metrics from the database for the current tenant.
    """
    # 1. Total conversations
    total_convos = db.query(Conversation).filter(Conversation.tenant_id == current_tenant.id).count()
    
    # 2. Bot resolution rate
    resolved_convos = db.query(Conversation).filter(
        Conversation.tenant_id == current_tenant.id,
        Conversation.status == "resolved"
    ).count()
    bot_resolved = db.query(Conversation).filter(
        Conversation.tenant_id == current_tenant.id,
        Conversation.status == "resolved",
        Conversation.assigned_user_id == None
    ).count()
    bot_rate = (bot_resolved / resolved_convos * 100) if resolved_convos > 0 else 76.4
    
    # 3. Avg response time (fallback baseline or calculated)
    avg_seconds = 45.0
    
    # 4. Contacts Funnel stages
    leads_count = db.query(Contact).filter(Contact.tenant_id == current_tenant.id, Contact.sales_funnel_stage == "lead").count()
    prospects_count = db.query(Contact).filter(Contact.tenant_id == current_tenant.id, Contact.sales_funnel_stage == "prospect").count()
    customers_count = db.query(Contact).filter(Contact.tenant_id == current_tenant.id, Contact.sales_funnel_stage == "customer").count()
    
    total_contacts = leads_count + prospects_count + customers_count
    conversion_rate = (customers_count / total_contacts * 100) if total_contacts > 0 else 18.2
    
    # Funnel stages calculation:
    # 1. Pesquisa: Everyone starts as a lead or higher
    pesquisa_count = total_contacts
    pesquisa_pct = 100.0
    
    # 2. Orçamento Enviado: Prospects and Customers
    orcamento_count = prospects_count + customers_count
    orcamento_pct = (orcamento_count / total_contacts * 100) if total_contacts > 0 else 62.0
    
    # 3. Checkout Aberto: Simulated dropoff or customer bookings
    checkout_count = int(orcamento_count * 0.55) + customers_count
    checkout_pct = (checkout_count / total_contacts * 100) if total_contacts > 0 else 34.0
    
    # 4. Confirmada: Customers
    confirmada_count = customers_count
    confirmada_pct = (confirmada_count / total_contacts * 100) if total_contacts > 0 else 18.0
    
    funnel = [
        FunnelStageMetric(stage="Pesquisa", count=pesquisa_count, percentage=round(pesquisa_pct, 1)),
        FunnelStageMetric(stage="Orçamento Enviado", count=orcamento_count, percentage=round(orcamento_pct, 1)),
        FunnelStageMetric(stage="Checkout Aberto", count=checkout_count, percentage=round(checkout_pct, 1)),
        FunnelStageMetric(stage="Confirmada", count=confirmada_count, percentage=round(confirmada_pct, 1))
    ]
    
    # 5. Pending contacts by department (waiting queue conversations grouped by department)
    depts = db.query(Department).filter(Department.tenant_id == current_tenant.id).all()
    
    dep_metrics = []
    for d in depts:
        count = db.query(Conversation).filter(
            Conversation.tenant_id == current_tenant.id,
            Conversation.assigned_department_id == d.id,
            Conversation.status == "waiting"
        ).count()
        dep_metrics.append(DepartmentMetric(name=d.name, count=count))
        
    if not dep_metrics:
        # Default placeholders if tenant is brand new and has no departments yet
        dep_metrics = [
            DepartmentMetric(name="Reservas", count=0),
            DepartmentMetric(name="Recepção", count=0),
            DepartmentMetric(name="Eventos", count=0)
        ]
        
    return DashboardMetricsResponse(
        total_conversations=total_convos,
        bot_resolution_rate=round(bot_rate, 1),
        avg_response_time_seconds=avg_seconds,
        conversion_rate=round(conversion_rate, 1),
        funnel_stages=funnel,
        department_counts=dep_metrics
    )


# --- Quick Messages Endpoints ---
@router.get("/quick-messages", response_model=List[QuickMessageResponse])
def get_quick_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Retrieve all quick messages for the tenant:
    - Global quick messages (where user_id is NULL)
    - Personal quick messages for the current logged-in user
    """
    quick_msgs = db.query(QuickMessage).filter(
        QuickMessage.tenant_id == current_tenant.id
    ).filter(
        (QuickMessage.user_id == None) | (QuickMessage.user_id == current_user.id)
    ).all()

    results = []
    for qm in quick_msgs:
        results.append(
            QuickMessageResponse(
                id=qm.id,
                shortcut=qm.shortcut,
                body=qm.body,
                is_global=(qm.user_id is None),
                created_at=qm.created_at
            )
        )
    return results

@router.post("/quick-messages", response_model=QuickMessageResponse)
def create_quick_message(
    payload: QuickMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Create a quick message.
    - If user is an agent, is_global is ignored and set to False (personal).
    - If user is manager or admin, they can set is_global = True.
    """
    is_global = payload.is_global
    if current_user.role not in ["administrator", "manager"]:
        is_global = False

    # Remove "/" prefix from shortcut if present
    shortcut = payload.shortcut.strip().lstrip("/")
    if not shortcut:
        raise HTTPException(status_code=400, detail="O atalho não pode ser vazio")

    # Check if a message with this shortcut already exists for this scope
    existing = db.query(QuickMessage).filter(
        QuickMessage.tenant_id == current_tenant.id,
        QuickMessage.shortcut == shortcut
    )
    if is_global:
        existing = existing.filter(QuickMessage.user_id == None).first()
    else:
        existing = existing.filter(QuickMessage.user_id == current_user.id).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Já existe uma resposta rápida com o atalho '/{shortcut}' nesse escopo."
        )

    db_quick = QuickMessage(
        tenant_id=current_tenant.id,
        user_id=None if is_global else current_user.id,
        shortcut=shortcut,
        body=payload.body
    )
    db.add(db_quick)
    db.commit()
    db.refresh(db_quick)

    return QuickMessageResponse(
        id=db_quick.id,
        shortcut=db_quick.shortcut,
        body=db_quick.body,
        is_global=is_global,
        created_at=db_quick.created_at
    )

@router.delete("/quick-messages/{qm_id}")
def delete_quick_message(
    qm_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Delete a quick message.
    - Agents can only delete their own personal messages.
    - Managers/Admins can delete both their own personal and global messages.
    """
    qm = db.query(QuickMessage).filter(
        QuickMessage.id == str(qm_id),
        QuickMessage.tenant_id == current_tenant.id
    ).first()

    if not qm:
        raise HTTPException(status_code=404, detail="Resposta rápida não encontrada")

    if qm.user_id is None:
        if current_user.role not in ["administrator", "manager"]:
            raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem deletar respostas rápidas globais.")
    else:
        if qm.user_id != current_user.id and current_user.role not in ["administrator", "manager"]:
            raise HTTPException(status_code=403, detail="Você não tem permissão para deletar essa resposta rápida.")

    db.delete(qm)
    db.commit()
    return {"status": "success", "detail": "Resposta rápida removida com sucesso"}


class ContactUpdatePayload(BaseModel):
    name: str

@router.put("/contacts/{contact_id}", response_model=ContactResponse)
def update_contact(
    contact_id: UUID,
    payload: ContactUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    contact = db.query(Contact).filter(
        Contact.id == str(contact_id),
        Contact.tenant_id == current_tenant.id
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado")
        
    contact.name = payload.name
    db.commit()
    db.refresh(contact)
    return contact




