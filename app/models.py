import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Boolean, Text, JSON, Table, Integer, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base, db_url

# Dynamic type selection for local sqlite and production postgresql
if db_url.startswith("sqlite"):
    ArrayType = JSON
else:
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    ArrayType = PG_ARRAY(String)

def generate_uuid_str():
    return str(uuid.uuid4())

class Plan(Base):
    __tablename__ = "qa_plans"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    price_monthly = Column(Numeric(10, 2), default=0)
    modules = Column(ArrayType, default=[])
    max_users = Column(Integer, default=5)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenants = relationship("Tenant", back_populates="plan")

# Association Table for User-Department (Many-to-Many)
qa_agents_departments = Table(
    'qa_agents_departments',
    Base.metadata,
    Column('user_id', String(36), ForeignKey('qa_users.id', ondelete='CASCADE'), primary_key=True),
    Column('department_id', String(36), ForeignKey('qa_departments.id', ondelete='CASCADE'), primary_key=True)
)

class Tenant(Base):
    __tablename__ = "qa_tenants"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    name = Column(String(255), nullable=False)
    subdomain = Column(String(100), unique=True, nullable=False, index=True)
    plan_type = Column(String(50), default="free")  # kept for backward compat
    plan_id = Column(String(36), ForeignKey("qa_plans.id", ondelete="SET NULL"), nullable=True)
    cnpj = Column(String(20))
    segment = Column(String(100), default="hotel")
    status = Column(String(50), default="active")  # active, suspended, trial
    trial_ends_at = Column(DateTime(timezone=True))
    custom_modules = Column(ArrayType, default=[])
    logo_url = Column(Text)
    max_users = Column(Integer, default=5)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    plan = relationship("Plan", back_populates="tenants")
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    meta_credentials = relationship("MetaCredential", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    bot_config = relationship("BotConfig", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    departments = relationship("Department", back_populates="tenant", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="tenant", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="tenant", cascade="all, delete-orphan")

class User(Base):
    __tablename__ = "qa_users"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(50), default="agent") # administrator, manager, agent
    status = Column(String(50), default="offline") # online, busy, offline
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="users")
    departments = relationship("Department", secondary=qa_agents_departments, back_populates="agents")

class MetaCredential(Base):
    __tablename__ = "qa_meta_credentials"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    phone_number_id = Column(String(100), nullable=False)
    waba_id = Column(String(100), nullable=False)
    permanent_access_token = Column(Text, nullable=False)
    verify_token = Column(String(255), unique=True, nullable=False)
    webhook_url = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="meta_credentials")

class BotConfig(Base):
    __tablename__ = "qa_bot_configs"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    is_active = Column(Boolean, default=True)
    welcome_message = Column(Text, default="Olá! Seja bem-vindo ao nosso hotel. Como posso ajudar você hoje?")
    fallback_message = Column(Text, default="Desculpe, não consegui entender. Digite *Atendente* a qualquer momento para falar com um humano.")
    out_of_hours_message = Column(Text, default="Olá! Nosso horário de atendimento é das 08h às 22h. Deixe sua mensagem que responderemos o mais breve possível.")
    transfer_keywords = Column(Text, default="atendente,humano,falar,suporte,ajuda")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="bot_config")

class Department(Base):
    __tablename__ = "qa_departments"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    color = Column(String(7), default="#3B82F6")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="departments")
    agents = relationship("User", secondary=qa_agents_departments, back_populates="departments")

class Contact(Base):
    __tablename__ = "qa_contacts"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False)
    phone_number = Column(String(30), nullable=False)
    name = Column(String(255))
    email = Column(String(255))
    language = Column(String(10), default="pt-BR")
    loyalty_level = Column(String(50), default="none")
    pms_id = Column(String(100))
    sales_funnel_stage = Column(String(50), default="lead") # lead, qualified, quotation, reservation_pending, reservation_confirmed, lost
    avatar_url = Column(String(500))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="contacts")
    conversations = relationship("Conversation", back_populates="contact", cascade="all, delete-orphan")

class Conversation(Base):
    __tablename__ = "qa_conversations"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String(36), ForeignKey("qa_contacts.id", ondelete="CASCADE"), nullable=False)
    assigned_user_id = Column(String(36), ForeignKey("qa_users.id", ondelete="SET NULL"))
    assigned_department_id = Column(String(36), ForeignKey("qa_departments.id", ondelete="SET NULL"))
    status = Column(String(50), default="waiting") # bot, waiting, active, resolved, archived
    routing_mode = Column(String(50), default="queue") # round_robin, queue, fixed, department
    unread = Column(Boolean, default=True)
    unread_count = Column(Integer, default=0)
    last_message_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="conversations")
    contact = relationship("Contact", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "qa_messages"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    conversation_id = Column(String(36), ForeignKey("qa_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_type = Column(String(20), nullable=False) # bot, agent, contact, system
    sender_id = Column(String(36))
    message_type = Column(String(50), nullable=False) # text, image, audio, video, document, location, interactive_button, interactive_list, template
    body = Column(Text)
    media_url = Column(Text)
    media_mime_type = Column(String(100))
    meta_message_id = Column(String(255), index=True)
    status = Column(String(50), default="sent") # sent, delivered, read, failed
    internal_note = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")

class WebhookEvent(Base):
    __tablename__ = "qa_webhook_events"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="SET NULL"))
    provider = Column(String(50), default="meta")
    payload = Column(JSON, nullable=False)
    processed = Column(Boolean, default=False)
    error_log = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class QuickMessage(Base):
    __tablename__ = "qa_quick_messages"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("qa_users.id", ondelete="CASCADE"), nullable=True, index=True)
    shortcut = Column(String(50), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")
    user = relationship("User")


class MarketingCampaign(Base):
    __tablename__ = "qa_marketing_campaigns"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    media_type = Column(String(50), default="text") # 'text', 'image', 'video', 'audio'
    media_url = Column(Text, nullable=True)
    button_type = Column(String(50), nullable=True) # 'none', 'quick_reply', 'cta_url'
    button_label = Column(String(100), nullable=True)
    button_url = Column(Text, nullable=True)
    sent_count = Column(Integer, default=0)
    delivered_count = Column(Integer, default=0)
    read_count = Column(Integer, default=0)
    click_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")


class CampaignRecipient(Base):
    __tablename__ = "qa_campaign_recipients"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    campaign_id = Column(String(36), ForeignKey("qa_marketing_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    contact_id = Column(String(36), ForeignKey("qa_contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    meta_message_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), default="sent") # 'sent', 'delivered', 'read', 'failed'
    clicked = Column(Boolean, default=False)
    clicked_at = Column(DateTime(timezone=True), nullable=True)

    campaign = relationship("MarketingCampaign")
    contact = relationship("Contact")


