"""Aplicar únicamente el esquema de la base separada del agente."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT
from app.db import agent_db

if __name__ == '__main__':
    with agent_db() as connection:
        connection.execute((ROOT / 'sql/001_schema.sql').read_text(encoding='utf-8'))
        mig_002 = ROOT / 'sql/002_conversations_history.sql'
        if mig_002.exists():
            connection.execute(mig_002.read_text(encoding='utf-8'))
    print('Esquema del copiloto preparado. Atlas no se modificó.')
    from app.db import demo_db
    with demo_db() as connection:
        # Estas funciones presuponen el catálogo tienda ya preparado.
        if connection.execute("SELECT to_regclass('tienda.productos') AS tabla").fetchone()['tabla']:
            connection.execute((ROOT / 'sql/011_compras_tareas.sql').read_text(encoding='utf-8'))
            connection.execute((ROOT / 'sql/012_contacto_pedidos.sql').read_text(encoding='utf-8'))
