"""Completa pedidos demo sin duplicar; conserva los pedidos ajenos al script."""
import random
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import BOGOTA, DEMO_DSN
from app.db import demo_db
from urllib.parse import urlsplit


def main():
    target = urlsplit(DEMO_DSN)
    if target.hostname != '127.0.0.1' or target.port != 5434:
        raise RuntimeError('El seed solo admite la base demo 127.0.0.1:5434.')
    now = datetime.now(BOGOTA)
    today = now.date()
    rng = random.Random(today.isoformat())
    created = 0
    with demo_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(8765011)")
        last = conn.execute("SELECT max((fecha AT TIME ZONE 'America/Bogota')::date) AS dia FROM tienda.pedidos").fetchone()['dia']
        clients = conn.execute('SELECT * FROM tienda.clientes ORDER BY nombre, id').fetchall()
        products = conn.execute('SELECT * FROM tienda.productos ORDER BY sku').fetchall()
        carriers = conn.execute('SELECT * FROM tienda.transportadoras WHERE activo ORDER BY nombre').fetchall()
        if not clients or not products or not carriers:
            raise RuntimeError('Primero prepara el catálogo demo con seed_demo.py.')
        counts = conn.execute("SELECT metodo_pago, count(*) AS n FROM tienda.pedidos WHERE (fecha AT TIME ZONE 'America/Bogota')::date = %s GROUP BY metodo_pago", (today,)).fetchall()
        counts = {r['metodo_pago']: r['n'] for r in counts}
        if any(n > 5 for n in counts.values()):
            raise RuntimeError('Hoy ya hay más de 5 pedidos de un método; no se borran pedidos existentes automáticamente.')
        next_num = conn.execute("SELECT coalesce(max(substring(numero from '^RV-([0-9]+)$')::int),1140) AS n FROM tienda.pedidos").fetchone()['n'] + 1
        day = min((last + timedelta(days=1)) if last else today, today)
        while day <= today:
            methods = (['pagado'] * (5-counts.get('pagado', 0)) + ['contra_entrega'] * (5-counts.get('contra_entrega', 0))) if day == today else [rng.choice(['pagado', 'contra_entrega']) for _ in range(rng.randint(2, 6))]
            for method in methods:
                client, carrier = rng.choice(clients), rng.choice(carriers)
                selected = rng.sample(products, rng.randint(1, min(3, len(products))))
                items = [(p, rng.randint(1, 2)) for p in selected]
                subtotal = sum(p['precio_venta'] * q for p, q in items)
                fee = rng.choice([9500, 11000, 12500, 14000])
                age = (today-day).days
                status = rng.choice(['nuevo', 'confirmado']) if age == 0 else (rng.choice(['confirmado', 'despachado']) if age < 4 else rng.choice(['entregado', 'entregado', 'cancelado', 'devolucion']))
                stamp = datetime.combine(day, time(rng.randint(8, 18), rng.randrange(60)), BOGOTA)
                if day == today:
                    stamp = min(stamp, now)
                row = conn.execute('''INSERT INTO tienda.pedidos(numero,fecha,cliente_id,ciudad,departamento,transportadora_id,estado,metodo_pago,subtotal,flete,total,creado_por)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'tienda') RETURNING id''',
                    (f'RV-{next_num}',stamp,client['id'],client['ciudad'],client['departamento'],carrier['id'],status,method,subtotal,fee,subtotal+fee)).fetchone()
                for product, quantity in items:
                    conn.execute('INSERT INTO tienda.pedido_items(pedido_id,producto_id,cantidad,precio_unitario,total) VALUES (%s,%s,%s,%s,%s)',
                                 (row['id'],product['id'],quantity,product['precio_venta'],quantity*product['precio_venta']))
                next_num += 1
                created += 1
            day += timedelta(days=1)
        summary = conn.execute("SELECT count(*) AS pedidos, count(*) FILTER (WHERE metodo_pago='pagado') AS pagados, count(*) FILTER (WHERE metodo_pago='contra_entrega') AS contra_entrega FROM tienda.pedidos WHERE (fecha AT TIME ZONE 'America/Bogota')::date=%s", (today,)).fetchone()
    print({'creados': created, 'hoy': str(today), **summary})


if __name__ == '__main__':
    main()
