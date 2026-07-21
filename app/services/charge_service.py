from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import Tenant, BillingTransaction, Conversation, Message

# Tarifas do Q-Aura (Custo Meta + Margem de Lucro)
RATES = {
    "marketing": {
        "amount": Decimal("0.45"),
        "cost_meta": Decimal("0.35"),
        "description": "Conversa de Marketing iniciada (Meta Cloud API)"
    },
    "utility": {
        "amount": Decimal("0.15"),
        "cost_meta": Decimal("0.08"),
        "description": "Conversa de Utilidade iniciada (Meta Cloud API)"
    },
    "service": {
        "amount": Decimal("0.25"),
        "cost_meta": Decimal("0.16"),
        "description": "Conversa de Serviço iniciada (Meta Cloud API)"
    }
}

def can_initiate_conversation(db: Session, tenant_id: str) -> bool:
    """
    Verifica se a empresa possui saldo (Pré-pago) ou limite disponível (Pós-pago)
    para iniciar uma nova conversa.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return False

    if tenant.billing_mode == "prepaid":
        # Se for pré-pago, precisa ter saldo maior que zero
        return tenant.balance > Decimal("0.00")
    else:
        # Se for pós-pago, calcula o consumo do mês e valida se está dentro do limite
        first_day_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Soma transações de débito do mês corrente
        monthly_spend = db.query(BillingTransaction).filter(
            BillingTransaction.tenant_id == tenant_id,
            BillingTransaction.category.in_(["marketing", "utility", "service"]),
            BillingTransaction.created_at >= first_day_of_month
        ).with_entities(
            db.func.sum(BillingTransaction.amount)
        ).scalar() or Decimal("0.00")

        return monthly_spend < tenant.postpaid_limit

def charge_tenant_conversation(db: Session, tenant_id: str, conversation_id: str, category: str, custom_description: str = None) -> bool:
    """
    Registra o débito de uma conversa no balance da empresa ou na fatura pós-paga.
    Retorna True em caso de sucesso ou False se o débito falhar (falta de saldo).
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return False

    rate = RATES.get(category)
    if not rate:
        return False

    amount = rate["amount"]
    cost_meta = rate["cost_meta"]
    description = custom_description or rate["description"]

    # 1. Valida se o cliente tem limite/saldo
    if not can_initiate_conversation(db, tenant_id):
        return False

    # 2. Desconta o saldo em caso de pré-pago
    if tenant.billing_mode == "prepaid":
        tenant.balance -= amount

    # 3. Registra a transação no extrato financeiro
    transaction = BillingTransaction(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        category=category,
        amount=amount,
        cost_meta=cost_meta,
        description=description
    )
    db.add(transaction)
    db.commit()
    return True
