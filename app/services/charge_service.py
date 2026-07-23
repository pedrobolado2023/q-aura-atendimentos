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
    try:
        tenant_id_str = str(tenant_id)
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_str).first()
        if not tenant:
            return False

        balance = Decimal(str(tenant.balance if tenant.balance is not None else 0.0))
        postpaid_limit = Decimal(str(tenant.postpaid_limit if tenant.postpaid_limit is not None else 100.0))
        mode = tenant.billing_mode or "prepaid"

        if mode == "prepaid":
            # Se for pré-pago, precisa ter saldo maior que zero
            return balance > Decimal("0.00")
        else:
            # Se for pós-pago, calcula o consumo do mês e valida se está dentro do limite
            first_day_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            try:
                monthly_spend = db.query(BillingTransaction).filter(
                    BillingTransaction.tenant_id == tenant_id_str,
                    BillingTransaction.category.in_(["marketing", "utility", "service"]),
                    BillingTransaction.created_at >= first_day_of_month
                ).with_entities(
                    db.func.sum(BillingTransaction.amount)
                ).scalar()
                monthly_spend = Decimal(str(monthly_spend)) if monthly_spend is not None else Decimal("0.00")
            except Exception:
                monthly_spend = Decimal("0.00")

            return monthly_spend < postpaid_limit
    except Exception as e:
        print(f"[can_initiate_conversation] Error: {e}")
        # Retorna True em caso de falha de leitura de saldo para não bloquear a operação por falha técnica
        return True

def charge_tenant_conversation(db: Session, tenant_id: str, conversation_id: str, category: str, custom_description: str = None) -> bool:
    """
    Registra o débito de uma conversa no balance da empresa ou na fatura pós-paga.
    Retorna True em caso de sucesso ou False se o débito falhar (falta de saldo).
    """
    try:
        tenant_id_str = str(tenant_id)
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_str).first()
        if not tenant:
            return False

        rate = RATES.get(category)
        if not rate:
            return False

        amount = rate["amount"]
        cost_meta = rate["cost_meta"]
        description = custom_description or rate["description"]

        # 1. Valida se o cliente tem limite/saldo
        if not can_initiate_conversation(db, tenant_id_str):
            return False

        # 2. Desconta o saldo em caso de pré-pago
        mode = tenant.billing_mode or "prepaid"
        if mode == "prepaid":
            current_bal = Decimal(str(tenant.balance if tenant.balance is not None else 0.0))
            tenant.balance = current_bal - amount

        # 3. Registra a transação no extrato financeiro
        transaction = BillingTransaction(
            tenant_id=tenant_id_str,
            conversation_id=str(conversation_id) if conversation_id else None,
            category=category,
            amount=amount,
            cost_meta=cost_meta,
            description=description
        )
        db.add(transaction)
        db.commit()
        return True
    except Exception as e:
        print(f"[charge_tenant_conversation] Error: {e}")
        db.rollback()
        return False

