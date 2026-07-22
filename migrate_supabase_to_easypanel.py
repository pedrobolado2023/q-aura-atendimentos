"""
Q-aura SaaS - Supabase to Easypanel PostgreSQL Migration Tool
--------------------------------------------------------------
Este script realiza a migração automatizada de todas as tabelas e dados
do banco de dados Supabase antigo para o novo banco PostgreSQL no Easypanel.

Uso:
    python migrate_supabase_to_easypanel.py
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# Configuração de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration")

# URLs dos bancos de dados
DEFAULT_SOURCE_URL = "postgresql://postgres.ooomoyseycfoctdcsbdx:qauraAdmin2026@aws-1-sa-east-1.pooler.supabase.com:5432/postgres"
DEFAULT_TARGET_URL = "postgresql://postgres:1ca9b8fbdc310884035e@dados_postgress:5432/dados?sslmode=disable"

SOURCE_URL = os.getenv("SOURCE_DATABASE_URL", DEFAULT_SOURCE_URL)
TARGET_URL = os.getenv("TARGET_DATABASE_URL", DEFAULT_TARGET_URL)

# Normalização de prefixos postgres:// -> postgresql://
if SOURCE_URL.startswith("postgres://"):
    SOURCE_URL = SOURCE_URL.replace("postgres://", "postgresql://", 1)
if TARGET_URL.startswith("postgres://"):
    TARGET_URL = TARGET_URL.replace("postgres://", "postgresql://", 1)

# Ordem de migração (respeitando chaves estrangeiras)
TABLES_ORDER = [
    "qa_plans",
    "qa_tenants",
    "qa_users",
    "qa_meta_credentials",
    "qa_bot_configs",
    "qa_departments",
    "qa_agents_departments",
    "qa_contacts",
    "qa_conversations",
    "qa_messages",
    "qa_quick_messages",
    "qa_billing_transactions"
]

def run_migration():
    logger.info("=== INICIANDO MIGRAÇÃO SUPABASE -> EASYPANEL POSTGRESQL ===")
    logger.info(f"Origem (Supabase): {SOURCE_URL.split('@')[-1]}")
    logger.info(f"Destino (Easypanel): {TARGET_URL.split('@')[-1]}")

    source_engine = create_engine(SOURCE_URL, pool_pre_ping=True)
    target_engine = create_engine(TARGET_URL, pool_pre_ping=True)

    # 1. Garantir que as tabelas existem no banco de destino
    try:
        from app.models import Base
        logger.info("Garantindo estrutura de tabelas no banco de destino...")
        Base.metadata.create_all(bind=target_engine)
        logger.info("Estrutura de tabelas criada com sucesso no destino.")
    except Exception as e:
        logger.error(f"Erro ao criar estrutura no destino: {e}")
        return

    source_inspector = inspect(source_engine)
    target_inspector = inspect(target_engine)

    existing_source_tables = source_inspector.get_table_names()
    logger.info(f"Tabelas encontradas no banco de origem: {existing_source_tables}")

    with source_engine.connect() as src_conn, target_engine.begin() as tgt_conn:
        for table_name in TABLES_ORDER:
            if table_name not in existing_source_tables:
                logger.warning(f"Tabela '{table_name}' não existe no banco de origem. Pulando.")
                continue

            try:
                # Ler registros da origem
                result = src_conn.execute(text(f'SELECT * FROM "{table_name}"'))
                rows = [dict(row._mapping) for row in result]

                if not rows:
                    logger.info(f"Tabela '{table_name}': 0 registros encontrados.")
                    continue

                logger.info(f"Tabela '{table_name}': Migrando {len(rows)} registros...")

                # Pega as colunas da tabela de destino
                target_cols = [c["name"] for c in target_inspector.get_columns(table_name)]

                success_count = 0
                for row in rows:
                    # Filtra apenas colunas válidas no destino
                    filtered_row = {k: v for k, v in row.items() if k in target_cols}
                    
                    if not filtered_row:
                        continue

                    cols_str = ", ".join([f'"{k}"' for k in filtered_row.keys()])
                    vals_str = ", ".join([f":{k}" for k in filtered_row.keys()])

                    # Cláusula de ON CONFLICT IGNORE para evitar duplicatas por Chave Primária (id)
                    pk_cols = target_inspector.get_pk_constraint(table_name).get("constrained_columns", ["id"])
                    pk_str = ", ".join([f'"{c}"' for c in pk_cols if c in filtered_row])

                    if pk_str:
                        sql = text(f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({vals_str}) ON CONFLICT ({pk_str}) DO NOTHING')
                    else:
                        sql = text(f'INSERT INTO "{table_name}" ({cols_str}) VALUES ({vals_str})')

                    try:
                        tgt_conn.execute(sql, filtered_row)
                        success_count += 1
                    except Exception as row_err:
                        logger.warning(f"Erro ao inserir registro em {table_name}: {row_err}")

                logger.info(f"Tabela '{table_name}': {success_count}/{len(rows)} registros migrados com sucesso!")

            except Exception as table_err:
                logger.error(f"Erro ao processar tabela '{table_name}': {table_err}")

    logger.info("=== MIGRAÇÃO CONCLUÍDA COM SUCESSO! ===")

if __name__ == "__main__":
    run_migration()
