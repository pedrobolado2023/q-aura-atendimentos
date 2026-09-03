import os
import shutil
import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Response, BackgroundTasks, UploadFile, File, Request, Form
from pydantic import BaseModel
from jose import jwt, JWTError
from sqlalchemy.orm import Session, joinedload
from uuid import UUID
from datetime import datetime, timezone
from typing import List, Optional, Union, Any
from sqlalchemy import func
from app.database import get_db, SessionLocal, engine
from app.models import User, Tenant, Conversation, Message, Contact, MetaCredential, BotConfig, Department, QuickMessage, MarketingCampaign, CampaignRecipient, MessageTemplate, Tag
from app.schemas import ConversationResponse, MessageResponse, BulkContactUploadRequest, CampaignSendRequest, CampaignResponse, BotConfigResponse, BotConfigUpdate, DashboardMetricsResponse, DepartmentMetric, FunnelStageMetric, AgentPerformanceMetric, DailyTrafficMetric, StartConversationRequest, QuickMessageCreate, QuickMessageResponse, ContactResponse, MessageTemplateCreate, MessageTemplateResponse, TagCreate, TagResponse, KanbanBoardResponse, KanbanColumn, KanbanCard, KanbanStageUpdateRequest, GlobalSearchResult, ResolveCSATRequest
from app.auth import get_current_user, get_current_tenant, ModuleRequired
from app.config import settings

def format_brazilian_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = "".join(filter(str.isdigit, str(phone)))
    if not digits:
        return ""
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

@router.get("/conversations", response_model=List[ConversationResponse])
def get_conversations(
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    try:
        from sqlalchemy.orm import joinedload
        tenant_id_str = str(current_tenant.id)
        user_id_str = str(current_user.id)

        query = db.query(Conversation).options(
            joinedload(Conversation.contact),
            joinedload(Conversation.tags)
        ).filter(Conversation.tenant_id == tenant_id_str)
        if status_filter:
            if status_filter == "waiting":
                query = query.filter(Conversation.status.in_(["waiting", "bot"]))
            else:
                query = query.filter(Conversation.status == status_filter)
            
        # Para atendentes normais (agentes), filtra apenas as conversas atribuídas a eles na aba Minhas
        if status_filter == "active" and current_user.role not in ["administrator", "manager"]:
            query = query.filter(Conversation.assigned_user_id == user_id_str)
            
        # Aplica a ordenação ANTES do limite para atender às regras estritas do SQLAlchemy
        query = query.order_by(Conversation.last_message_at.desc())

        # Limite inteligente para evitar travamento com milhares de conversas resolvidas antigas
        if status_filter == "resolved":
            query = query.limit(150)
        else:
            query = query.limit(300)

        convos = query.all()

        # Busca em LOTE (1 única query SQL direta, 100% compatível com PostgreSQL UUID e SQLite)
        last_msg_map = {}
        last_contact_msg_map = {}

        if convos:
            convo_ids = [str(c.id) for c in convos]
            
            # Busca apenas as mensagens mais recentes das conversas listadas (máximo desempenho SQL)
            recent_messages = (
                db.query(Message)
                .filter(Message.conversation_id.in_(convo_ids))
                .order_by(Message.created_at.desc())
                .limit(600)
                .all()
            )

            for m in recent_messages:
                cid_key = str(m.conversation_id)
                # Guarda a mensagem mais recente da conversa
                if cid_key not in last_msg_map:
                    last_msg_map[cid_key] = m
                # Guarda a mensagem mais recente enviada pelo contato (para a janela de 24h)
                if m.sender_type == "contact" and cid_key not in last_contact_msg_map:
                    last_contact_msg_map[cid_key] = m

        # Enriquece cada conversa instantaneamente a partir do mapa em memória (O(1))
        result = []
        now = datetime.now(timezone.utc)
        for c in convos:
            try:
                # Sanitização de nulos em registros legados
                if c.unread is None: c.unread = False
                if c.unread_count is None: c.unread_count = 0
                if c.is_flagged is None: c.is_flagged = False
                if c.flag_type is None: c.flag_type = "none"

                d = ConversationResponse.from_orm(c)
                lm = last_msg_map.get(str(c.id))
                if lm:
                    d.last_message_body = (lm.body or "")[:80]
                    d.last_message_sender_type = lm.sender_type
                    
                last_contact_msg = last_contact_msg_map.get(str(c.id))
                if last_contact_msg and last_contact_msg.created_at:
                    created_at_tz = last_contact_msg.created_at.replace(tzinfo=timezone.utc) if last_contact_msg.created_at.tzinfo is None else last_contact_msg.created_at
                    diff_hours = (now - created_at_tz).total_seconds() / 3600
                    d.has_active_window = diff_hours <= 24.0
                else:
                    d.has_active_window = False
                    
                result.append(d)
            except Exception as single_err:
                print(f"[Inbox Serialization Warning] Failed to parse conversation {c.id}: {single_err}")
                continue
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Inbox Error] get_conversations failed: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar conversas: {str(e)}")

@router.get("/conversations/{conversation_id}/detail", response_model=ConversationResponse)
def get_conversation_detail(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Retorna detalhes completos de uma conversa incluindo contato aninhado."""
    from sqlalchemy.orm import joinedload
    convo_id_str = str(conversation_id)
    tenant_id_str = str(current_tenant.id)
    convo = (
        db.query(Conversation)
        .options(
            joinedload(Conversation.contact),
            joinedload(Conversation.tags)
        )
        .filter(
            (Conversation.id == conversation_id) | (Conversation.id == convo_id_str),
            (Conversation.tenant_id == current_tenant.id) | (Conversation.tenant_id == tenant_id_str)
        )
        .first()
    )
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    d = ConversationResponse.from_orm(convo)
    # Preview da última mensagem
    lm = (
        db.query(Message)
        .filter((Message.conversation_id == conversation_id) | (Message.conversation_id == convo_id_str))
        .order_by(Message.created_at.desc())
        .first()
    )
    if lm:
        d.last_message_body = (lm.body or "")[:80]
        d.last_message_sender_type = lm.sender_type

    # Calcula janela de 24 horas
    now = datetime.now(timezone.utc)
    last_contact_msg = (
        db.query(Message)
        .filter((Message.conversation_id == conversation_id) | (Message.conversation_id == convo_id_str), Message.sender_type == "contact")
        .order_by(Message.created_at.desc())
        .first()
    )
    if last_contact_msg and last_contact_msg.created_at:
        created_at_tz = last_contact_msg.created_at.replace(tzinfo=timezone.utc) if last_contact_msg.created_at.tzinfo is None else last_contact_msg.created_at
        diff_hours = (now - created_at_tz).total_seconds() / 3600
        d.has_active_window = diff_hours <= 24.0
    else:
        d.has_active_window = False

    return d

@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
def get_messages(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    convo_id_str = str(conversation_id)
    tenant_id_str = str(current_tenant.id)

    # Verify conversation belongs to tenant
    convo = db.query(Conversation).filter(
        (Conversation.id == conversation_id) | (Conversation.id == convo_id_str),
        (Conversation.tenant_id == current_tenant.id) | (Conversation.tenant_id == tenant_id_str)
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    # Mark conversation as read
    # Retorna no máximo as 150 mensagens mais recentes para resposta ultra-rápida (sub-5ms)
    recent_messages = db.query(Message).filter(
        (Message.conversation_id == conversation_id) | (Message.conversation_id == convo_id_str)
    ).order_by(Message.created_at.desc()).limit(150).all()
    return list(reversed(recent_messages))

class SendMessagePayload(BaseModel):
    conversation_id: Union[UUID, str, Any]
    body: Optional[str] = ""
    template_name: Optional[str] = None
    template_language: Optional[str] = "pt_BR"
    internal_note: Optional[bool] = False

@router.post("/send-message", response_model=MessageResponse)
async def send_message(
    payload: SendMessagePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    conversation_id = payload.conversation_id
    body = (payload.body or "").strip()
    is_template = bool(payload.template_name and payload.template_name.strip())
    is_internal = bool(payload.internal_note)
    
    if not body and not is_template:
        raise HTTPException(status_code=400, detail="O conteúdo da mensagem ou o nome do modelo (template) deve ser informado.")

    # 1. Verify conversation
    convo = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        convo = db.query(Conversation).filter(
            Conversation.id == str(conversation_id)
        ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")

    # 1.1 Se for Nota Interna Privada, grava direto no DB e envia WebSocket apenas para a equipe
    if is_internal:
        try:
            db.execute(text("ALTER TABLE qa_messages ADD COLUMN IF NOT EXISTS internal_note BOOLEAN DEFAULT FALSE"))
            db.commit()
        except Exception:
            db.rollback()

        try:
            note_msg = Message(
                conversation_id=convo.id,
                sender_type="agent",
                sender_id=current_user.id,
                message_type="text",
                body=body,
                internal_note=True,
                status="sent"
            )
            db.add(note_msg)
            convo.last_message_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(note_msg)
        except Exception as db_err:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Erro ao salvar nota interna no banco: {str(db_err)}")

        try:
            from app.services.websocket_manager import manager
            await manager.broadcast_to_tenant(str(current_tenant.id), {
                "type": "NEW_MESSAGE",
                "conversation_id": str(convo.id),
                "message": {
                    "id": str(note_msg.id),
                    "conversation_id": str(note_msg.conversation_id),
                    "sender_type": "agent",
                    "sender_id": str(current_user.id),
                    "sender_name": current_user.name or current_user.email,
                    "message_type": "text",
                    "body": body,
                    "internal_note": True,
                    "created_at": note_msg.created_at.isoformat() if note_msg.created_at else datetime.now(timezone.utc).isoformat()
                }
            })
        except Exception as ws_err:
            print(f"[WebSocket] Notice broadcasting internal note: {ws_err}")

        return note_msg
        
    # Validar saldo / limite antes de enviar nova mensagem ativa
    from app.services.charge_service import can_initiate_conversation, charge_tenant_conversation
    
    # Verifica se a janela de 24h já expirou (se sim, cobramos por iniciar uma nova conversação)
    last_contact_msg = (
        db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.sender_type == "contact")
        .order_by(Message.created_at.desc())
        .first()
    )
    is_new_session = True
    if last_contact_msg and last_contact_msg.created_at:
        created_at_tz = last_contact_msg.created_at.replace(tzinfo=timezone.utc) if last_contact_msg.created_at.tzinfo is None else last_contact_msg.created_at
        diff_hours = (datetime.now(timezone.utc) - created_at_tz).total_seconds() / 3600
        if diff_hours <= 24.0:
            is_new_session = False

    if is_new_session and not can_initiate_conversation(db, current_tenant.id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, 
            detail="Saldo insuficiente ou limite pós-pago atingido. Efetue uma recarga para continuar enviando mensagens ativas."
        )

    contact = db.query(Contact).filter(Contact.id == convo.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # 2. Get Meta API credentials for tenant
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Meta credentials not configured for this tenant")

    recipient_phone = format_brazilian_phone(contact.phone_number)

    # 3. Post to Meta API (WhatsApp Cloud API)
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}",
        "Content-Type": "application/json"
    }

    if is_template:
        target_template = payload.template_name.strip()
        template_lang = payload.template_language or "pt_BR"
        meta_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "template",
            "template": {
                "name": target_template,
                "language": {"code": template_lang}
            }
        }
        
        # Se o template tiver variáveis no corpo, injeta o nome do cliente como parâmetro
        try:
            tpl_db = db.query(MessageTemplate).filter(
                MessageTemplate.tenant_id == str(current_tenant.id),
                MessageTemplate.name == target_template
            ).first()
            if tpl_db and tpl_db.body_text and "{{" in tpl_db.body_text:
                contact_name = (contact.name or "Cliente").strip()
                meta_payload["template"]["components"] = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": contact_name}
                        ]
                    }
                ]
        except Exception:
            pass

        msg_body_record = tpl_db.body_text if (tpl_db and tpl_db.body_text) else f"[Template: {target_template}]"
        formatted_body = msg_body_record
        msg_type_record = "template"
    else:
        # Prepend agent's name in WhatsApp bold format
        formatted_body = f"*Atendente {current_user.name}:* {body}"
        meta_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": formatted_body
            }
        }
        msg_body_record = formatted_body
        msg_type_record = "text"

    meta_message_id = None
    meta_error_detail = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(meta_url, headers=headers, json=meta_payload, timeout=12.0)
            if response.status_code == 200:
                res_data = response.json()
                meta_message_id = res_data.get("messages", [{}])[0].get("id")
            else:
                try:
                    err_data = response.json()
                    err_obj = err_data.get("error", {})
                    meta_error_detail = err_obj.get("message") or f"Erro Meta HTTP {response.status_code}"
                    err_code = err_obj.get("code")
                    if err_code == 131047 or "24 hours" in str(meta_error_detail).lower():
                        meta_error_detail = "A janela de 24h para envio de mensagens gratuitas expirou. Envie um Modelo (Template) aprovado na Meta."
                except Exception:
                    meta_error_detail = f"Erro Meta API HTTP {response.status_code}: {response.text[:200]}"
        except Exception as e:
            meta_error_detail = f"Erro de conexão com a API Meta WhatsApp: {str(e)}"

    if not meta_message_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meta_error_detail or "Falha ao enviar mensagem via WhatsApp API Meta."
        )

    # 4. Save to Database
    msg = Message(
        conversation_id=conversation_id,
        sender_type="agent",
        sender_id=current_user.id,
        message_type=msg_type_record,
        body=msg_body_record,
        meta_message_id=meta_message_id,
        status="sent"
    )
    db.add(msg)
    
    # Update last message timestamp & assign human agent
    old_status = convo.status
    now_utc = datetime.now(timezone.utc)
    convo.last_message_at = now_utc

    if old_status in ["waiting", "bot"]:
        user_disp_name = current_user.name or current_user.email
        sys_msg = Message(
            conversation_id=convo.id,
            sender_type="system",
            sender_id=current_user.id,
            message_type="system",
            body=f"👤 {user_disp_name} assumiu a conversa e iniciou o atendimento humano.",
            internal_note=True
        )
        db.add(sys_msg)

    convo.status = "active"
    if not convo.assigned_user_id:
        convo.assigned_user_id = current_user.id
    db.commit()
    db.refresh(msg)

    # Executa o débito/tarifação se for nova sessão
    if is_new_session and meta_message_id:
        charge_tenant_conversation(
            db, 
            tenant_id=str(current_tenant.id), 
            conversation_id=str(convo.id), 
            category="service", # Mensagem enviada pelo atendente é cobrada como Service
            custom_description=f"Conversa de Serviço iniciada por atendente com {contact.phone_number}"
        )

    # Broadcast via WebSocket Manager
    from app.services.websocket_manager import manager
    broadcast_data = {
        "type": "new_message",
        "id": msg.id,
        "conversation_id": convo.id,
        "sender_type": "agent",
        "body": formatted_body,
        "message_type": "text",
        "media_url": None,
        "unread": False,
        "unread_count": convo.unread_count,
        "contact_name": contact.name or contact.phone_number,
        "contact_phone": contact.phone_number,
        "contact_avatar": contact.avatar_url,
        "preview": formatted_body[:60] if formatted_body else "",
        "last_message_at": convo.last_message_at.isoformat() if convo.last_message_at else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None
    }
    await manager.broadcast_to_tenant(str(current_tenant.id), broadcast_data)

    return msg


@router.post("/send-media", response_model=MessageResponse)
async def send_media(
    conversation_id: UUID = Form(...),
    caption: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    # 1. Verify conversation
    convo = db.query(Conversation).filter(
        Conversation.id == str(conversation_id),
        Conversation.tenant_id == str(current_tenant.id)
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")

    # Validar saldo / limite antes de enviar mídia em nova sessão
    from app.services.charge_service import can_initiate_conversation, charge_tenant_conversation
    import re
    from uuid import uuid4

    last_contact_msg = (
        db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.sender_type == "contact")
        .order_by(Message.created_at.desc())
        .first()
    )
    is_new_session = True
    if last_contact_msg and last_contact_msg.created_at:
        created_at_tz = last_contact_msg.created_at.replace(tzinfo=timezone.utc) if last_contact_msg.created_at.tzinfo is None else last_contact_msg.created_at
        diff_hours = (datetime.now(timezone.utc) - created_at_tz).total_seconds() / 3600
        if diff_hours <= 24.0:
            is_new_session = False

    if is_new_session and not can_initiate_conversation(db, current_tenant.id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, 
            detail="Saldo insuficiente ou limite pós-pago atingido. Efetue uma recarga para continuar enviando mensagens ativas."
        )

    contact = db.query(Contact).filter(Contact.id == convo.contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")

    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Credenciais Meta não configuradas para este tenant.")

    # 2. Process file contents & save locally
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="O arquivo enviado está vazio.")

    mime_type = file.content_type or "application/octet-stream"
    original_filename = file.filename or "arquivo"
    safe_filename = re.sub(r'[^a-zA-Z0-9_\.\-]', '_', original_filename)
    unique_filename = f"{uuid4().hex}_{safe_filename}"

    if mime_type.startswith("image/"):
        wa_media_type = "image"
    elif mime_type.startswith("video/"):
        wa_media_type = "video"
    elif mime_type.startswith("audio/"):
        wa_media_type = "audio"
    else:
        wa_media_type = "document"

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    uploads_folder = os.path.join(base_dir, "uploads", str(current_tenant.id))
    os.makedirs(uploads_folder, exist_ok=True)
    file_disk_path = os.path.join(uploads_folder, unique_filename)

    with open(file_disk_path, "wb") as f:
        f.write(file_bytes)

    local_media_url = f"/uploads/{current_tenant.id}/{unique_filename}"
    recipient_phone = format_brazilian_phone(contact.phone_number)

    # 3. Upload media binary to Meta WhatsApp Cloud API
    meta_media_id = None
    meta_error_detail = None
    meta_media_upload_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/media"
    headers_auth = {"Authorization": f"Bearer {creds.permanent_access_token}"}

    # Meta Cloud API requires strict MIME types for audio
    meta_upload_mime = mime_type
    if wa_media_type == "audio":
        if "webm" in mime_type or "ogg" in mime_type:
            meta_upload_mime = "audio/ogg"
        elif "mp4" in mime_type or "m4a" in mime_type or "aac" in mime_type:
            meta_upload_mime = "audio/mp4"
        elif "mpeg" in mime_type or "mp3" in mime_type:
            meta_upload_mime = "audio/mpeg"

    async with httpx.AsyncClient() as client:
        try:
            files_payload = {
                "file": (original_filename, file_bytes, meta_upload_mime)
            }
            data_payload = {
                "messaging_product": "whatsapp",
                "type": meta_upload_mime
            }
            res_upload = await client.post(meta_media_upload_url, headers=headers_auth, data=data_payload, files=files_payload, timeout=30.0)
            if res_upload.status_code == 200:
                res_json = res_upload.json()
                meta_media_id = res_json.get("id")
            else:
                try:
                    err_data = res_upload.json()
                    meta_error_detail = err_data.get("error", {}).get("message") or f"Erro upload mídia Meta HTTP {res_upload.status_code}"
                except Exception:
                    meta_error_detail = f"Erro Meta API Media HTTP {res_upload.status_code}: {res_upload.text[:200]}"
        except Exception as e:
            meta_error_detail = f"Erro de conexão ao enviar mídia para Meta WhatsApp: {str(e)}"

    if not meta_media_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=meta_error_detail or "Falha ao enviar arquivo de mídia para a API Meta WhatsApp."
        )

    # 4. Post media message to Meta API
    formatted_caption = f"*Atendente {current_user.name}:* {caption.strip()}" if caption and caption.strip() else None
    
    meta_msg_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    meta_msg_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": wa_media_type
    }

    media_obj = {"id": meta_media_id}
    if formatted_caption and wa_media_type in ["image", "video", "document"]:
        media_obj["caption"] = formatted_caption
    if wa_media_type == "document":
        media_obj["filename"] = original_filename

    meta_msg_payload[wa_media_type] = media_obj

    meta_message_id = None
    async with httpx.AsyncClient() as client:
        try:
            res_msg = await client.post(meta_msg_url, headers=headers_auth, json=meta_msg_payload, timeout=15.0)
            if res_msg.status_code == 200:
                res_msg_data = res_msg.json()
                meta_message_id = res_msg_data.get("messages", [{}])[0].get("id")
            else:
                print(f"Meta media send message error: {res_msg.text}")
        except Exception as e:
            print(f"Error sending Meta media message: {e}")

    # 5. Save Message in Database
    display_text = formatted_caption if formatted_caption else (f"*Atendente {current_user.name}:* {original_filename}" if wa_media_type == "document" else f"*Atendente {current_user.name}:* [{wa_media_type.capitalize()}]")
    
    msg = Message(
        conversation_id=conversation_id,
        sender_type="agent",
        sender_id=current_user.id,
        message_type=wa_media_type,
        body=display_text,
        media_url=local_media_url,
        media_mime_type=mime_type,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)

    now_utc = datetime.now(timezone.utc)
    convo.last_message_at = now_utc

    if convo.status in ["waiting", "bot"]:
        user_disp_name = current_user.name or current_user.email
        sys_msg = Message(
            conversation_id=convo.id,
            sender_type="system",
            sender_id=current_user.id,
            message_type="system",
            body=f"👤 {user_disp_name} assumiu a conversa e enviou um arquivo.",
            internal_note=True
        )
        db.add(sys_msg)

    convo.status = "active"
    if not convo.assigned_user_id:
        convo.assigned_user_id = current_user.id
    db.commit()
    db.refresh(msg)

    if is_new_session and meta_message_id:
        charge_tenant_conversation(
            db, 
            tenant_id=str(current_tenant.id), 
            conversation_id=str(convo.id), 
            category="service",
            custom_description=f"Conversa de Serviço iniciada por atendente enviando mídia para {contact.phone_number}"
        )

    # 6. Broadcast via WebSocket
    from app.services.websocket_manager import manager
    broadcast_data = {
        "type": "new_message",
        "id": msg.id,
        "conversation_id": convo.id,
        "sender_type": "agent",
        "body": display_text,
        "message_type": wa_media_type,
        "media_url": local_media_url,
        "media_mime_type": mime_type,
        "unread": False,
        "unread_count": convo.unread_count,
        "contact_name": contact.name or contact.phone_number,
        "contact_phone": contact.phone_number,
        "contact_avatar": contact.avatar_url,
        "preview": f"📎 {original_filename}",
        "last_message_at": convo.last_message_at.isoformat() if convo.last_message_at else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None
    }
    await manager.broadcast_to_tenant(str(current_tenant.id), broadcast_data)

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
        "id": msg.id,
        "conversation_id": convo.id,
        "sender_type": "bot",
        "body": payload.body,
        "message_type": "text",
        "media_url": None,
        "unread": False,
        "contact_name": contact.name or contact.phone_number,
        "contact_phone": contact.phone_number,
        "contact_avatar": contact.avatar_url,
        "preview": payload.body[:60] if payload.body else "",
        "last_message_at": msg.created_at.isoformat() if msg.created_at else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None
    }
    await manager.broadcast_to_tenant(str(current_tenant.id), broadcast_data)

    return msg


@router.post("/start-conversation", response_model=MessageResponse)
async def start_conversation(
    payload: StartConversationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    tenant_id_str = str(current_tenant.id)
    cleaned_phone = format_brazilian_phone(payload.phone_number)
    if not cleaned_phone:
        raise HTTPException(status_code=400, detail="Número de telefone inválido.")

    # Validar saldo / limite antes de abrir nova conversa ativa
    from app.services.charge_service import can_initiate_conversation, charge_tenant_conversation
    if not can_initiate_conversation(db, tenant_id_str):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo insuficiente ou limite pós-pago atingido. Efetue uma recarga para continuar enviando mensagens ativas."
        )

    # 2. Get/Create contact
    contact = db.query(Contact).filter(
        Contact.phone_number == cleaned_phone,
        Contact.tenant_id == tenant_id_str
    ).first()

    if not contact:
        contact_name = payload.name if payload.name else f"Hóspede {cleaned_phone[-4:]}"
        contact = Contact(
            tenant_id=tenant_id_str,
            phone_number=cleaned_phone,
            name=contact_name,
            language="pt-BR",
            sales_funnel_stage="lead",
            is_list_contact=False
        )
        db.add(contact)
        db.flush()

    # 3. Get/Create conversation
    convo = db.query(Conversation).filter(
        Conversation.contact_id == contact.id,
        Conversation.tenant_id == tenant_id_str
    ).first()

    if not convo:
        convo = Conversation(
            tenant_id=tenant_id_str,
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
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id_str).first()
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Credenciais da Meta WhatsApp não estão configuradas para esta empresa. Configure em Configurações > Conexão WhatsApp."
        )

    # Determine se será enviado um Template ou mensagem de texto simples
    target_template = payload.template_name or (payload.body if payload.body and payload.body != "text" and not payload.body.startswith("*Atendente") else None)
    template_lang = payload.template_language or "pt_BR"

    if target_template:
        meta_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": contact.phone_number,
            "type": "template",
            "template": {
                "name": target_template,
                "language": {"code": template_lang}
            }
        }
        msg_body_record = f"[Template Enviado: {target_template}]"
        msg_type_record = "template"
    else:
        formatted_body = f"*Atendente {current_user.name}:* {payload.body or 'Olá!'}"
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
        msg_body_record = formatted_body
        msg_type_record = "text"

    # 5. Send message via Meta Cloud API
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {creds.permanent_access_token}",
        "Content-Type": "application/json"
    }

    meta_message_id = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(meta_url, headers=headers, json=meta_payload)

            # Se falhou e era um envio por template, tenta um fallback para texto simples apenas se não for erro de credenciais
            if response.status_code != 200 and target_template == "primeiro_contato":
                fallback_body = f"Olá! Sou o atendente {current_user.name}. Como posso ajudar?"
                text_payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": contact.phone_number,
                    "type": "text",
                    "text": {
                        "preview_url": False,
                        "body": fallback_body
                    }
                }
                fallback_resp = await client.post(meta_url, headers=headers, json=text_payload)
                if fallback_resp.status_code == 200:
                    response = fallback_resp
                    msg_body_record = fallback_body
                    msg_type_record = "text"

            if response.status_code == 200:
                try:
                    res_data = response.json()
                    meta_message_id = res_data.get("messages", [{}])[0].get("id")
                except Exception:
                    meta_message_id = None
            else:
                try:
                    err_data = response.json()
                    error_msg = err_data.get("error", {}).get("message", f"Erro HTTP {response.status_code} da Meta")
                except Exception:
                    error_msg = f"Erro HTTP {response.status_code} da API da Meta"
                raise HTTPException(status_code=400, detail=f"Erro Meta WhatsApp: {error_msg}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro de conexão com a API da Meta: {str(e)}")

    # 6. Save message
    msg = Message(
        conversation_id=convo.id,
        sender_type="agent",
        sender_id=current_user.id,
        message_type=msg_type_record,
        body=msg_body_record,
        meta_message_id=meta_message_id,
        status="sent" if meta_message_id else "failed"
    )
    db.add(msg)

    # Update last message timestamp
    from sqlalchemy.sql import func
    convo.last_message_at = func.now()
    db.commit()
    db.refresh(msg)

    # Executa tarifação se a mensagem foi enviada com sucesso
    if meta_message_id:
        category = "utility" if msg_type_record == "template" else "marketing"
        charge_tenant_conversation(
            db,
            tenant_id=tenant_id_str,
            conversation_id=str(convo.id),
            category=category,
            custom_description=f"Conversa de {category.capitalize()} iniciada com template ({target_template or 'texto'}) com {contact.phone_number}"
        )

    return msg



class AssignRequest(BaseModel):
    user_id: Optional[UUID] = None

@router.post("/conversations/{conversation_id}/assign", response_model=ConversationResponse)
async def assign_conversation(
    conversation_id: UUID,
    payload: Optional[AssignRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Assigns the conversation to a user (or current user) and marks it active with a system note.
    """
    convo = db.query(Conversation).filter(
        Conversation.id == str(conversation_id),
        Conversation.tenant_id == str(current_tenant.id)
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    target_user = current_user
    if payload and payload.user_id:
        u = db.query(User).filter(User.id == str(payload.user_id), User.tenant_id == str(current_tenant.id)).first()
        if u:
            target_user = u

    convo.assigned_user_id = target_user.id
    convo.status = "active"

    user_disp_name = current_user.name or current_user.email
    if target_user.id == current_user.id:
        sys_text = f"👤 {user_disp_name} assumiu o atendimento."
    else:
        target_name = target_user.name or target_user.email
        sys_text = f"🔄 {user_disp_name} transferiu o atendimento para {target_name}."

    sys_msg = Message(
        conversation_id=convo.id,
        sender_type="system",
        sender_id=current_user.id,
        message_type="system",
        body=sys_text,
        internal_note=True
    )
    db.add(sys_msg)
    db.commit()
    db.refresh(convo)

    from app.services.websocket_manager import manager
    await manager.broadcast_to_tenant(str(current_tenant.id), {
        "type": "new_message",
        "conversation_id": str(convo.id),
        "id": str(sys_msg.id),
        "sender_type": "system",
        "message_type": "system",
        "body": sys_text,
        "internal_note": True,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    return convo


@router.post("/conversations/{conversation_id}/transfer-to-bot", response_model=ConversationResponse)
async def transfer_conversation_to_bot(
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
        Conversation.tenant_id == str(current_tenant.id)
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    convo.status = "bot"
    convo.assigned_user_id = None

    user_disp_name = current_user.name or current_user.email
    sys_text = f"🤖 {user_disp_name} transferiu a conversa para o Robô Chatbot."

    sys_msg = Message(
        conversation_id=convo.id,
        sender_type="system",
        sender_id=current_user.id,
        message_type="system",
        body=sys_text,
        internal_note=True
    )
    db.add(sys_msg)
    db.commit()
    db.refresh(convo)

    from app.services.websocket_manager import manager
    await manager.broadcast_to_tenant(str(current_tenant.id), {
        "type": "new_message",
        "conversation_id": str(convo.id),
        "id": str(sys_msg.id),
        "sender_type": "system",
        "message_type": "system",
        "body": sys_text,
        "internal_note": True,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

    return convo


@router.post("/conversations/{conversation_id}/resolve", response_model=ConversationResponse)
async def resolve_conversation(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Marks the conversation as resolved, creates system note, and sends closing message.
    """
    try:
        convo = db.query(Conversation).filter(
            Conversation.id == str(conversation_id),
            Conversation.tenant_id == str(current_tenant.id)
        ).first()
        if not convo:
            raise HTTPException(status_code=404, detail="Conversation not found")
            
        convo.status = "resolved"
        convo.last_message_at = datetime.now(timezone.utc)

        user_disp_name = current_user.name or current_user.email
        sys_text = f"✅ {user_disp_name} encerrou o atendimento."

        sys_msg = Message(
            conversation_id=convo.id,
            sender_type="system",
            sender_id=current_user.id,
            message_type="system",
            body=sys_text,
            internal_note=True
        )
        db.add(sys_msg)
        
        # Send closing message to the contact
        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == str(current_tenant.id)).first()
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
            
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    response = await client.post(meta_url, headers=headers, json=meta_payload, timeout=10.0)
                    if response.status_code == 200:
                        res_data = response.json()
                        meta_message_id = res_data.get("messages", [{}])[0].get("id")
                        
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
            except Exception as e:
                print(f"[Resolve] Failed to send closing message: {e}")
                    
        db.commit()
        db.refresh(convo)

        from app.services.websocket_manager import manager
        await manager.broadcast_to_tenant(str(current_tenant.id), {
            "type": "new_message",
            "conversation_id": str(convo.id),
            "id": str(sys_msg.id),
            "sender_type": "system",
            "message_type": "system",
            "body": sys_text,
            "internal_note": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        })

        return convo
    except Exception as err:
        import traceback
        traceback.print_exc()
        print(f"[Resolve Error] {err}")
        raise HTTPException(status_code=500, detail=f"Erro ao resolver conversa: {str(err)}")

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

    tenant_id_str = str(current_tenant.id)
    try:
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
            contact_name = c.name if c.name and c.name.strip() else f"Hóspede {phone[-4:]}"
            contacts_to_process.append((phone, contact_name))

        if not contacts_to_process:
            return {"status": "success", "imported": 0}

        # Reseta a flag de lista de campanha dos contatos antigos do tenant para garantir que a campanha vai SOMENTE para a nova lista importada
        db.query(Contact).filter(Contact.tenant_id == tenant_id_str).update(
            {"is_list_contact": False}, synchronize_session=False
        )

        from app.services.webhook_processor import get_phone_variations
        all_phone_vars = []
        for p, n in contacts_to_process:
            all_phone_vars.extend(get_phone_variations(p))

        # Fetch all existing contacts for any 8/9 digit phone variation in a single query
        existing = db.query(Contact).filter(
            Contact.tenant_id == tenant_id_str,
            Contact.phone_number.in_(all_phone_vars)
        ).all()
        
        existing_map = {}
        for c in existing:
            for v in get_phone_variations(c.phone_number):
                existing_map[v] = c

        for phone, name in contacts_to_process:
            contact = existing_map.get(phone)
            if contact:
                if name and not name.startswith("Hóspede "):
                    contact.name = name
                contact.is_list_contact = True
            else:
                contact = Contact(
                    tenant_id=tenant_id_str,
                    phone_number=phone,
                    name=name,
                    sales_funnel_stage="lead",
                    loyalty_level="none",
                    language="pt-BR",
                    is_list_contact=True
                )
                db.add(contact)
                # Registra variações do novo contato no mapa local para evitar duplicidade na mesma lista
                for v in get_phone_variations(phone):
                    existing_map[v] = contact
            imported_count += 1
            
        db.commit()
        return {"status": "success", "imported": imported_count}
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"[CRM Import Error] {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao importar lista de contatos: {str(e)}")


@router.post("/contacts/delete-bulk")
def delete_contact_list(
    payload: BulkContactUploadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("crm"))
):
    """
    Deletes specified contacts and their conversations/messages in bulk.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem excluir contatos.")

    tenant_id_str = str(current_tenant.id)
    try:
        from app.services.webhook_processor import get_phone_variations
        phones_to_delete = []
        for c in payload.contacts:
            phone = format_brazilian_phone(c.phone_number)
            if phone:
                phones_to_delete.extend(get_phone_variations(phone))

        if not phones_to_delete:
            return {"status": "success", "deleted": 0}

        contacts = db.query(Contact).filter(
            Contact.tenant_id == tenant_id_str,
            Contact.phone_number.in_(phones_to_delete)
        ).all()

        deleted_count = len(contacts)
        if contacts:
            contact_ids = [c.id for c in contacts]
            convos = db.query(Conversation).filter(Conversation.contact_id.in_(contact_ids)).all()
            if convos:
                convo_ids = [cv.id for cv in convos]
                db.query(Message).filter(Message.conversation_id.in_(convo_ids)).delete(synchronize_session=False)
                db.query(Conversation).filter(Conversation.id.in_(convo_ids)).delete(synchronize_session=False)
            db.query(Contact).filter(Contact.id.in_(contact_ids)).delete(synchronize_session=False)
            db.commit()

        return {"status": "success", "deleted": deleted_count}
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"[CRM Delete Bulk Error] {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao excluir contatos: {str(e)}")


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
                        status="resolved",
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
                    if campaign.media_type in ["image", "video", "document"] and campaign.media_url:
                        payload["template"]["components"] = [
                            {
                                "type": "header",
                                "parameters": [
                                    {
                                        "type": campaign.media_type,
                                        campaign.media_type: {
                                            "link": campaign.media_url
                                        }
                                    }
                                ]
                            }
                        ]
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
                    else:
                        print(f"[Campaign] API Error sending to {contact.phone_number}: {response.status_code} - {response.text}")
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

                # Registra o débito no faturamento da empresa se enviado com sucesso
                if meta_message_id:
                    from app.services.charge_service import charge_tenant_conversation
                    charge_tenant_conversation(
                        db,
                        tenant_id=tenant_id,
                        conversation_id=str(convo.id),
                        category="marketing",
                        custom_description=f"Campanha de Marketing '{campaign.name}' enviada para {contact.phone_number}"
                    )

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
        
    from app.services.charge_service import can_initiate_conversation
    if not can_initiate_conversation(db, current_tenant.id):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Saldo insuficiente ou limite pós-pago atingido. Não é possível iniciar disparos em massa."
        )
        
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=400, detail="Chaves da API da Meta não configuradas para este hotel.")
        
    # Valida contatos alvo da lista antes de criar a campanha (Garante 100% que dispara SOMENTE para a lista selecionada)
    list_contacts_count = db.query(Contact).filter(
        Contact.tenant_id == current_tenant.id,
        Contact.is_list_contact == True
    ).count()

    if list_contacts_count == 0:
        raise HTTPException(
            status_code=400,
            detail="Nenhum contato encontrado na lista de envio. Por favor, escolha uma planilha de contatos em formato CSV na coluna da esquerda antes de disparar a campanha."
        )

    clean_template_name = camp.template_name.strip().lower() if camp.template_name else None

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
        template_name=clean_template_name,
        template_language=(camp.template_language or "pt_BR").strip(),
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
    if payload.flow_data is not None:
        config.flow_data = payload.flow_data
        
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
    from datetime import datetime, timezone, timedelta
    t_id = current_tenant.id

    # 1. Real Conversation Counts by Status
    total_convos = db.query(Conversation).filter(Conversation.tenant_id == t_id).count()
    active_convos = db.query(Conversation).filter(Conversation.tenant_id == t_id, Conversation.status == "active").count()
    waiting_convos = db.query(Conversation).filter(Conversation.tenant_id == t_id, Conversation.status == "waiting").count()
    bot_convos = db.query(Conversation).filter(Conversation.tenant_id == t_id, Conversation.status == "bot").count()
    resolved_convos = db.query(Conversation).filter(Conversation.tenant_id == t_id, Conversation.status == "resolved").count()

    # 2. Real Contacts & Messages Counts
    total_contacts = db.query(Contact).filter(Contact.tenant_id == t_id).count()
    total_messages = db.query(Message).join(Conversation, Message.conversation_id == Conversation.id).filter(Conversation.tenant_id == t_id).count()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = 0
    try:
        messages_today = db.query(Message).join(Conversation, Message.conversation_id == Conversation.id).filter(
            Conversation.tenant_id == t_id,
            Message.created_at >= today_start
        ).count()
    except Exception:
        # Fallback for naive date comparisons
        messages_today = db.query(Message).join(Conversation, Message.conversation_id == Conversation.id).filter(
            Conversation.tenant_id == t_id,
            Message.created_at >= today_start.replace(tzinfo=None)
        ).count()

    # 3. Real Bot Resolution Rate
    bot_resolved = db.query(Conversation).filter(
        Conversation.tenant_id == t_id,
        Conversation.status == "resolved",
        Conversation.assigned_user_id == None
    ).count()
    bot_rate = (bot_resolved / resolved_convos * 100.0) if resolved_convos > 0 else 0.0

    # 4. Real Contacts Funnel Stages
    leads_count = db.query(Contact).filter(Contact.tenant_id == t_id, Contact.sales_funnel_stage == "lead").count()
    prospects_count = db.query(Contact).filter(Contact.tenant_id == t_id, Contact.sales_funnel_stage == "prospect").count()
    customers_count = db.query(Contact).filter(Contact.tenant_id == t_id, Contact.sales_funnel_stage == "customer").count()

    if total_contacts > 0 and (leads_count + prospects_count + customers_count == 0):
        leads_count = total_contacts

    stage_total = max(total_contacts, 1)
    conversion_rate = (customers_count / stage_total * 100.0) if total_contacts > 0 else 0.0

    funnel = [
        FunnelStageMetric(stage="Novos Leads", count=leads_count, percentage=round(leads_count / stage_total * 100.0, 1)),
        FunnelStageMetric(stage="Em Negociação (Prospects)", count=prospects_count, percentage=round(prospects_count / stage_total * 100.0, 1)),
        FunnelStageMetric(stage="Clientes Convertidos", count=customers_count, percentage=round(conversion_rate, 1))
    ]

    # 5. Real Department Breakdown
    depts = db.query(Department).filter(Department.tenant_id == t_id).all()
    dep_metrics = []
    for d in depts:
        count = db.query(Conversation).filter(
            Conversation.tenant_id == t_id,
            Conversation.assigned_department_id == d.id
        ).count()
        dep_metrics.append(DepartmentMetric(name=d.name, count=count))

    if not dep_metrics:
        dep_metrics = [
            DepartmentMetric(name="Atendimento Geral", count=total_convos)
        ]

    # 6. Real Daily Traffic (Last 7 Days) - High Performance Single Aggregation Query
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    traffic_map = {}
    for i in range(7):
        d_str = (datetime.now(timezone.utc) - timedelta(days=6 - i)).strftime("%d/%m")
        traffic_map[d_str] = {"incoming": 0, "outgoing": 0}

    try:
        recent_msgs = db.query(
            Message.sender_type,
            Message.created_at
        ).join(Conversation, Message.conversation_id == Conversation.id).filter(
            Conversation.tenant_id == t_id,
            Message.created_at >= cutoff_date
        ).all()

        for msg in recent_msgs:
            if msg.created_at:
                d_key = msg.created_at.strftime("%d/%m")
                if d_key in traffic_map:
                    if msg.sender_type == "contact":
                        traffic_map[d_key]["incoming"] += 1
                    else:
                        traffic_map[d_key]["outgoing"] += 1
    except Exception as err:
        try:
            recent_msgs = db.query(
                Message.sender_type,
                Message.created_at
            ).join(Conversation, Message.conversation_id == Conversation.id).filter(
                Conversation.tenant_id == t_id,
                Message.created_at >= cutoff_date.replace(tzinfo=None)
            ).all()
            for msg in recent_msgs:
                if msg.created_at:
                    d_key = msg.created_at.strftime("%d/%m")
                    if d_key in traffic_map:
                        if msg.sender_type == "contact":
                            traffic_map[d_key]["incoming"] += 1
                        else:
                            traffic_map[d_key]["outgoing"] += 1
        except Exception as e2:
            print(f"[Dashboard Traffic Query Error] {e2}")

    daily_traffic = [
        DailyTrafficMetric(date=d_key, incoming_count=counts["incoming"], outgoing_count=counts["outgoing"])
        for d_key, counts in traffic_map.items()
    ]

    # 7. Real Team Performance
    team_users = db.query(User).filter(User.tenant_id == t_id).all()
    team_performance = []
    for u in team_users:
        u_str = str(u.id)
        active_cnt = db.query(Conversation).filter(
            Conversation.tenant_id == t_id,
            Conversation.assigned_user_id == u_str,
            Conversation.status == "active"
        ).count()
        resolved_cnt = db.query(Conversation).filter(
            Conversation.tenant_id == t_id,
            Conversation.assigned_user_id == u_str,
            Conversation.status == "resolved"
        ).count()
        team_performance.append(AgentPerformanceMetric(
            id=u_str,
            name=u.name or u.email,
            email=u.email,
            role="Administrador" if u.role == "administrator" else ("Supervisor" if u.role == "manager" else "Atendente"),
            active_count=active_cnt,
            resolved_count=resolved_cnt
        ))

    # 8. Avg Response Time Calculation
    avg_seconds = 0.0
    try:
        recent_convos_with_msgs = db.query(Conversation).filter(
            Conversation.tenant_id == t_id
        ).order_by(Conversation.created_at.desc()).limit(20).all()
        
        response_deltas = []
        for cv in recent_convos_with_msgs:
            first_in = db.query(Message).filter(Message.conversation_id == cv.id, Message.sender_type == "contact").order_by(Message.created_at.asc()).first()
            first_out = db.query(Message).filter(Message.conversation_id == cv.id, Message.sender_type.in_(["agent", "bot"])).order_by(Message.created_at.asc()).first()
            if first_in and first_out and first_out.created_at > first_in.created_at:
                diff = (first_out.created_at - first_in.created_at).total_seconds()
                if 0 < diff < 3600:
                    response_deltas.append(diff)
        if response_deltas:
            avg_seconds = round(sum(response_deltas) / len(response_deltas), 1)
    except Exception:
        avg_seconds = 0.0

    return DashboardMetricsResponse(
        total_conversations=total_convos,
        active_conversations=active_convos,
        waiting_conversations=waiting_convos,
        bot_conversations=bot_convos,
        resolved_conversations=resolved_convos,
        total_contacts=total_contacts,
        total_messages=total_messages,
        messages_today=messages_today,
        bot_resolution_rate=round(bot_rate, 1),
        avg_response_time_seconds=avg_seconds,
        conversion_rate=round(conversion_rate, 1),
        funnel_stages=funnel,
        department_counts=dep_metrics,
        team_performance=team_performance,
        daily_traffic=daily_traffic
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
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    sales_funnel_stage: Optional[str] = None
    loyalty_level: Optional[str] = None

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
        
    if payload.name is not None:
        contact.name = payload.name.strip()
    if payload.phone_number is not None:
        cleaned = format_brazilian_phone(payload.phone_number)
        if cleaned:
            contact.phone_number = cleaned
    if payload.email is not None:
        contact.email = payload.email.strip()
    if payload.sales_funnel_stage is not None:
        contact.sales_funnel_stage = payload.sales_funnel_stage
    if payload.loyalty_level is not None:
        contact.loyalty_level = payload.loyalty_level

    db.commit()
    db.refresh(contact)
    return contact


# ==========================================
# MESSAGE TEMPLATES ENDPOINTS (META DEVELOPER)
# ==========================================

def ensure_templates_table_exists(db: Session = None):
    try:
        from sqlalchemy import text
        from app.database import db_url
        is_pg = not db_url.startswith("sqlite")
        uuid_type = "UUID" if is_pg else "VARCHAR(36)"
        with engine.begin() as conn:
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS qa_message_templates (
                    id {uuid_type} PRIMARY KEY,
                    tenant_id {uuid_type} NOT NULL,
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
            conn.execute(text("ALTER TABLE qa_message_templates ADD COLUMN IF NOT EXISTS body_text TEXT"))
            conn.execute(text("ALTER TABLE qa_message_templates ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            conn.execute(text("ALTER TABLE qa_messages ADD COLUMN IF NOT EXISTS internal_note BOOLEAN DEFAULT FALSE"))
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_qa_msg_tpl_tenant ON qa_message_templates(tenant_id)"))
            except Exception:
                pass
    except Exception as ex:
        print(f"[Database] Notice creating qa_message_templates: {ex}")

@router.get("/templates", response_model=List[MessageTemplateResponse])
def get_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    try:
        ensure_templates_table_exists(db)
        from sqlalchemy import cast, String
        t_id_str = str(current_tenant.id)
        templates = db.query(MessageTemplate).filter(
            cast(MessageTemplate.tenant_id, String) == t_id_str
        ).order_by(MessageTemplate.created_at.desc()).all()

        # Se nao houver nenhum template cadastrado ainda, cadastrar o "primeiro_contato" padrao
        if not templates:
            try:
                default_tpl = MessageTemplate(
                    tenant_id=t_id_str,
                    name="primeiro_contato",
                    label="Primeiro Contato - Boas-Vindas",
                    language="pt_BR",
                    category="UTILITY"
                )
                db.add(default_tpl)
                db.commit()
                db.refresh(default_tpl)
                templates = [default_tpl]
            except Exception as e:
                db.rollback()
                print(f"[Templates Notice] Could not seed default template: {e}")
                templates = []

        return templates
    except Exception as e:
        print(f"[Templates Error] {e}")
        return []


@router.post("/templates", response_model=MessageTemplateResponse)
def create_template(
    payload: MessageTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    if current_user.role not in ["administrator", "manager", "superadmin"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e gestores podem cadastrar templates.")

    ensure_templates_table_exists(db)
    from sqlalchemy import cast, String
    t_id_str = str(current_tenant.id)
    clean_name = payload.name.strip().lower().replace(" ", "_")
    if not clean_name:
        raise HTTPException(status_code=400, detail="Nome do template inválido.")

    existing = db.query(MessageTemplate).filter(
        cast(MessageTemplate.tenant_id, String) == t_id_str,
        MessageTemplate.name == clean_name
    ).first()

    if existing:
        existing.label = payload.label or clean_name
        existing.language = payload.language or "pt_BR"
        existing.category = payload.category or "UTILITY"
        existing.body_text = payload.body_text
        db.commit()
        db.refresh(existing)
        return existing

    new_tpl = MessageTemplate(
        tenant_id=t_id_str,
        name=clean_name,
        label=payload.label or clean_name.replace("_", " ").title(),
        language=payload.language or "pt_BR",
        category=payload.category or "UTILITY",
        body_text=payload.body_text
    )
    db.add(new_tpl)
    db.commit()
    db.refresh(new_tpl)
    return new_tpl


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    if current_user.role not in ["administrator", "manager", "superadmin"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e gestores podem remover templates.")

    ensure_templates_table_exists(db)
    from sqlalchemy import cast, String
    t_id_str = str(current_tenant.id)
    tpl = db.query(MessageTemplate).filter(
        cast(MessageTemplate.id, String) == str(template_id),
        cast(MessageTemplate.tenant_id, String) == t_id_str
    ).first()

    if not tpl:
        raise HTTPException(status_code=404, detail="Template não encontrado.")

    db.delete(tpl)
    db.commit()
    return {"message": "Template excluído com sucesso."}


@router.post("/templates/sync-meta")
async def sync_meta_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Sincroniza automaticamente todos os modelos de templates aprovados
    diretamente da Meta Graph API para o banco de dados do tenant.
    """
    if current_user.role not in ["administrator", "manager", "superadmin"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e gestores podem sincronizar templates.")

    ensure_templates_table_exists(db)
    from sqlalchemy import cast, String
    t_id_str = str(current_tenant.id)

    # 1. Buscar credenciais da Meta
    creds = db.query(MetaCredential).filter(
        (MetaCredential.tenant_id == current_tenant.id) | (MetaCredential.tenant_id == t_id_str)
    ).first()
    if not creds:
        creds = db.query(MetaCredential).first()

    if not creds or not creds.permanent_access_token or not (creds.waba_id or creds.phone_number_id):
        raise HTTPException(
            status_code=400,
            detail="Credenciais da Meta (WABA ID ou Token Permanente) não configuradas. Acesse a aba Configurações > Meta para cadastrar."
        )

    token_clean = creds.permanent_access_token.strip()
    candidate_waba_ids = []
    if creds.waba_id and creds.waba_id.strip():
        candidate_waba_ids.append(creds.waba_id.strip())
    if creds.phone_number_id and creds.phone_number_id.strip() and creds.phone_number_id.strip() not in candidate_waba_ids:
        candidate_waba_ids.append(creds.phone_number_id.strip())

    headers = {"Authorization": f"Bearer {token_clean}"}
    synced_count = 0
    templates_meta = []
    last_error = ""

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Tenta buscar templates para cada ID candidato
        for candidate_id in candidate_waba_ids:
            # 1. Tentar direto no candidate_id com v21.0, v20.0 e versão configurada
            for version in ["v21.0", "v20.0", settings.META_API_VERSION or "v18.0"]:
                url = f"https://graph.facebook.com/{version}/{candidate_id}/message_templates"
                try:
                    resp = await client.get(url, headers=headers, params={"limit": 250})
                    if resp.status_code != 200:
                        # Fallback passando access_token como query param
                        resp = await client.get(url, params={"access_token": token_clean, "limit": 250})
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        templates_meta = data.get("data", [])
                        break
                    else:
                        err_obj = resp.json().get("error", {}) if resp.headers.get("content-type", "").startswith("application/json") else {}
                        last_error = err_obj.get("message", resp.text)
                        
                        # Se o erro indicar que o nó é um Phone Number e não WABA, tenta descobrir o WABA ID associado
                        if "WhatsAppBusinessPhoneNumber" in last_error or "node type" in last_error:
                            lookup_url = f"https://graph.facebook.com/{version}/{candidate_id}"
                            lookup_resp = await client.get(lookup_url, headers=headers, params={"fields": "whatsapp_business_account"})
                            if lookup_resp.status_code == 200:
                                real_waba = lookup_resp.json().get("whatsapp_business_account", {}).get("id")
                                if real_waba and real_waba not in candidate_waba_ids:
                                    candidate_waba_ids.append(real_waba)
                except Exception as e:
                    last_error = str(e)
            
            if templates_meta:
                break

        if not templates_meta and last_error:
            # Mensagens de erro amigáveis para orientar o usuário
            if "Error validating access token" in last_error or "Session has expired" in last_error:
                raise HTTPException(status_code=400, detail="Token da Meta expirado ou inválido. Gere um novo Token Permanente no Meta for Developers.")
            elif "whatsapp_business_management" in last_error or "permission" in last_error.lower():
                raise HTTPException(status_code=400, detail="Permissão 'whatsapp_business_management' ausente no Token da Meta.")
            else:
                raise HTTPException(status_code=400, detail=f"Erro na Meta API: {last_error}")

        for item in templates_meta:
            tpl_name = item.get("name")
            if not tpl_name:
                continue
            tpl_lang = item.get("language", "pt_BR")
            tpl_cat = item.get("category", "UTILITY")

            body_text = None
            for comp in item.get("components", []):
                if comp.get("type", "").upper() == "BODY":
                    body_text = comp.get("text")
                    break

            existing = db.query(MessageTemplate).filter(
                cast(MessageTemplate.tenant_id, String) == t_id_str,
                MessageTemplate.name == tpl_name
            ).first()

            if not existing:
                new_tpl = MessageTemplate(
                    tenant_id=t_id_str,
                    name=tpl_name,
                    label=tpl_name.replace("_", " ").title(),
                    language=tpl_lang,
                    category=tpl_cat,
                    body_text=body_text
                )
                db.add(new_tpl)
                synced_count += 1
            else:
                existing.language = tpl_lang
                existing.category = tpl_cat
                if body_text:
                    existing.body_text = body_text

        try:
            db.commit()
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Erro ao salvar templates sincronizados no banco: {str(e)}")

    total_in_db = db.query(MessageTemplate).filter(cast(MessageTemplate.tenant_id, String) == t_id_str).count()
    return {
        "message": f"Sincronização concluída! {synced_count} novos modelos adicionados ({total_in_db} modelos no total).",
        "synced_count": synced_count,
        "total_templates": total_in_db
    }


@router.get("/debug/webhook-events")
def get_debug_webhook_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    from app.models import WebhookEvent
    events = db.query(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(20).all()
    return [{"id": str(e.id), "created_at": str(e.created_at), "payload": e.payload} for e in events]


# --- 🏷️ TAGS / ETIQUETAS ENDPOINTS ---
@router.get("/tags", response_model=List[TagResponse])
def get_tags(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """List all tags for tenant."""
    t_id = str(current_tenant.id)
    return db.query(Tag).filter(Tag.tenant_id == t_id).order_by(Tag.name.asc()).all()

@router.post("/tags", response_model=TagResponse)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Create a new tag with custom name and color."""
    t_id = str(current_tenant.id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="O nome da tag é obrigatório.")
        
    existing = db.query(Tag).filter(Tag.tenant_id == t_id, Tag.name.ilike(name)).first()
    if existing:
        return existing
        
    tag = Tag(
        tenant_id=t_id,
        name=name,
        color=payload.color or "#6366f1"
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag

@router.delete("/tags/{tag_id}")
def delete_tag(
    tag_id: Union[UUID, str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Delete a tag."""
    tid_str = str(tag_id)
    t_id = str(current_tenant.id)
    tag = db.query(Tag).filter(Tag.id == tid_str, Tag.tenant_id == t_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag não encontrada.")
    db.delete(tag)
    db.commit()
    return {"message": "Tag excluída com sucesso."}

@router.post("/conversations/{conversation_id}/tags/{tag_id}", response_model=ConversationResponse)
def add_tag_to_conversation(
    conversation_id: Union[UUID, str],
    tag_id: Union[UUID, str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Assigns a tag to a conversation."""
    cid_str = str(conversation_id)
    tid_str = str(tag_id)
    t_id = str(current_tenant.id)
    
    convo = db.query(Conversation).options(joinedload(Conversation.tags)).filter(
        Conversation.id == cid_str,
        Conversation.tenant_id == t_id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        
    tag = db.query(Tag).filter(Tag.id == tid_str, Tag.tenant_id == t_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag não encontrada.")
        
    try:
        if tag not in convo.tags:
            convo.tags.append(tag)
            db.commit()
    except Exception:
        db.rollback()
        try:
            db.execute(text("""
                INSERT INTO qa_conversation_tags (conversation_id, tag_id)
                VALUES (CAST(:cid AS uuid), CAST(:tid AS uuid))
                ON CONFLICT DO NOTHING
            """), {"cid": cid_str, "tid": tid_str})
            db.commit()
        except Exception:
            pass

    convo = db.query(Conversation).options(
        joinedload(Conversation.contact),
        joinedload(Conversation.tags)
    ).filter(Conversation.id == cid_str).first()
    return convo

@router.delete("/conversations/{conversation_id}/tags/{tag_id}", response_model=ConversationResponse)
def remove_tag_from_conversation(
    conversation_id: Union[UUID, str],
    tag_id: Union[UUID, str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Removes a tag from a conversation."""
    cid_str = str(conversation_id)
    tid_str = str(tag_id)
    t_id = str(current_tenant.id)
    
    convo = db.query(Conversation).options(joinedload(Conversation.tags)).filter(
        Conversation.id == cid_str,
        Conversation.tenant_id == t_id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")

    tag = db.query(Tag).filter(Tag.id == tid_str, Tag.tenant_id == t_id).first()
    if tag and tag in convo.tags:
        convo.tags.remove(tag)
        db.commit()
    else:
        try:
            db.execute(text("""
                DELETE FROM qa_conversation_tags 
                WHERE CAST(conversation_id AS text) = :cid AND CAST(tag_id AS text) = :tid
            """), {"cid": cid_str, "tid": tid_str})
            db.commit()
        except Exception:
            pass
    
    convo = db.query(Conversation).options(
        joinedload(Conversation.contact),
        joinedload(Conversation.tags)
    ).filter(Conversation.id == cid_str).first()
    return convo


# --- 🔍 GLOBAL MESSAGE SEARCH ENDPOINT ---
@router.get("/messages/search", response_model=List[GlobalSearchResult])
def search_messages(
    q: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """Search messages across all conversations for the tenant."""
    query_str = (q or "").strip()
    if not query_str:
        return []
        
    results = db.query(Message, Conversation, Contact).join(
        Conversation, Message.conversation_id == Conversation.id
    ).join(
        Contact, Conversation.contact_id == Contact.id
    ).filter(
        Conversation.tenant_id == current_tenant.id,
        (Message.body.ilike(f"%{query_str}%") | Contact.name.ilike(f"%{query_str}%") | Contact.phone_number.ilike(f"%{query_str}%"))
    ).order_by(Message.created_at.desc()).limit(30).all()
    
    out = []
    for msg, cv, ct in results:
        out.append(GlobalSearchResult(
            id=msg.id,
            conversation_id=cv.id,
            contact_name=ct.name or ct.phone_number,
            phone_number=ct.phone_number,
            snippet=msg.body[:150] if msg.body else "",
            matched_at=msg.created_at or datetime.now(timezone.utc),
            sender_type=msg.sender_type,
            is_note=bool(msg.internal_note)
        ))
    return out


# --- 📌 KANBAN CRM PIPELINE ENDPOINTS ---
@router.get("/crm/kanban", response_model=KanbanBoardResponse)
def get_kanban_board(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Returns the CRM sales pipeline grouped by stages with batch loading."""
    stages = [
        {"stage": "lead", "label": "Novo Lead 📥"},
        {"stage": "qualificacao", "label": "Qualificação 💬"},
        {"stage": "prospect", "label": "Orçamento Enviado 📄"},
        {"stage": "negociacao", "label": "Em Negociação 🤝"},
        {"stage": "customer", "label": "Fechado / Ganho 🎉"},
        {"stage": "perdido", "label": "Perdido ❌"}
    ]
    t_id = str(current_tenant.id)
    contacts = db.query(Contact).filter(Contact.tenant_id == t_id).order_by(Contact.created_at.desc()).limit(300).all()
    columns_map = {s["stage"]: [] for s in stages}
    
    if contacts:
        contact_ids = [str(c.id) for c in contacts]
        
        # Batch load conversations with tags (single query O(1))
        convos = db.query(Conversation).options(
            joinedload(Conversation.tags)
        ).filter(
            Conversation.contact_id.in_(contact_ids),
            Conversation.tenant_id == t_id
        ).all()
        
        convo_by_contact = {}
        convo_ids = []
        for cv in convos:
            cid_str = str(cv.contact_id)
            if cid_str not in convo_by_contact:
                convo_by_contact[cid_str] = cv
                convo_ids.append(str(cv.id))
                
        # Batch load recent messages
        last_msg_map = {}
        if convo_ids:
            recent_msgs = db.query(Message).filter(
                Message.conversation_id.in_(convo_ids)
            ).order_by(Message.created_at.desc()).limit(500).all()
            for m in recent_msgs:
                m_cid = str(m.conversation_id)
                if m_cid not in last_msg_map:
                    last_msg_map[m_cid] = m.body
                
        # Batch load users for agent names
        users = db.query(User).filter(User.tenant_id == t_id).all()
        user_name_map = {str(u.id): (u.name or u.email) for u in users}
        
        for ct in contacts:
            stage_key = ct.kanban_stage or ct.sales_funnel_stage or "lead"
            if stage_key not in columns_map:
                stage_key = "lead"
                
            latest_cv = convo_by_contact.get(str(ct.id))
            assigned_name = None
            card_tags = []
            last_body = None
            last_at = ct.created_at
            cv_id = latest_cv.id if latest_cv else ct.id
            
            if latest_cv:
                card_tags = [TagResponse.from_orm(t) for t in latest_cv.tags] if latest_cv.tags else []
                last_at = latest_cv.last_message_at
                if latest_cv.assigned_user_id:
                    assigned_name = user_name_map.get(str(latest_cv.assigned_user_id))
                last_body = last_msg_map.get(str(latest_cv.id))
                    
            card = KanbanCard(
                id=cv_id,
                contact_id=ct.id,
                name=ct.name or ct.phone_number,
                phone_number=ct.phone_number,
                deal_value=float(ct.deal_value or 0.0),
                kanban_stage=stage_key,
                last_message=last_body or "Nenhuma mensagem recente",
                last_message_at=last_at,
                tags=card_tags,
                assigned_agent_name=assigned_name
            )
            columns_map[stage_key].append(card)
        
    columns_out = []
    grand_total_val = 0.0
    grand_total_deals = 0
    
    for s in stages:
        cards_list = columns_map[s["stage"]]
        col_val = sum(c.deal_value for c in cards_list)
        grand_total_val += col_val
        grand_total_deals += len(cards_list)
        
        columns_out.append(KanbanColumn(
            stage=s["stage"],
            label=s["label"],
            total_deals=len(cards_list),
            total_value=round(col_val, 2),
            cards=cards_list
        ))
        
    return KanbanBoardResponse(
        columns=columns_out,
        grand_total_value=round(grand_total_val, 2),
        grand_total_deals=grand_total_deals
    )

@router.patch("/contacts/{contact_id}/kanban-stage")
@router.post("/contacts/{contact_id}/kanban-stage")
@router.put("/contacts/{contact_id}/kanban-stage")
def update_contact_kanban_stage(
    contact_id: Union[UUID, str],
    payload: KanbanStageUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Moves a contact to a new Kanban stage and optionally updates deal value."""
    cid_str = str(contact_id)
    t_id_str = str(current_tenant.id)
    contact = db.query(Contact).filter(Contact.id == cid_str, Contact.tenant_id == t_id_str).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contato não encontrado.")
        
    contact.kanban_stage = payload.kanban_stage
    contact.sales_funnel_stage = payload.kanban_stage
    if payload.deal_value is not None:
        contact.deal_value = float(payload.deal_value)
        
    db.commit()
    db.refresh(contact)
    return {"message": "Estágio atualizado com sucesso.", "kanban_stage": contact.kanban_stage, "deal_value": contact.deal_value}


# --- ⭐ CSAT SATISFACTION SURVEY ENDPOINT ---
@router.post("/conversations/{conversation_id}/resolve-with-csat")
async def resolve_conversation_with_csat(
    conversation_id: UUID,
    payload: ResolveCSATRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """Resolves conversation and optionally sends a WhatsApp 1-5 star CSAT survey."""
    convo = db.query(Conversation).filter(
        Conversation.id == str(conversation_id),
        Conversation.tenant_id == current_tenant.id
    ).first()
    if not convo:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
        
    convo.status = "resolved"
    now_utc = datetime.now(timezone.utc)
    
    # Save system resolution message
    user_name = current_user.name or current_user.email
    db.add(Message(
        conversation_id=convo.id,
        sender_type="system",
        sender_id=current_user.id,
        message_type="system",
        body=f"✅ Atendimento finalizado por {user_name}.",
        internal_note=True,
        created_at=now_utc
    ))
    
    if payload.send_csat:
        convo.csat_sent_at = now_utc
        contact = db.query(Contact).filter(Contact.id == convo.contact_id).first()
        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
        
        if contact and creds and creds.phone_number_id and creds.permanent_access_token:
            phone = format_brazilian_phone(contact.phone_number)
            csat_text = (
                payload.rating_question or 
                "Como você avalia o nosso atendimento hoje? Responda com uma nota de 1 a 5 estrelas:\n\n"
                "⭐⭐⭐⭐⭐ 5 - Excelente\n"
                "⭐⭐⭐⭐ 4 - Muito Bom\n"
                "⭐⭐⭐ 3 - Bom\n"
                "⭐⭐ 2 - Regular\n"
                "⭐ 1 - Ruim"
            )
            
            meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{creds.phone_number_id}/messages"
            headers = {
                "Authorization": f"Bearer {creds.permanent_access_token}",
                "Content-Type": "application/json"
            }
            meta_payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {"body": csat_text}
            }
            
            async with httpx.AsyncClient() as client:
                try:
                    res = await client.post(meta_url, headers=headers, json=meta_payload, timeout=10.0)
                    if res.status_code == 200:
                        m_id = res.json().get("messages", [{}])[0].get("id")
                        db.add(Message(
                            conversation_id=convo.id,
                            sender_type="bot",
                            message_type="text",
                            body=csat_text,
                            meta_message_id=m_id,
                            status="sent",
                            created_at=now_utc
                        ))
                except Exception as err:
                    print(f"[CSAT Send Error] {err}")
                    
    db.commit()
    db.refresh(convo)
    return convo





