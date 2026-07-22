import httpx
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.database import get_db, SessionLocal
from app.models import Tenant, BillingTransaction, User
from app.auth import get_current_user, get_current_tenant, ModuleRequired
from app.config import settings

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
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    if current_user.role not in ["administrator", "manager", "superadmin"]:
        raise HTTPException(status_code=403, detail="Acesso não autorizado. Apenas supervisores e administradores podem visualizar dados de faturamento.")

    tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

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
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    if current_user.role not in ["administrator", "manager", "superadmin"]:
        raise HTTPException(status_code=403, detail="Acesso não autorizado. Apenas supervisores e administradores podem visualizar o histórico financeiro.")

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
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
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


# --- INTEGRAÇÃO REAL MERCADO PAGO ---

@router.post("/recharge")
async def create_mp_pix_recharge(
    amount: float,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Cria uma cobrança real PIX no Mercado Pago para adicionar saldo pré-pago.
    """
    if current_user.role not in ["administrator", "manager"]:
        raise HTTPException(status_code=403, detail="Apenas administradores podem efetuar recargas.")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="O valor da recarga deve ser maior que zero.")

    tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    # Gera expiração de 15 minutos
    expiration_date = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    idempotency_key = str(uuid.uuid4())

    payload = {
        "transaction_amount": float(round(amount, 2)),
        "payment_method_id": "pix",
        "description": f"Recarga de créditos Q-Aura - {tenant.name}",
        "date_of_expiration": expiration_date,
        "payer": {
            "email": current_user.email,
            "first_name": current_user.name.split(" ")[0] if current_user.name else "Cliente",
            "last_name": "SaaS",
            "identification": {
                "type": "CPF",
                "number": "00000000000"
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("https://api.mercadopago.com/v1/payments", json=payload, headers=headers)
            mp_data = response.json()
            
            if response.status_code != 201:
                print(f"[MP Error] Status {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=400,
                    detail=mp_data.get("message", "Erro ao criar pagamento Pix no Mercado Pago")
                )

            # Extração dos dados do Pix
            tx_data = mp_data.get("point_of_interaction", {}).get("transaction_data", {})
            payment_id = str(mp_data.get("id"))
            qr_code = tx_data.get("qr_code")
            qr_code_base64 = tx_data.get("qr_code_base64")

            # Salva a transação temporária com status 'pending' (como débito/crédito zerado até ser aprovado)
            # Para não inflar o saldo antes do pagamento, criamos uma transação com categoria 'recharge_pending'
            tx = BillingTransaction(
                id=payment_id, # Usamos o próprio ID de pagamento do MP como chave primária do extrato temporário
                tenant_id=tenant.id,
                category="recharge_pending",
                amount=Decimal(str(amount)),
                cost_meta=Decimal("0.00"),
                description=f"PIX Gerado: Recarga pendente de R$ {amount:.2f} (ID MP: {payment_id})"
            )
            db.add(tx)
            db.commit()

            return {
                "success": True,
                "paymentId": payment_id,
                "qrCode": qr_code,
                "qrCodeBase64": qr_code_base64,
                "status": mp_data.get("status"),
                "expiresAt": mp_data.get("date_of_expiration")
            }

        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Erro de conexão com o Mercado Pago: {str(e)}")


@router.get("/recharge/status/{payment_id}")
def check_recharge_status(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(ModuleRequired("inbox"))
):
    """
    Consulta o status de um pagamento Pix de recarga.
    """
    tx = db.query(BillingTransaction).filter(
        BillingTransaction.id == payment_id,
        BillingTransaction.tenant_id == current_tenant.id
    ).first()
    
    if not tx:
        raise HTTPException(status_code=404, detail="Transação de recarga não encontrada.")

    # Se já foi aprovada localmente, retorna
    if tx.category == "recharge":
        return {"status": "approved"}

    # Consulta na API do Mercado Pago
    headers = {
        "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}"
    }

    try:
        response = httpx.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers)
        if response.status_code != 200:
            return {"status": "pending"}

        mp_data = response.json()
        mp_status = mp_data.get("status")

        if mp_status == "approved":
            # Atualiza o saldo e altera a categoria de recharge_pending para recharge oficial
            tenant = db.query(Tenant).filter(Tenant.id == current_tenant.id).first()
            if tenant and tx.category == "recharge_pending":
                tenant.balance = (tenant.balance or Decimal("0.00")) + tx.amount
                tx.category = "recharge"
                tx.description = f"Recarga de créditos via PIX aprovada (ID MP: {payment_id})"
                db.commit()
                return {"status": "approved"}

        return {"status": mp_status}
    except Exception:
        return {"status": "pending"}


@router.post("/webhook/mercadopago")
async def mercadopago_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Webhook público para receber as notificações do Mercado Pago e liberar saldo Pix instantaneamente.
    """
    try:
        body = await request.json()
    except Exception:
        return Response(content="Invalid JSON", status_code=400)

    # Respondemos 200/201 imediatamente para evitar reenvio
    response_ok = Response(content="OK", status_code=200)

    event_type = body.get("type")
    data_id = body.get("data", {}).get("id")

    if event_type == "payment" and data_id:
        # Consulta o pagamento no MP
        headers = {
            "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}"
        }
        try:
            res = httpx.get(f"https://api.mercadopago.com/v1/payments/{data_id}", headers=headers)
            if res.status_code == 200:
                payment_data = res.json()
                mp_status = payment_data.get("status")

                if mp_status == "approved":
                    # Busca a transação pendente correspondente no nosso BD
                    tx = db.query(BillingTransaction).filter(
                        BillingTransaction.id == str(data_id),
                        BillingTransaction.category == "recharge_pending"
                    ).first()

                    if tx:
                        tenant = db.query(Tenant).filter(Tenant.id == tx.tenant_id).first()
                        if tenant:
                            tenant.balance = (tenant.balance or Decimal("0.00")) + tx.amount
                            tx.category = "recharge"
                            tx.description = f"Recarga de créditos via PIX aprovada via Webhook (ID MP: {data_id})"
                            db.commit()
                            print(f"[Webhook MP] Saldo de R$ {tx.amount} liberado para tenant {tenant.name}")
        except Exception as e:
            print(f"[Webhook MP Error] Falha ao processar pagamento {data_id}: {e}")

    return response_ok
