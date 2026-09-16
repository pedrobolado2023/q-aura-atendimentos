import sqlite3

def run_migration():
    conn = sqlite3.connect('q_aura.db')
    cursor = conn.cursor()
    cursor.execute('PRAGMA table_info(qa_bot_configs)')
    cols = [row[1] for row in cursor.fetchall()]
    print('Colunas existentes:', cols)

    new_cols = [
        ('bot_mode', 'TEXT DEFAULT "flow"'),
        ('hermes_agent_name', 'TEXT DEFAULT "Assistente Virtual"'),
        ('hermes_system_prompt', 'TEXT'),
        ('hermes_model', 'TEXT DEFAULT "hermes-3-llama-3.1-8b"'),
        ('hermes_max_tokens', 'INTEGER DEFAULT 250'),
        ('hermes_temperature', 'REAL DEFAULT 0.7'),
        ('hermes_api_url', 'TEXT'),
        ('hermes_api_key', 'TEXT')
    ]

    for col_name, col_type in new_cols:
        if col_name not in cols:
            print(f'Adicionando coluna {col_name}...')
            cursor.execute(f'ALTER TABLE qa_bot_configs ADD COLUMN {col_name} {col_type}')

    conn.commit()
    conn.close()
    print('Migração de colunas concluída com 100% de sucesso!')

if __name__ == '__main__':
    run_migration()
