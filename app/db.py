from contextlib import contextmanager
import atexit
import json
import threading
import psycopg
from psycopg.rows import dict_row
from .config import ATLAS_DSN, AGENT_DSN, DEMO_DSN

# Una conexión persistente por base, serializada entre hilos de FastAPI.
# Cada uso mantiene su propia transacción: commit o rollback antes de reutilizarla.
_connections = {}
_locks = {'agent': threading.RLock(), 'atlas': threading.RLock(), 'demo': threading.RLock()}


@contextmanager
def reusable_connection(key, dsn, read_only=False):
    with _locks[key]:
        connection = _connections.get(key)
        if connection is None or connection.closed or connection.broken:
            connection = psycopg.connect(
                dsn, row_factory=dict_row, connect_timeout=5, autocommit=True,
                options='-c default_transaction_read_only=on' if read_only else '')
            _connections[key] = connection
        try:
            with connection.transaction():
                if read_only:
                    connection.execute('SET TRANSACTION READ ONLY')
                timeout = '10000ms' if read_only else '15000ms'
                connection.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
                yield connection
        except (psycopg.OperationalError, psycopg.InterfaceError):
            # No reintentar escrituras: una desconexión durante commit puede ser ambigua.
            connection.close()
            _connections.pop(key, None)
            raise


def close_connections():
    for key, lock in _locks.items():
        with lock:
            connection = _connections.pop(key, None)
            if connection is not None:
                connection.close()


atexit.register(close_connections)


@contextmanager
def agent_db():
    with reusable_connection('agent', AGENT_DSN) as connection:
        yield connection


@contextmanager
def atlas_db():
    # Independientemente del rol configurado, esta conexión jamás permite escribir.
    with reusable_connection('atlas', ATLAS_DSN, read_only=True) as connection:
        yield connection


@contextmanager
def demo_db():
    with reusable_connection('demo', DEMO_DSN, read_only=False) as connection:
        yield connection


def serialized(value):
    return json.dumps(value, ensure_ascii=False, default=str)
