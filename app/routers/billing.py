from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from decimal import Decimal
from datetime import datetime, timezone
from app.database import get_db
from app.models import Tenant, BillingTransaction, User
from app.auth import get_current_user, get_current_tenant, ModuleRequired

router = APIRouter(prefix="/api/billing", tags=["billing"])

class BillingSummaryResponse(BaseModel):
    billing_mode: str
    balance: float
    postpaid_limit: float
    monthly_spend: float

class TransactionResponse(BaseModel):
    id: str
    category: str
    amount: float
    description: Optional[str]
    created_at: str

    class Config:
        from_attributes = True

class ChangeBillingModeRequest(BaseModel):
    billing_mode: str

@router.get("/summary", response_model=BillingSummaryResponse)
def get_billing_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings")) # Acesso restrito a quem tem módulo admin
):
    tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    # Calcula os gastos do mês atual
    first_day = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_spend = db.query(BillingTransaction).filter(
        BillingTransaction.tenant_id == tenant.id,
        BillingTransaction.category.in_(["marketing", "utility", "service"]),
        BillingTransaction.created_at >= first_day
    ).with_entities(
        db.func.sum(BillingTransaction.amount)
    ).scalar() or Decimal("0.00")

    return BillingSummaryResponse(
        billing_mode=tenant.billing_mode or "prepaid",
        balance=float(tenant.balance or 0.0),
        postpaid_limit=float(tenant.postpaid_limit or 100.0),
        monthly_spend=float(monthly_spend)
    )

@router.get("/transactions", response_model=List[TransactionResponse])
def get_billing_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings"))
):
    txs = db.query(BillingTransaction).filter(
        BillingTransaction.tenant_id == current_tenant.id
    ).order_by(BillingTransaction.created_at.desc()).limit(100).all()

    results = []
    for t in txs:
        results.append(TransactionResponse(
            id=str(t.id),
            category=t.category,
            amount=float(t.amount),
            description=t.description,
            created_at=t.created_at.isoformat() if t.created_at else ""
        ))
    return results

@router.post("/mode")
def change_billing_mode(
    payload: ChangeBillingModeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings"))
):
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores podem alterar o método de faturamento.")

    if payload.billing_mode not in ["prepaid", "postpaid"]:
        raise HTTPException(status_code=400, detail="Método de faturamento inválido.")

    tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    tenant.billing_mode = payload.billing_mode
    db.commit()
    return {"status": "success", "billing_mode": tenant.billing_mode}

@router.post("/recharge")
def simulate_recharge(
    amount: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("meta_settings"))
):
    """
    Simula uma recarga de créditos pré-pagos via Pix no sandbox.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores podem efetuar recargas.")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="O valor da recarga deve ser maior que zero.")

    tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    # Atualiza o saldo
    tenant.balance = (tenant.balance or Decimal("0.00")) + Decimal(str(amount))
    
    # Registra a recarga no extrato
    tx = BillingTransaction(
        tenant_id=tenant.id,
        category="recharge",
        amount=Decimal(str(amount)),
        cost_meta=Decimal("0.00"),
        description=f"Recarga de créditos via PIX efetuada por {current_user.name}"
    )
    db.add(tx)
    db.commit()
    
    return {"status": "success", "new_balance": float(tenant.balance)}
