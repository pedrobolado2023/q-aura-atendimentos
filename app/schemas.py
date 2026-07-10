from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

# Tenant Schemas
class TenantBase(BaseModel):
    name: str
    subdomain: str

class TenantCreate(TenantBase):
    pass

class TenantResponse(TenantBase):
    id: UUID
    plan_type: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

# User Schemas
class UserBase(BaseModel):
    email: EmailStr
    name: str
    role: str

class UserCreate(UserBase):
    password: str
    tenant_id: UUID

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(UserBase):
    id: UUID
    tenant_id: UUID
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None

# Meta Credentials Schemas
class MetaCredentialCreate(BaseModel):
    phone_number_id: str
    waba_id: str
    permanent_access_token: str
    verify_token: str

class MetaCredentialResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    phone_number_id: str
    waba_id: str
    webhook_url: str
    created_at: datetime

    class Config:
        from_attributes = True

class MetaCredentialDetailsResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    phone_number_id: str
    waba_id: str
    verify_token: str
    permanent_access_token: str  # Retorna a string mascarada para a UI
    webhook_url: str
    created_at: datetime

    class Config:
        from_attributes = True


# Conversation and Messages
class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_type: str
    sender_id: Optional[UUID]
    message_type: str
    body: Optional[str]
    media_url: Optional[str]
    media_mime_type: Optional[str]
    status: str
    internal_note: bool
    created_at: datetime

    class Config:
        from_attributes = True

class ContactResponse(BaseModel):
    id: UUID
    phone_number: str
    name: Optional[str]
    email: Optional[str]
    language: str
    loyalty_level: str
    sales_funnel_stage: str
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    contact_id: UUID
    assigned_user_id: Optional[UUID]
    assigned_department_id: Optional[UUID]
    status: str
    routing_mode: str
    last_message_at: datetime
    created_at: datetime
    unread: bool
    unread_count: int
    contact: Optional[ContactResponse] = None

    class Config:
        from_attributes = True


# CRM & Campaign Schemas
class BulkContactItem(BaseModel):
    name: str
    phone_number: str

class BulkContactUploadRequest(BaseModel):
    contacts: List[BulkContactItem]

class CampaignSendRequest(BaseModel):
    name: str
    media_type: str  # text, image, video, audio
    media_url: Optional[str] = None
    body: str
    button_type: str  # none, cta_url, quick_reply
    button_label: Optional[str] = None
    button_url: Optional[str] = None

# Chatbot Config Schemas
class BotConfigResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    is_active: bool
    welcome_message: str
    fallback_message: str
    out_of_hours_message: str
    transfer_keywords: str

    class Config:
        from_attributes = True

class BotConfigUpdate(BaseModel):
    is_active: Optional[bool] = None
    welcome_message: Optional[str] = None
    fallback_message: Optional[str] = None
    out_of_hours_message: Optional[str] = None
    transfer_keywords: Optional[str] = None

# Dashboard Metrics Schemas
class DepartmentMetric(BaseModel):
    name: str
    count: int

class FunnelStageMetric(BaseModel):
    stage: str
    count: int
    percentage: float

class DashboardMetricsResponse(BaseModel):
    total_conversations: int
    bot_resolution_rate: float
    avg_response_time_seconds: float
    conversion_rate: float
    funnel_stages: List[FunnelStageMetric]
    department_counts: List[DepartmentMetric]



