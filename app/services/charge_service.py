from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import Tenant, BillingTransaction, Conversation, Message, PricingConfig

# Valores-padrão hardcoded usados como fallback se a tabela estiver vazia
_DEFAULT_RATES = {
    "marketing": {
        "price_tenant": Decimal("0.45"),
        "cost_meta":    Decimal("0.35"),
        "label":        "Conversa de Marketing",
    },
    "utility": {
        "price_tenant": Decimal("0.15"),
        "cost_meta":    Decimal("0.08"),
        "label":        "Conversa de Utilidade",
    },
    "service": {
        "price_tenant": Decimal("0.25"),
        "cost_meta":    Decimal("0.16"),
        "label":        "Conversa de Serviço",
    },
}


def get_rates(db: Session) -> dict:
    """
    Carrega as tarifas de precificação do banco (tabela qa_pricing_config).
    Se não houver registros, insere os valores padrão e retorna eles.
    O campo price_tenant é o que o cliente vê; cost_meta fica oculto.
    """
    try:
        rows = db.query(PricingConfig).all()

        if not rows:
            # Primeira execução: inicializa a tabela com os valores padrão
            for cat, vals in _DEFAULT_RATES.items():
                db.add(PricingConfig(
                    category=cat,
                    price_tenant=vals["price_tenant"],
                    cost_meta=vals["cost_meta"],
                    label=vals["label"],
                ))
            db.commit()
            rows = db.query(PricingConfig).all()

        return {
            row.category: {
                "price_tenant": Decimal(str(row.price_tenant)),
                "cost_meta":    Decimal(str(row.cost_meta)),
                "label":        row.label or row.category.capitalize(),
            }
            for row in rows
        }
    except Exception as e:
        print(f"[get_rates] Erro ao carregar preços do banco, usando fallback: {e}")
        return _DEFAULT_RATES


def can_initiate_conversation(db: Session, tenant_id: str) -> bool:
    """
    Verifica se a empresa possui saldo (Pré-pago) ou limite disponível (Pós-pago)
    para iniciar uma nova conversa.
    Tenants sem billing_mode definido (novos/trial) têm acesso liberado.
    """
    try:
        tenant_id_str = str(tenant_id)
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_str).first()
        if not tenant:
            return False

        mode = tenant.billing_mode or "prepaid"

        # Novos tenants sem plano formal: acesso liberado (modo trial/grace)
        if tenant.plan_id is None:
            return True

        balance = Decimal(str(tenant.balance if tenant.balance is not None else 0.0))
        postpaid_limit = Decimal(str(tenant.postpaid_limit if tenant.postpaid_limit is not None else 100.0))

        if mode == "prepaid":
            return balance > Decimal("0.00")
        else:
            first_day_of_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            try:
                monthly_spend = db.query(BillingTransaction).filter(
                    BillingTransaction.tenant_id == tenant_id_str,
                    BillingTransaction.category.in_(["marketing", "utility", "service"]),
                    BillingTransaction.created_at >= first_day_of_month
                ).with_entities(
                    func.sum(BillingTransaction.amount)
                ).scalar()
                monthly_spend = Decimal(str(monthly_spend)) if monthly_spend is not None else Decimal("0.00")
            except Exception:
                monthly_spend = Decimal("0.00")

            return monthly_spend < postpaid_limit
    except Exception as e:
        print(f"[can_initiate_conversation] Error: {e}")
        return True


def charge_tenant_conversation(db: Session, tenant_id: str, conversation_id: str, category: str, custom_description: str = None) -> bool:
    """
    Registra o débito de uma conversa no balance da empresa ou na fatura pós-paga.
    Usa os preços definidos pelo Superadmin na tabela qa_pricing_config.
    O campo amount é o preço que o cliente vê; cost_meta é o custo real da Meta.
    Retorna True em caso de sucesso ou False se o débito falhar.
    """
    try:
        tenant_id_str = str(tenant_id)
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id_str).first()
        if not tenant:
            return False

        # Carrega tarifas atualizadas do banco
        rates = get_rates(db)
        rate = rates.get(category)
        if not rate:
            return False

        amount    = rate["price_tenant"]   # valor cobrado do cliente
        cost_meta = rate["cost_meta"]      # custo real da Meta (interno)
        label     = rate.get("label", category.capitalize())
        description = custom_description or f"{label} iniciada (Meta Cloud API)"

        # Valida se o cliente tem limite/saldo
        if not can_initiate_conversation(db, tenant_id_str):
            return False

        # Desconta o saldo em caso de pré-pago
        mode = tenant.billing_mode or "prepaid"
        if mode == "prepaid":
            current_bal = Decimal(str(tenant.balance if tenant.balance is not None else 0.0))
            tenant.balance = current_bal - amount

        # Registra a transação no extrato financeiro
        transaction = BillingTransaction(
            tenant_id=tenant_id_str,
            conversation_id=str(conversation_id) if conversation_id else None,
            category=category,
            amount=amount,        # o cliente vê este valor
            cost_meta=cost_meta,  # custo real (oculto)
            description=description
        )
        db.add(transaction)
        db.commit()
        return True
    except Exception as e:
        print(f"[charge_tenant_conversation] Error: {e}")
        db.rollback()
        return False
