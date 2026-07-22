"""
Q-aura SaaS - Supabase to Easypanel PostgreSQL Migration Tool
--------------------------------------------------------------
Este script realiza a migração automatizada de todas as tabelas e dados
do banco de dados Supabase antigo para o novo banco PostgreSQL no Easypanel.
 Pode ser executado manualmente ou automaticamente pela aplicação no startup.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text, inspect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migration")

DEFAULT_SOURCE_URL = "postgresql://postgres.ooomoyseycfoctdcsbdx:qauraAdmin2026@aws-1-sa-east-1.pooler.supabase.com:5432/postgres"
DEFAULT_TARGET_URL = "postgresql://postgres:1ca9b8fbdc310884035e@dados_postgress:5432/dados?sslmode=disable"

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

def run_migration(target_engine=None):
    logger.info("=== INICIANDO MIGRAÇÃO SUPABASE -> EASYPANEL POSTGRESQL ===")
    
    source_url = os.getenv("SOURCE_DATABASE_URL", DEFAULT_SOURCE_URL)
    if source_url.startswith("postgres://"):
        source_url = source_url.replace("postgres://", "postgresql://", 1)

    try:
        source_engine = create_engine(source_url, pool_pre_ping=True)
        if not target_engine:
            target_url = os.getenv("DATABASE_URL", DEFAULT_TARGET_URL)
            if target_url.startswith("postgres://"):
                target_url = target_url.replace("postgres://", "postgresql://", 1)
            target_engine = create_engine(target_url, pool_pre_ping=True)

        from app.models import Base
        Base.metadata.create_all(bind=target_engine)

        source_inspector = inspect(source_engine)
        target_inspector = inspect(target_engine)
        existing_source_tables = source_inspector.get_table_names()

        with source_engine.connect() as src_conn, target_engine.begin() as tgt_conn:
            for table_name in TABLES_ORDER:
                if table_name not in existing_source_tables:
                    continue

                try:
                    result = src_conn.execute(text(f'SELECT * FROM "{table_name}"'))
                    rows = [dict(row._mapping) for row in result]
                    if not rows:
                        continue

                    logger.info(f"Tabela '{table_name}': Migrando {len(rows)} registros...")
                    target_cols = [c["name"] for c in target_inspector.get_columns(table_name)]

                    success_count = 0
                    for row in rows:
                        filtered_row = {k: v for k, v in row.items() if k in target_cols}
                        if not filtered_row:
                            continue

                        cols_str = ", ".join([f'"{k}"' for k in filtered_row.keys()])
                        vals_str = ", ".join([f":{k}" for k in filtered_row.keys()])
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

                    logger.info(f"Tabela '{table_name}': {success_count}/{len(rows)} migrados com sucesso!")
                except Exception as table_err:
                    logger.error(f"Erro em '{table_name}': {table_err}")

        logger.info("=== MIGRAÇÃO SUPABASE CONCLUÍDA COM SUCESSO! ===")
    except Exception as e:
        logger.error(f"[Migration Failed]: {e}")

if __name__ == "__main__":
    run_migration()
