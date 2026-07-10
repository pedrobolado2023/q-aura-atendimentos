from sqlalchemy.orm import Session
from app.models import Contact, Conversation, Message
from datetime import datetime

async def process_webhook_payload(tenant_id: str, payload: dict, db: Session, websocket_broadcast_fn) -> bool:
    """
    Parses Meta WhatsApp Webhook payload and updates contacts, conversations, messages,
    then broadcasts updates to active agents via WebSocket.
    """
    try:
        # Check if this is a message event
        entry_list = payload.get("entry", [])
        for entry in entry_list:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts_meta = value.get("contacts", [])

                if not messages:
                    continue

                # Parse profile contact details
                contact_name = "Hóspede WhatsApp"
                if contacts_meta:
                    contact_name = contacts_meta[0].get("profile", {}).get("name", contact_name)

                for msg_data in messages:
                    sender_phone = msg_data.get("from")
                    meta_msg_id = msg_data.get("id")
                    msg_type = msg_data.get("type", "text")
                    
                    # Extract body content based on type
                    body_content = ""
                    media_url = None
                    media_mime = None

                    if msg_type == "text":
                        body_content = msg_data.get("text", {}).get("body", "")
                    elif msg_type == "image":
                        body_content = "[Imagem]"
                        media_url = msg_data.get("image", {}).get("id") # Meta media ID
                        media_mime = msg_data.get("image", {}).get("mime_type")
                    elif msg_type == "audio":
                        body_content = "[Áudio]"
                        media_url = msg_data.get("audio", {}).get("id")
                        media_mime = msg_data.get("audio", {}).get("mime_type")
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

                    # 2. Resolve Conversation (find active/waiting/bot, or create new)
                    convo = db.query(Conversation).filter(
                        Conversation.tenant_id == tenant_id,
                        Conversation.contact_id == contact.id,
                        Conversation.status.in_(["bot", "waiting", "active"])
                    ).first()

                    if not convo:
                        convo = Conversation(
                            tenant_id=tenant_id,
                            contact_id=contact.id,
                            status="waiting", # Default to human queue for simplicity in Phase 1
                            routing_mode="queue"
                        )
                        db.add(convo)
                        db.commit()
                        db.refresh(convo)

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
                        status="delivered"
                    )
                    db.add(new_msg)
                    
                    # Update conversation last message timestamp and mark as unread
                    convo.last_message_at = datetime.utcnow()
                    convo.unread = True
                    db.commit()
                    db.refresh(new_msg)

                    # 4. Broadcast via WebSocket Manager
                    broadcast_data = {
                        "type": "new_message",
                        "conversation_id": convo.id,
                        "sender_type": "contact",
                        "body": body_content,
                        "message_type": msg_type,
                        "media_url": media_url,
                        "unread": True,
                        "created_at": new_msg.created_at.isoformat() if new_msg.created_at else None
                    }
                    await websocket_broadcast_fn(tenant_id, broadcast_data)

        return True
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error parsing webhook payload: {e}")
        return False
