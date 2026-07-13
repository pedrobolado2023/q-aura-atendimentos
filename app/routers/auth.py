from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Tenant, User, MetaCredential, Plan
from app.schemas import TenantCreate, UserCreate, UserLogin, Token, TenantResponse, UserResponse, MetaCredentialCreate, MetaCredentialResponse, EmployeeCreate
from app.auth import get_password_hash, verify_password, create_access_token, get_current_user, get_current_tenant, ModuleRequired
from typing import List

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

    # Superadmin has no tenant_id
    token_data = {"sub": str(user.id), "tenant_id": str(user.tenant_id) if user.tenant_id else ""}
    access_token = create_access_token(data=token_data)
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/meta-credentials", response_model=MetaCredentialResponse)
def configure_meta_credentials(
    creds_in: MetaCredentialCreate, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings"))
):
    if current_user.role != "administrator":
        raise HTTPException(status_code=403, detail="Apenas administradores podem configurar credenciais do WhatsApp.")
        
    # Generate tenant-specific webhook URL
    webhook_url = f"https://api.q-aura.com/api/webhook/{current_tenant.id}"
    
    # Check if credentials exist for tenant
    db_creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if db_creds:
        db_creds.phone_number_id = creds_in.phone_number_id
        db_creds.waba_id = creds_in.waba_id
        
        # Só atualiza a senha se não for um placeholder mascarado
        token = creds_in.permanent_access_token
        if token and not (token.startswith("••") or "..." in token):
            db_creds.permanent_access_token = token
            
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


from app.schemas import MetaCredentialDetailsResponse


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Returns the currently logged-in user profile, including enabled_modules for the tenant.
    Superadmin gets a special response with all-access flag.
    """
    # Superadmin: no tenant, full access
    if current_user.role == "superadmin":
        return {
            "id": current_user.id,
            "email": current_user.email,
            "name": current_user.name,
            "role": current_user.role,
            "tenant_id": None,
            "status": current_user.status,
            "created_at": current_user.created_at,
            "enabled_modules": ["superadmin"],
            "tenant": None,
        }

    # Regular user: fetch tenant and compute enabled modules
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    enabled_modules = []
    if tenant:
        if tenant.status == "suspended":
            raise HTTPException(status_code=403, detail="Conta suspensa. Entre em contato com o suporte.")
        base_modules = list(tenant.plan.modules or []) if tenant.plan else []
        custom = list(tenant.custom_modules or [])
        enabled_modules = list(set(base_modules + custom))
        # If no plan is set, give full access (legacy tenants)
        if not tenant.plan_id:
            enabled_modules = ["inbox", "chatbot", "dashboard", "crm", "team", "meta_settings"]

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id,
        "status": current_user.status,
        "created_at": current_user.created_at,
        "enabled_modules": enabled_modules,
        "tenant": {
            "id": tenant.id if tenant else None,
            "name": tenant.name if tenant else None,
            "status": tenant.status if tenant else None,
            "plan_type": tenant.plan_type if tenant else None,
        } if tenant else None,
    }

@router.get("/meta-credentials", response_model=MetaCredentialDetailsResponse)
def get_meta_credentials(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings"))
):
    """
    Returns Meta credentials for the current tenant.
    """
    if current_user.role != "administrator":
        raise HTTPException(status_code=403, detail="Apenas administradores podem acessar credenciais do WhatsApp.")
        
    creds = db.query(MetaCredential).filter(MetaCredential.tenant_id == current_tenant.id).first()
    if not creds:
        raise HTTPException(status_code=404, detail="Meta credentials not configured yet")
        
    # Return masked token for security
    masked_token = "••••••••"
    if creds.permanent_access_token:
        token_len = len(creds.permanent_access_token)
        if token_len > 8:
            masked_token = creds.permanent_access_token[:4] + "..." + creds.permanent_access_token[-4:]
            
    return MetaCredentialDetailsResponse(
        id=creds.id,
        tenant_id=creds.tenant_id,
        phone_number_id=creds.phone_number_id,
        waba_id=creds.waba_id,
        verify_token=creds.verify_token,
        permanent_access_token=masked_token,
        webhook_url=creds.webhook_url,
        created_at=creds.created_at
    )



@router.get("/users", response_model=List[UserResponse])
def get_hotel_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("team"))
):
    """
    Returns all registered users (staff) for the current hotel (tenant).
    Accessible by Administrators and Managers.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem acessar a lista de usuários.")
        
    query = db.query(User).filter(User.tenant_id == current_tenant.id)
    if current_user.role == "manager":
        query = query.filter(User.role != "administrator")
    return query.all()


@router.post("/users", response_model=UserResponse)
def create_hotel_user(
    payload: EmployeeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("team"))
):
    """
    Creates a new user (agent/manager) for the current hotel (tenant).
    Accessible by Administrators and Managers.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem cadastrar usuários.")
        
    # Prevent normal supervisors from registering administrators or other invalid roles
    if payload.role not in ["agent", "manager"]:
        raise HTTPException(status_code=400, detail="Cargo inválido. Apenas 'agent' (vendedor) ou 'manager' (supervisor) são permitidos.")
        
    # Check if email is already taken
    db_user = db.query(User).filter(User.email == payload.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Este email já está cadastrado.")
        
    # Create the user
    user = User(
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        name=payload.name,
        tenant_id=current_tenant.id,
        role=payload.role,
        status="offline"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_hotel_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("team"))
):
    """
    Deletes a user (staff) from the current hotel (tenant).
    Accessible by Administrators and Managers.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores e supervisores podem excluir usuários.")
        
    user_to_delete = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_tenant.id
    ).first()
    
    if not user_to_delete:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
        
    # Prevent self-deletion
    if user_to_delete.id == current_user.id:
        raise HTTPException(status_code=400, detail="Você não pode excluir o seu próprio usuário.")
        
    # Prevent manager from deleting an administrator
    if user_to_delete.role == "administrator" and current_user.role != "administrator":
        raise HTTPException(status_code=403, detail="Supervisores não podem excluir administradores.")
        
    db.delete(user_to_delete)
    db.commit()
    return {"message": "Usuário excluído com sucesso"}

