import httpx
from typing import Optional
from sqlalchemy.orm import Session
from app.models import Contact, Conversation, Message, BotConfig, MetaCredential, CampaignRecipient
from app.config import settings
from app.database import SessionLocal
from datetime import datetime

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

async def send_whatsapp_text(phone_number_id: str, token: str, to_phone: str, body: str) -> Optional[str]:
    """Helper function to send a text WhatsApp message via Meta Cloud API."""
    meta_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body
        }
    }
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(meta_url, headers=headers, json=payload)
            if res.status_code == 200:
                return res.json().get("messages", [{}])[0].get("id")
            else:
                print(f"Meta API send error: {res.text}")
        except Exception as e:
            print(f"Error sending WhatsApp message: {str(e)}")
    return None

async def relay_webhook_to_n8n(url: str, payload: dict):
    """
    Relays the raw Meta webhook payload to the n8n endpoint asynchronously.
    """
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Content-Type": "application/json"}
            res = await client.post(url, json=payload, headers=headers, timeout=10.0)
            if res.status_code not in [200, 201]:
                print(f"[Webhook Relay] n8n returned error status {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[Webhook Relay] Error forwarding webhook to n8n at {url}: {e}")

async def process_webhook_payload(tenant_id: str, payload: dict, websocket_broadcast_fn) -> bool:
    """
    Parses Meta WhatsApp Webhook payload and updates contacts, conversations, messages,
    then broadcasts updates to active agents via WebSocket. Intercepts with Chatbot replies.
    """
    db = SessionLocal()
    try:
        # Check if this is a message event
        entry_list = payload.get("entry", [])
        for entry in entry_list:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts_meta = value.get("contacts", [])
                statuses = value.get("statuses", [])

                if statuses:
                    for status_data in statuses:
                        meta_msg_id = status_data.get("id")
                        status_type = status_data.get("status") # 'delivered', 'read', 'failed'
                        
                        recipient = db.query(CampaignRecipient).filter(
                            CampaignRecipient.meta_message_id == meta_msg_id
                        ).first()
                        
                        if recipient:
                            current_status = recipient.status
                            if status_type == "read":
                                if current_status != "read":
                                    recipient.status = "read"
                                    campaign = recipient.campaign
                                    if campaign:
                                        campaign.read_count = (campaign.read_count or 0) + 1
                                        if current_status == "sent":
                                            campaign.delivered_count = (campaign.delivered_count or 0) + 1
                            elif status_type == "delivered":
                                if current_status not in ["delivered", "read"]:
                                    recipient.status = "delivered"
                                    campaign = recipient.campaign
                                    if campaign:
                                        campaign.delivered_count = (campaign.delivered_count or 0) + 1
                            elif status_type == "failed":
                                recipient.status = "failed"
                            
                            db.commit()

                if not messages:
                    continue

                # Parse profile contact details
                contact_name = "Hóspede WhatsApp"
                if contacts_meta:
                    contact_name = contacts_meta[0].get("profile", {}).get("name", contact_name)

                for msg_data in messages:
                    sender_phone = format_brazilian_phone(msg_data.get("from"))
                    meta_msg_id = msg_data.get("id")
                    msg_type = msg_data.get("type", "text")
                    
                    # Extract body content based on type
                    body_content = ""
                    media_url = None
                    media_mime = None

                    button_reply_id = None
                    if msg_type == "text":
                        body_content = msg_data.get("text", {}).get("body", "")
                    elif msg_type == "image":
                        body_content = msg_data.get("image", {}).get("caption") or "[Imagem]"
                        media_url = msg_data.get("image", {}).get("id") # Meta media ID
                        media_mime = msg_data.get("image", {}).get("mime_type")
                    elif msg_type == "audio":
                        body_content = "[Áudio]"
                        media_url = msg_data.get("audio", {}).get("id")
                        media_mime = msg_data.get("audio", {}).get("mime_type")
                    elif msg_type == "sticker":
                        body_content = "[Figurinha]"
                        media_url = msg_data.get("sticker", {}).get("id")
                        media_mime = msg_data.get("sticker", {}).get("mime_type", "image/webp")
                    elif msg_type == "document":
                        doc_data = msg_data.get("document", {})
                        body_content = doc_data.get("caption") or doc_data.get("filename") or "[Documento]"
                        media_url = doc_data.get("id")
                        media_mime = doc_data.get("mime_type")
                    elif msg_type == "video":
                        vid_data = msg_data.get("video", {})
                        body_content = vid_data.get("caption") or "[Vídeo]"
                        media_url = vid_data.get("id")
                        media_mime = vid_data.get("mime_type")
                    elif msg_type == "location":
                        loc = msg_data.get("location", {})
                        loc_name = loc.get("name") or loc.get("address") or f"{loc.get('latitude')}, {loc.get('longitude')}"
                        body_content = f"📍 [Localização] {loc_name}"
                    elif msg_type == "contacts":
                        contacts_list = msg_data.get("contacts", [])
                        c_name = contacts_list[0].get("name", {}).get("formatted_name", "Contato") if contacts_list else "Contato"
                        body_content = f"📇 [Contato] {c_name}"
                    elif msg_type == "reaction":
                        emoji = msg_data.get("reaction", {}).get("emoji", "👍")
                        body_content = f"Reagiu: {emoji}"
                    elif msg_type == "interactive":
                        interactive_data = msg_data.get("interactive", {})
                        int_type = interactive_data.get("type")
                        if int_type == "button_reply":
                            body_content = interactive_data.get("button_reply", {}).get("title", "[Clique no Botão]")
                            button_reply_id = interactive_data.get("button_reply", {}).get("id", "")
                        elif int_type == "list_reply":
                            body_content = interactive_data.get("list_reply", {}).get("title", "[Seleção da Lista]")
                        else:
                            body_content = "[Resposta Interativa]"
                    elif msg_type == "unsupported":
                        body_content = "[Figurinha / Mensagem do WhatsApp]"
                    else:
                        body_content = f"[{msg_type.capitalize()}]"

                    # 1. Resolve Contact
                    contact = db.query(Contact).filter(
                        Contact.tenant_id == tenant_id,
                        Contact.phone_number == sender_phone
                    ).first()

                    if not contact:
                        contact = Contact(
                            tenant_id=tenant_id,
                            phone_number=sender_phone,
                            name=contact_name,
                            sales_funnel_stage="lead"
                        )
                        db.add(contact)
                        db.commit()
                        db.refresh(contact)

                    # Check campaign quick-reply button click tracking
                    if button_reply_id and button_reply_id.startswith("camp_click_"):
                        recipient_id_str = button_reply_id.replace("camp_click_", "")
                        recipient = db.query(CampaignRecipient).filter(CampaignRecipient.id == recipient_id_str).first()
                        if recipient and not recipient.clicked:
                            recipient.clicked = True
                            recipient.clicked_at = datetime.utcnow()
                            campaign = recipient.campaign
                            if campaign:
                                campaign.click_count = (campaign.click_count or 0) + 1
                            db.commit()

                    # 2. Resolve Conversation (find the latest conversation for this contact)
                    convo = db.query(Conversation).filter(
                        Conversation.tenant_id == tenant_id,
                        Conversation.contact_id == contact.id
                    ).order_by(Conversation.last_message_at.desc()).first()

                    # Check if chatbot or n8n is configured
                    bot_config = db.query(BotConfig).filter(BotConfig.tenant_id == tenant_id).first()
                    is_bot_active = bool(bot_config and bot_config.is_active)
                    n8n_url = None
                    if bot_config and bot_config.n8n_webhook_url:
                        n8n_url = bot_config.n8n_webhook_url
                    elif settings.N8N_WEBHOOK_URL:
                        n8n_url = settings.N8N_WEBHOOK_URL
                    
                    is_any_bot_enabled = is_bot_active or bool(n8n_url)

                    # Timestamp real da mensagem do Meta WhatsApp ou hora atual UTC
                    meta_raw_ts = msg_data.get("timestamp")
                    if meta_raw_ts:
                        try:
                            msg_created_at = datetime.fromtimestamp(int(meta_raw_ts), timezone.utc)
                        except Exception:
                            msg_created_at = datetime.now(timezone.utc)
                    else:
                        msg_created_at = datetime.now(timezone.utc)

                    is_new_convo = False
                    if not convo:
                        is_new_convo = True
                        convo = Conversation(
                            tenant_id=tenant_id,
                            contact_id=contact.id,
                            status="bot" if is_any_bot_enabled else "waiting",
                            routing_mode="queue"
                        )
                        db.add(convo)
                        db.commit()
                        db.refresh(convo)
                    else:
                        # If conversation was resolved, re-open it!
                        if convo.status == "resolved":
                            convo.status = "bot" if is_any_bot_enabled else "waiting"
                            convo.assigned_user_id = None # Clear previous assignment so it goes to queue!
                            sys_text = "🤖 Conversa reaberta e enviada ao Robô Chatbot" if is_any_bot_enabled else "⏳ Conversa reaberta e enviada para a fila de atendimento"
                            sys_msg = Message(
                                conversation_id=convo.id,
                                sender_type="system",
                                message_type="system",
                                body=sys_text,
                                internal_note=True,
                                created_at=msg_created_at
                            )
                            db.add(sys_msg)

                    if is_new_convo:
                        sys_text = "🤖 Conversa iniciada e enviada para o Robô Chatbot" if is_any_bot_enabled else "⏳ Conversa entrou na fila de atendimento"
                        sys_msg = Message(
                            conversation_id=convo.id,
                            sender_type="system",
                            message_type="system",
                            body=sys_text,
                            internal_note=True,
                            created_at=msg_created_at
                        )
                        db.add(sys_msg)

                    # Check if message already exists
                    existing_msg = db.query(Message).filter(Message.meta_message_id == meta_msg_id).first()
                    if existing_msg:
                        continue

                    # 3. Create Message
                    new_msg = Message(
                        conversation_id=convo.id,
                        sender_type="contact",
                        sender_id=contact.id,
                        message_type=msg_type,
                        body=body_content,
                        media_url=media_url,
                        media_mime_type=media_mime,
                        meta_message_id=meta_msg_id,
                        status="delivered",
                        created_at=msg_created_at
                    )
                    db.add(new_msg)
                    
                    # Update conversation last message timestamp and increment unread count
                    convo.last_message_at = msg_created_at
                    convo.unread = True
                    convo.unread_count = (convo.unread_count or 0) + 1
                    db.commit()
                    db.refresh(new_msg)

                    # 4. Broadcast via WebSocket Manager
                    broadcast_data = {
                        "type": "new_message",
                        "id": new_msg.id,
                        "conversation_id": convo.id,
                        "sender_type": "contact",
                        "body": body_content,
                        "message_type": msg_type,
                        "media_url": media_url,
                        "unread": True,
                        "unread_count": convo.unread_count,
                        "contact_name": contact.name or contact.phone_number,
                        "contact_phone": contact.phone_number,
                        "contact_avatar": contact.avatar_url,
                        "preview": body_content[:60] if body_content else "",
                        "last_message_at": convo.last_message_at.isoformat() if convo.last_message_at else None,
                        "created_at": new_msg.created_at.isoformat() if new_msg.created_at else None,
                        "has_active_window": True
                    }
                    await websocket_broadcast_fn(tenant_id, broadcast_data)

                    # Check if conversation has been taken over by human agent
                    is_human_handled = (convo.status == "active" or convo.assigned_user_id is not None)

                    # 5. Site Chatbot Autoreply Logic (Only if NOT handled by human agent)
                    if not is_human_handled and convo.status == "bot" and is_bot_active and bot_config:
                        creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == tenant_id).first()
                        if creds:
                            # Parse keywords to check for transfer to human agent
                            keywords = [k.strip().lower() for k in bot_config.transfer_keywords.split(",") if k.strip()] if bot_config.transfer_keywords else []
                            should_transfer = any(k in body_content.lower() for k in keywords)

                            bot_reply_body = ""
                            if should_transfer:
                                convo.status = "waiting" # Transfer to human queue
                                db.commit()
                                bot_reply_body = "Certo, estou te transferindo para a fila de atendimento humano. Um momento, por favor!"
                            else:
                                # Count previous messages from contact to decide between welcome or fallback
                                contact_msg_count = db.query(Message).filter(
                                    Message.conversation_id == convo.id,
                                    Message.sender_type == "contact"
                                ).count()

                                if contact_msg_count <= 1:
                                    bot_reply_body = bot_config.welcome_message
                                else:
                                    bot_reply_body = bot_config.fallback_message

                            if bot_reply_body:
                                # Send reply via WhatsApp API
                                bot_meta_msg_id = await send_whatsapp_text(
                                    phone_number_id=creds.phone_number_id,
                                    token=creds.permanent_access_token,
                                    to_phone=contact.phone_number,
                                    body=bot_reply_body
                                )

                                # Save bot message to database
                                bot_msg = Message(
                                    conversation_id=convo.id,
                                    sender_type="bot",
                                    body=bot_reply_body,
                                    meta_message_id=bot_meta_msg_id,
                                    status="sent" if bot_meta_msg_id else "failed"
                                )
                                db.add(bot_msg)
                                
                                # Mark conversation last message timestamp
                                convo.last_message_at = datetime.utcnow()
                                db.commit()
                                db.refresh(bot_msg)

                                # Broadcast the bot's reply via WebSocket
                                bot_broadcast_data = {
                                    "type": "new_message",
                                    "id": bot_msg.id,
                                    "conversation_id": convo.id,
                                    "sender_type": "bot",
                                    "body": bot_reply_body,
                                    "message_type": "text",
                                    "media_url": None,
                                    "unread": False,
                                    "unread_count": convo.unread_count,
                                    "contact_name": contact.name or contact.phone_number,
                                    "contact_phone": contact.phone_number,
                                    "contact_avatar": contact.avatar_url,
                                    "preview": bot_reply_body[:60] if bot_reply_body else "",
                                    "last_message_at": convo.last_message_at.isoformat() if convo.last_message_at else None,
                                    "created_at": bot_msg.created_at.isoformat() if bot_msg.created_at else None,
                                    "has_active_window": True
                                }
                                await websocket_broadcast_fn(tenant_id, bot_broadcast_data)

        # 6. Forward/Relay to n8n webhook (Only if NOT handled by human agent)
        if 'convo' in locals() and convo:
            is_human_handled = (convo.status == "active" or convo.assigned_user_id is not None)
            bot_config = db.query(BotConfig).filter(BotConfig.tenant_id == tenant_id).first()
            n8n_url = None
            if bot_config and bot_config.n8n_webhook_url:
                n8n_url = bot_config.n8n_webhook_url
            elif settings.N8N_WEBHOOK_URL:
                n8n_url = settings.N8N_WEBHOOK_URL

            if n8n_url and not is_human_handled:
                payload["conversation_id"] = str(convo.id)
                await relay_webhook_to_n8n(n8n_url, payload)

        return True
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"Error parsing webhook payload: {e}")
        return False
    finally:
        db.close()
