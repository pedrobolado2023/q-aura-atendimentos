from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import Tenant, User, Plan
from app.schemas import (
    SuperadminTenantCreate,
    SuperadminTenantUpdate,
    TenantDetailResponse,
    PlanCreate,
    PlanUpdate,
    PlanResponse,
)
from app.auth import get_password_hash, get_current_user

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])


def require_superadmin(current_user: User = Depends(get_current_user)):
    """Dependency that ensures only the superadmin can access these routes."""
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso exclusivo do Superadmin.",
        )
    return current_user


def _get_enabled_modules(tenant: Tenant) -> List[str]:
    """Returns the effective enabled modules for a tenant (plan + custom overrides)."""
    base_modules = list(tenant.plan.modules or []) if tenant.plan else []
    custom = list(tenant.custom_modules or [])
    return list(set(base_modules + custom))


def _tenant_to_response(tenant: Tenant) -> dict:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "subdomain": tenant.subdomain,
        "cnpj": tenant.cnpj,
        "segment": tenant.segment,
        "plan_type": tenant.plan_type,
        "status": tenant.status,
        "max_users": tenant.max_users,
        "custom_modules": tenant.custom_modules or [],
        "logo_url": tenant.logo_url,
        "created_at": tenant.created_at,
        "plan": tenant.plan,
        "enabled_modules": _get_enabled_modules(tenant),
        "billing_mode": tenant.billing_mode or "prepaid",
        "balance": float(tenant.balance or 0.0),
        "postpaid_limit": float(tenant.postpaid_limit or 100.0),
    }


# ─── Dashboard Global ─────────────────────────────────────────────────────────

@router.get("/dashboard")
def superadmin_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Global metrics for the Superadmin panel."""
    total_tenants = db.query(Tenant).count()
    active_tenants = db.query(Tenant).filter(Tenant.status == "active").count()
    suspended_tenants = db.query(Tenant).filter(Tenant.status == "suspended").count()
    trial_tenants = db.query(Tenant).filter(Tenant.status == "trial").count()
    total_users = db.query(User).filter(User.role != "superadmin").count()
    total_plans = db.query(Plan).count()

    return {
        "total_tenants": total_tenants,
        "active_tenants": active_tenants,
        "suspended_tenants": suspended_tenants,
        "trial_tenants": trial_tenants,
        "total_users": total_users,
        "total_plans": total_plans,
    }


# ─── Plans CRUD ───────────────────────────────────────────────────────────────

@router.get("/plans", response_model=List[PlanResponse])
def list_plans(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    return db.query(Plan).order_by(Plan.price_monthly).all()


@router.post("/plans", response_model=PlanResponse)
def create_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    plan = Plan(
        name=payload.name,
        description=payload.description,
        price_monthly=payload.price_monthly,
        modules=payload.modules or [],
        max_users=payload.max_users or 5,
        is_active=payload.is_active if payload.is_active is not None else True,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.put("/plans/{plan_id}", response_model=PlanResponse)
def update_plan(
    plan_id: str,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    for field, value in payload.dict(exclude_none=True).items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    plan = db.query(Plan).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    db.delete(plan)
    db.commit()
    return {"message": "Plano excluído com sucesso."}


# ─── Tenants (Companies) CRUD ─────────────────────────────────────────────────

@router.get("/tenants", response_model=List[TenantDetailResponse])
def list_tenants(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return [_tenant_to_response(t) for t in tenants]


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
def get_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return _tenant_to_response(tenant)


@router.post("/tenants", response_model=TenantDetailResponse)
def create_tenant(
    payload: SuperadminTenantCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    # Check subdomain uniqueness
    existing = db.query(Tenant).filter(Tenant.subdomain == payload.subdomain).first()
    if existing:
        raise HTTPException(status_code=400, detail="Subdomínio já cadastrado.")

    # Check admin email uniqueness
    existing_user = db.query(User).filter(User.email == payload.admin_email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email do administrador já cadastrado.")

    # Resolve plan
    plan = None
    if payload.plan_id:
        plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plano não encontrado.")

    # Create tenant
    tenant = Tenant(
        name=payload.name,
        subdomain=payload.subdomain,
        cnpj=payload.cnpj,
        segment=payload.segment or "hotel",
        plan_id=payload.plan_id,
        plan_type=plan.name if plan else "custom",
        status="active",
        max_users=payload.max_users or (plan.max_users if plan else 5),
        custom_modules=[],
    )
    db.add(tenant)
    db.flush()  # get tenant.id before creating user

    # Create admin user for the tenant
    admin_user = User(
        email=payload.admin_email,
        password_hash=get_password_hash(payload.admin_password),
        name=payload.admin_name,
        tenant_id=tenant.id,
        role="administrator",
        status="offline",
    )
    db.add(admin_user)
    db.commit()
    db.refresh(tenant)
    return _tenant_to_response(tenant)


@router.put("/tenants/{tenant_id}", response_model=TenantDetailResponse)
def update_tenant(
    tenant_id: str,
    payload: SuperadminTenantUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    update_data = payload.dict(exclude_none=True)

    # If plan changed, update plan_type label too
    if "plan_id" in update_data:
        plan = db.query(Plan).filter(Plan.id == update_data["plan_id"]).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plano não encontrado.")
        tenant.plan_type = plan.name

    for field, value in update_data.items():
        setattr(tenant, field, value)

    db.commit()
    db.refresh(tenant)
    return _tenant_to_response(tenant)


@router.post("/tenants/{tenant_id}/suspend")
def suspend_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    tenant.status = "suspended"
    db.commit()
    return {"message": f"Empresa '{tenant.name}' suspensa com sucesso."}


@router.post("/tenants/{tenant_id}/activate")
def activate_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    tenant.status = "active"
    db.commit()
    return {"message": f"Empresa '{tenant.name}' reativada com sucesso."}


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    db.delete(tenant)
    db.commit()
    return {"message": f"Empresa '{tenant.name}' excluída com sucesso."}


# ─── Tenant Users (view only) ─────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/users")
def get_tenant_users(
    tenant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    users = db.query(User).filter(User.tenant_id == tenant_id).all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role,
            "status": u.status,
            "created_at": u.created_at,
        }
        for u in users
    ]


# ─── Superadmin Billing Overrides ─────────────────────────────────────────────
from pydantic import BaseModel
from decimal import Decimal
from app.models import BillingTransaction

class SuperadminAddBalanceRequest(BaseModel):
    amount: float

class SuperadminSetLimitRequest(BaseModel):
    limit: float

class SuperadminSetModeRequest(BaseModel):
    billing_mode: str

@router.post("/tenants/{tenant_id}/add-balance")
def superadmin_add_balance(
    tenant_id: str,
    payload: SuperadminAddBalanceRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    tenant.balance = (tenant.balance or Decimal("0.00")) + Decimal(str(payload.amount))
    
    tx = BillingTransaction(
        tenant_id=tenant.id,
        category="recharge",
        amount=Decimal(str(payload.amount)),
        cost_meta=Decimal("0.00"),
        description="Crédito manual injetado pelo Superadmin"
    )
    db.add(tx)
    db.commit()
    return {"message": "Saldo adicionado com sucesso.", "new_balance": float(tenant.balance)}

@router.post("/tenants/{tenant_id}/set-limit")
def superadmin_set_limit(
    tenant_id: str,
    payload: SuperadminSetLimitRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    tenant.postpaid_limit = Decimal(str(payload.limit))
    db.commit()
    return {"message": "Limite pós-pago atualizado com sucesso.", "new_limit": float(tenant.postpaid_limit)}

@router.post("/tenants/{tenant_id}/set-billing-mode")
def superadmin_set_billing_mode(
    tenant_id: str,
    payload: SuperadminSetModeRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    if payload.billing_mode not in ["prepaid", "postpaid"]:
        raise HTTPException(status_code=400, detail="Modo de faturamento inválido.")
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    tenant.billing_mode = payload.billing_mode
    db.commit()
    return {"message": "Método de faturamento atualizado com sucesso.", "billing_mode": tenant.billing_mode}

