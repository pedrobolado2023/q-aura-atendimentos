from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import Tenant, User, Plan, PricingConfig
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


def _normalize_plan_type(plan_name: Optional[str]) -> str:
    if not plan_name:
        return "custom"
    name_clean = str(plan_name).lower().strip()
    if "pro" in name_clean:
        return "pro"
    if "enterp" in name_clean:
        return "enterprise"
    if "basic" in name_clean or "básico" in name_clean or "basico" in name_clean:
        return "basic"
    if "free" in name_clean or "gratis" in name_clean or "grátis" in name_clean:
        return "free"
    return "custom"


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

        # Resolve plan (safely by UUID or by Plan Name)
        plan = None
        plan_id = payload.plan_id.strip() if payload.plan_id and isinstance(payload.plan_id, str) and payload.plan_id.strip() else None
        if plan_id:
            if _is_valid_uuid(plan_id):
                plan = db.query(Plan).filter(Plan.id == plan_id).first()
            else:
                plan = db.query(Plan).filter(Plan.name.ilike(plan_id)).first()

        # Create or Update tenant (Upsert mode for superadmin)
        tenant = db.query(Tenant).filter(Tenant.subdomain == subdomain).first()
        if tenant:
            tenant.name = payload.name.strip()
            tenant.cnpj = payload.cnpj.strip() if payload.cnpj else None
            tenant.segment = payload.segment or "hotel"
            tenant.plan_id = plan.id if plan else tenant.plan_id
            tenant.plan_type = _normalize_plan_type(plan.name) if plan else (tenant.plan_type or "custom")
            tenant.status = "active"
            tenant.max_users = payload.max_users or (plan.max_users if plan else 5)
        else:
            tenant = Tenant(
                name=payload.name.strip(),
                subdomain=subdomain,
                cnpj=payload.cnpj.strip() if payload.cnpj else None,
                segment=payload.segment or "hotel",
                plan_id=plan.id if plan else None,
                plan_type=_normalize_plan_type(plan.name) if plan else "custom",
                status="active",
                max_users=payload.max_users or (plan.max_users if plan else 5),
                custom_modules=[],
            )
            db.add(tenant)

        db.flush()  # get tenant.id before creating/updating user

        # Create or Update admin user for the tenant (Upsert mode)
        admin_user = db.query(User).filter(User.email == admin_email).first()
        if admin_user:
            admin_user.name = payload.admin_name.strip()
            admin_user.password_hash = get_password_hash(payload.admin_password)
            admin_user.tenant_id = tenant.id
            admin_user.role = "administrator"
        else:
            admin_user = User(
                email=admin_email,
                password_hash=get_password_hash(payload.admin_password),
                name=payload.admin_name.strip(),
                tenant_id=tenant.id,
                role="administrator",
                status="offline",
            )
            db.add(admin_user)

        # Create default BotConfig for the tenant if missing
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
    except Exception as e:
        db.rollback()
        print(f"[Superadmin create_tenant error]: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao salvar empresa: {str(e)}")


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
                    tenant.plan_type = _normalize_plan_type(plan.name)
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
    
    tenant_name = tenant.name
    tenant_id_str = str(tenant.id)
    
    try:
        from sqlalchemy import text
        # 1. Mensagens e Conversas
        db.execute(text("""
            DELETE FROM qa_messages WHERE conversation_id IN (
                SELECT id FROM qa_conversations WHERE tenant_id = :tid
            )
        """), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_conversations WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 2. Contatos
        db.execute(text("DELETE FROM qa_contacts WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 3. Campanhas e Destinatários
        db.execute(text("""
            DELETE FROM qa_campaign_recipients WHERE campaign_id IN (
                SELECT id FROM qa_marketing_campaigns WHERE tenant_id = :tid
            )
        """), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_marketing_campaigns WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 4. Departamentos e Agentes vinculados
        db.execute(text("""
            DELETE FROM qa_agents_departments WHERE department_id IN (
                SELECT id FROM qa_departments WHERE tenant_id = :tid
            ) OR user_id IN (
                SELECT id FROM qa_users WHERE tenant_id = :tid
            )
        """), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_departments WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 5. Usuários
        db.execute(text("DELETE FROM qa_users WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 6. Credenciais Meta, Bot, Templates, Respostas Rápidas, Cobrança, Webhooks
        db.execute(text("DELETE FROM qa_meta_credentials WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_bot_configs WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_quick_messages WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        try:
            db.execute(text("DELETE FROM qa_message_templates WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        except Exception:
            pass
        db.execute(text("DELETE FROM qa_billing_transactions WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        db.execute(text("DELETE FROM qa_webhook_events WHERE tenant_id = :tid"), {"tid": tenant_id_str})
        
        # 7. Por fim, remove a Empresa
        db.execute(text("DELETE FROM qa_tenants WHERE id = :tid"), {"tid": tenant_id_str})
        db.commit()
        
        return {"message": f"Empresa '{tenant_name}' e todos os seus dados foram excluídos com sucesso."}
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir empresa: {str(e)}")


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


# ─── Pricing Config (Tabela de preços global) ────────────────────────────────

from app.services.charge_service import _DEFAULT_RATES

class PricingUpdateItem(BaseModel):
    category: str
    price_tenant: float
    cost_meta: float
    label: Optional[str] = None

class PricingUpdateRequest(BaseModel):
    items: List[PricingUpdateItem]


def _ensure_pricing_defaults(db: Session):
    """Inicializa a tabela de preços com os valores padrão se estiver vazia."""
    existing = db.query(PricingConfig).count()
    if existing == 0:
        for cat, vals in _DEFAULT_RATES.items():
            db.add(PricingConfig(
                category=cat,
                price_tenant=vals["price_tenant"],
                cost_meta=vals["cost_meta"],
                label=vals.get("label", cat.capitalize()),
            ))
        db.commit()


@router.get("/pricing")
def get_pricing(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """
    Retorna a tabela de preços atual (visível somente ao Superadmin).
    price_tenant = valor cobrado do cliente
    cost_meta    = custo real da Meta (nunca exposto ao cliente)
    """
    _ensure_pricing_defaults(db)
    rows = db.query(PricingConfig).order_by(PricingConfig.id).all()
    return [
        {
            "id": row.id,
            "category": row.category,
            "label": row.label or row.category.capitalize(),
            "price_tenant": float(row.price_tenant),
            "cost_meta": float(row.cost_meta),
            "margin": round(float(row.price_tenant) - float(row.cost_meta), 4),
            "margin_pct": round(
                ((float(row.price_tenant) - float(row.cost_meta)) / float(row.cost_meta) * 100)
                if float(row.cost_meta) > 0 else 0,
                1
            ),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


@router.put("/pricing")
def update_pricing(
    payload: PricingUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """
    Atualiza os preços cobrados por categoria de conversa.
    Somente o Superadmin pode alterar esses valores.
    O cliente NUNCA tem acesso a este endpoint nem ao custo real da Meta.
    """
    _ensure_pricing_defaults(db)

    updated = []
    for item in payload.items:
        if item.price_tenant < 0 or item.cost_meta < 0:
            raise HTTPException(status_code=400, detail=f"Valores negativos não são permitidos para '{item.category}'.")
        if item.price_tenant < item.cost_meta:
            raise HTTPException(
                status_code=400,
                detail=f"O preço cobrado ({item.price_tenant}) não pode ser menor que o custo Meta ({item.cost_meta}) para '{item.category}'."
            )

        row = db.query(PricingConfig).filter(PricingConfig.category == item.category).first()
        if row:
            row.price_tenant = item.price_tenant
            row.cost_meta = item.cost_meta
            if item.label:
                row.label = item.label
        else:
            db.add(PricingConfig(
                category=item.category,
                price_tenant=item.price_tenant,
                cost_meta=item.cost_meta,
                label=item.label or item.category.capitalize(),
            ))
        updated.append(item.category)

    db.commit()
    return {"message": f"Preços atualizados com sucesso para: {', '.join(updated)}.", "updated": updated}
