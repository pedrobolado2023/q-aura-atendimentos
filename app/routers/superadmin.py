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

import json
import uuid
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])


def _is_valid_uuid(val: str) -> bool:
    if not val or not isinstance(val, str):
        return False
    try:
        uuid.UUID(val.strip())
        return True
    except Exception:
        return False


def require_superadmin(current_user: User = Depends(get_current_user)):
    """Dependency that ensures only the superadmin can access these routes."""
    if current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso exclusivo do Superadmin.",
        )
    return current_user


def _parse_list(val) -> List[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [val]
    return []


def _get_enabled_modules(tenant: Tenant) -> List[str]:
    """Returns the effective enabled modules for a tenant (plan + custom overrides)."""
    base_modules = _parse_list(tenant.plan.modules) if tenant.plan else []
    custom = _parse_list(tenant.custom_modules)
    return list(set(base_modules + custom))


def _plan_to_response(plan: Optional[Plan]) -> Optional[dict]:
    if not plan:
        return None
    return {
        "id": str(plan.id),
        "name": plan.name,
        "description": plan.description,
        "price_monthly": float(plan.price_monthly or 0.0),
        "modules": _parse_list(plan.modules),
        "max_users": plan.max_users or 5,
        "is_active": bool(plan.is_active) if plan.is_active is not None else True,
        "created_at": plan.created_at,
    }


def _tenant_to_response(tenant: Tenant) -> dict:
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "subdomain": tenant.subdomain,
        "cnpj": tenant.cnpj,
        "segment": tenant.segment,
        "plan_type": tenant.plan_type,
        "status": tenant.status,
        "max_users": tenant.max_users,
        "custom_modules": _parse_list(tenant.custom_modules),
        "logo_url": tenant.logo_url,
        "created_at": tenant.created_at,
        "plan": _plan_to_response(tenant.plan),
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
    plans = db.query(Plan).order_by(Plan.price_monthly).all()
    return [_plan_to_response(p) for p in plans]


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
    return _plan_to_response(plan)


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
    return _plan_to_response(plan)


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
    try:
        tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
        return [_tenant_to_response(t) for t in tenants]
    except Exception as e:
        db.rollback()
        print(f"[Superadmin list_tenants error]: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar empresas: {str(e)}")


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
    try:
        subdomain = payload.subdomain.strip().lower()
        admin_email = payload.admin_email.strip().lower()

        # Check subdomain uniqueness
        existing = db.query(Tenant).filter(Tenant.subdomain == subdomain).first()
        if existing:
            # If the tenant is an unused test record (0 contacts and 0 conversations), clean it up automatically!
            if len(existing.contacts or []) == 0 and len(existing.conversations or []) == 0:
                db.delete(existing)
                db.flush()
            else:
                raise HTTPException(status_code=400, detail=f"O subdomínio '{subdomain}' já está em uso pela empresa '{existing.name}'.")

        # Check admin email uniqueness
        existing_user = db.query(User).filter(User.email == admin_email).first()
        if existing_user:
            if existing_user.tenant_id:
                user_tenant = db.query(Tenant).filter(Tenant.id == existing_user.tenant_id).first()
                if user_tenant:
                    raise HTTPException(status_code=400, detail=f"O e-mail '{admin_email}' já está em uso pela empresa '{user_tenant.name}'.")
            # Orphan user from deleted tenant - clean it up
            db.delete(existing_user)
            db.flush()

        # Resolve plan (safely by UUID or by Plan Name)
        plan = None
        plan_id = payload.plan_id.strip() if payload.plan_id and isinstance(payload.plan_id, str) and payload.plan_id.strip() else None
        if plan_id:
            if _is_valid_uuid(plan_id):
                plan = db.query(Plan).filter(Plan.id == plan_id).first()
            else:
                plan = db.query(Plan).filter(Plan.name.ilike(plan_id)).first()

        # Create tenant
        tenant = Tenant(
            name=payload.name.strip(),
            subdomain=subdomain,
            cnpj=payload.cnpj.strip() if payload.cnpj else None,
            segment=payload.segment or "hotel",
            plan_id=plan.id if plan else None,
            plan_type=plan.name if plan else "custom",
            status="active",
            max_users=payload.max_users or (plan.max_users if plan else 5),
            custom_modules=[],
        )
        db.add(tenant)
        db.flush()  # get tenant.id before creating user

        # Create admin user for the tenant
        admin_user = User(
            email=admin_email,
            password_hash=get_password_hash(payload.admin_password),
            name=payload.admin_name.strip(),
            tenant_id=tenant.id,
            role="administrator",
            status="offline",
        )
        db.add(admin_user)

        # Create default BotConfig for the tenant
        try:
            from app.models import BotConfig
            bot_conf = db.query(BotConfig).filter(BotConfig.tenant_id == tenant.id).first()
            if not bot_conf:
                bot_conf = BotConfig(
                    tenant_id=tenant.id,
                    is_active=True,
                    welcome_message="Olá! Seja bem-vindo ao nosso hotel. Como posso ajudar você hoje?",
                    fallback_message="Desculpe, não consegui entender. Digite *Atendente* a qualquer momento para falar com um humano.",
                    out_of_hours_message="Olá! Nosso horário de atendimento é das 08h às 22h. Deixe sua mensagem que responderemos o mais breve possível.",
                    transfer_keywords="atendente,humano,falar,suporte,ajuda"
                )
                db.add(bot_conf)
        except Exception as bot_err:
            print(f"[Warning]: Failed to create BotConfig: {bot_err}")

        db.commit()
        db.refresh(tenant)
        return _tenant_to_response(tenant)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as ie:
        db.rollback()
        print(f"[IntegrityError in create_tenant]: {ie}")
        raise HTTPException(status_code=400, detail="Subdomínio ou E-mail do administrador já cadastrado no sistema.")
    except Exception as e:
        db.rollback()
        print(f"[Superadmin create_tenant error]: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao criar empresa: {str(e)}")


@router.put("/tenants/{tenant_id}", response_model=TenantDetailResponse)
def update_tenant(
    tenant_id: str,
    payload: SuperadminTenantUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Empresa não encontrada.")

        update_data = payload.dict(exclude_none=True)

        # If plan changed, update plan_type label too
        if "plan_id" in update_data:
            pid = update_data["plan_id"]
            if pid and isinstance(pid, str) and pid.strip():
                plan = db.query(Plan).filter(Plan.id == pid.strip()).first()
                if plan:
                    tenant.plan_type = plan.name
                    update_data["plan_id"] = plan.id
                else:
                    update_data["plan_id"] = None
                    tenant.plan_type = "custom"
            else:
                update_data["plan_id"] = None
                tenant.plan_type = "custom"

        for field, value in update_data.items():
            setattr(tenant, field, value)

        db.commit()
        db.refresh(tenant)
        return _tenant_to_response(tenant)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"[Superadmin update_tenant error]: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar empresa: {str(e)}")


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

