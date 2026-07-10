import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Boolean, Text, JSON, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

def generate_uuid_str():
    return str(uuid.uuid4())

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
    plan_type = Column(String(50), default="free")
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    meta_credentials = relationship("MetaCredential", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    departments = relationship("Department", back_populates="tenant", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="tenant", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="tenant", cascade="all, delete-orphan")

class User(Base):
    __tablename__ = "qa_users"
    id = Column(String(36), primary_key=True, default=generate_uuid_str)
    tenant_id = Column(String(36), ForeignKey("qa_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
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
