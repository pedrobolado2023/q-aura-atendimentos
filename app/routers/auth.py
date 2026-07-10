from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Tenant, User, MetaCredential
from app.schemas import TenantCreate, UserCreate, UserLogin, Token, TenantResponse, UserResponse, MetaCredentialCreate, MetaCredentialResponse
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user, get_current_tenant

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/onboard", response_model=TenantResponse)
def onboard_tenant(tenant_in: TenantCreate, db: Session = Depends(get_db)):
    # Check if subdomain exists
    db_tenant = db.query(Tenant).filter(Tenant.subdomain == tenant_in.subdomain).first()
    if db_tenant:
        raise HTTPException(status_code=400, detail="Subdomain already registered")
    
    tenant = Tenant(
        name=tenant_in.name,
        subdomain=tenant_in.subdomain,
        plan_type="free",
        status="active"
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant

@router.post("/signup", response_model=UserResponse)
def signup_user(user_in: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user_in.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    tenant = db.query(Tenant).filter(Tenant.id == user_in.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    user = User(
        email=user_in.email,
        password_hash=get_password_hash(user_in.password),
        name=user_in.name,
        tenant_id=user_in.tenant_id,
        role=user_in.role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.post("/login", response_model=Token)
def login(user_credentials: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_credentials.email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Credentials")
    
    if not verify_password(user_credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Credentials")
        
    access_token = create_access_token(data={"sub": str(user.id), "tenant_id": str(user.tenant_id)})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/meta-credentials", response_model=MetaCredentialResponse)
def configure_meta_credentials(
    creds_in: MetaCredentialCreate, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized to configure credentials")
        
    # Generate tenant-specific webhook URL
    webhook_url = f"https://api.q-aura.com/api/webhook/{current_tenant.id}"
    
    # Check if credentials exist for tenant
    db_creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if db_creds:
        db_creds.phone_number_id = creds_in.phone_number_id
        db_creds.waba_id = creds_in.waba_id
        db_creds.permanent_access_token = creds_in.permanent_access_token
        db_creds.verify_token = creds_in.verify_token
        db_creds.webhook_url = webhook_url
        db.commit()
        db.refresh(db_creds)
        return db_creds
    
    creds = MetaCredential(
        tenant_id=current_tenant.id,
        phone_number_id=creds_in.phone_number_id,
        waba_id=creds_in.waba_id,
        permanent_access_token=creds_in.permanent_access_token,
        verify_token=creds_in.verify_token,
        webhook_url=webhook_url
    )
    db.add(creds)
    db.commit()
    db.refresh(creds)
    return creds
