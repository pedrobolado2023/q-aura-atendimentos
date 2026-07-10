import os
import shutil
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Response, BackgroundTasks, UploadFile, File
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime
from typing import List, Optional
from app.database import get_db, SessionLocal
from app.models import User, Tenant, Conversation, Message, Contact, MetaCredential, BotConfig
from app.schemas import ConversationResponse, MessageResponse, BulkContactUploadRequest, CampaignSendRequest, BotConfigResponse, BotConfigUpdate
from app.auth import get_current_user, get_current_tenant
from app.config import settings

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    query = db.query(Conversation).filter(Conversation.tenant_id == current_tenant.id)
    if status_filter:
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
    current_tenant: Tenant = Depends(get_current_tenant)
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
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Imports contacts in bulk for the current tenant.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem importar contatos.")

    imported_count = 0
    for c in payload.contacts:
        # Limpa o telefone para conter apenas dígitos
        phone = "".join(filter(str.isdigit, c.phone_number))
        if not phone:
            continue
            
        contact = db.query(Contact).filter(
            Contact.tenant_id == current_tenant.id,
            Contact.phone_number == phone
        ).first()
        
        if contact:
            contact.name = c.name
        else:
            contact = Contact(
                tenant_id=current_tenant.id,
                phone_number=phone,
                name=c.name,
                sales_funnel_stage="lead",
                loyalty_level="none",
                language="pt-BR"
            )
            db.add(contact)
        imported_count += 1
        
    db.commit()
    return {"status": "success", "imported": imported_count}


async def dispatch_campaign_bulk(
    tenant_id: UUID,
    agent_id: UUID,
    camp: CampaignSendRequest,
    db_session_factory
):
    """
    Background worker that iterates through all tenant contacts and sends Meta WhatsApp campaigns.
    """
    db = db_session_factory()
    try:
        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
        if not creds:
            print(f"[Campaign] Credentials not found for tenant {tenant_id}")
            return
            
        contacts = db.query(Contact).filter(Contact.tenant_id == tenant_id).all()
        if not contacts:
            print(f"[Campaign] No contacts in database for tenant {tenant_id}")
            return
            
        meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {creds.permanent_access_token}",
            "Content-Type": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            for contact in contacts:
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
                
                # Check button configuration
                if camp.button_type == "cta_url" and camp.button_label and camp.button_url:
                    payload["type"] = "interactive"
                    payload["interactive"] = {
                        "type": "cta_url",
                        "body": {
                            "text": camp.body
                        },
                        "action": {
                            "name": "cta_url",
                            "parameters": {
                                "display_text": camp.button_label,
                                "url": camp.button_url
                            }
                        }
                    }
                    if camp.media_type in ["image", "video"] and camp.media_url:
                        payload["interactive"]["header"] = {
                            "type": camp.media_type,
                            camp.media_type: {
                                "link": camp.media_url
                            }
                        }
                        
                elif camp.button_type == "quick_reply" and camp.button_label:
                    payload["type"] = "interactive"
                    payload["interactive"] = {
                        "type": "button",
                        "body": {
                            "text": camp.body
                        },
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": "campaign_reply_1",
                                        "title": camp.button_label
                                    }
                                }
                            ]
                        }
                    }
                    if camp.media_type in ["image", "video"] and camp.media_url:
                        payload["interactive"]["header"] = {
                            "type": camp.media_type,
                            camp.media_type: {
                                "link": camp.media_url
                            }
                        }
                else:
                    # Media without buttons or plain text
                    if camp.media_type == "image" and camp.media_url:
                        payload["type"] = "image"
                        payload["image"] = {
                            "link": camp.media_url,
                            "caption": camp.body
                        }
                    elif camp.media_type == "video" and camp.media_url:
                        payload["type"] = "video"
                        payload["video"] = {
                            "link": camp.media_url,
                            "caption": camp.body
                        }
                    elif camp.media_type == "audio" and camp.media_url:
                        payload["type"] = "audio"
                        payload["audio"] = {
                            "link": camp.media_url
                        }
                    else:
                        payload["type"] = "text"
                        payload["text"] = {
                            "preview_url": False,
                            "body": camp.body
                        }
                        
                # 3. Request sending to Meta
                meta_message_id = None
                try:
                    response = await client.post(meta_url, headers=headers, json=payload)
                    if response.status_code == 200:
                        res_data = response.json()
                        meta_message_id = res_data.get("messages", [{}])[0].get("id")
                except Exception as e:
                    print(f"[Campaign] Error sending to {contact.phone_number}: {e}")
                    
                # 4. Save to Database
                new_msg = Message(
                    conversation_id=convo.id,
                    sender_type="agent",
                    sender_id=agent_id,
                    message_type="image" if camp.media_type == "image" else ("video" if camp.media_type == "video" else ("audio" if camp.media_type == "audio" else "text")),
                    body=camp.body,
                    media_url=camp.media_url,
                    meta_message_id=meta_message_id,
                    status="sent" if meta_message_id else "failed"
                )
                db.add(new_msg)
                convo.last_message_at = datetime.utcnow()
                db.commit()
                
    except Exception as e:
        print(f"[Campaign] General campaign failure: {e}")
    finally:
        db.close()


@router.post("/campaigns/send")
async def send_campaign(
    camp: CampaignSendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Launches a marketing campaign in the background for all tenant contacts.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem disparar campanhas.")
        
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Chaves da API da Meta não configuradas para este hotel.")
        
    # Queue campaign dispatch in background
    background_tasks.add_task(
        dispatch_campaign_bulk,
        tenant_id=current_tenant.id,
        agent_id=current_user.id,
        camp=camp,
        db_session_factory=SessionLocal
    )
    
    return {"status": "campaign_queued"}

@router.post("/upload-media")
def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
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
    current_tenant: Tenant = Depends(get_current_tenant)
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
    current_tenant: Tenant = Depends(get_current_tenant)
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
        
    db.commit()
    db.refresh(config)
    return config



