"""Compras propuestas y agenda de la tienda demo, sin llamadas al modelo."""
import math
import re
import unicodedata
from datetime import datetime, timedelta
from psycopg.types.json import Jsonb
from .config import BOGOTA
from .db import demo_db, agent_db


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFD', str(text).lower()) if unicodedata.category(c) != 'Mn')


def cop(amount):
    return f'{int(amount):,}'.replace(',', '.')


def proponer_reabastecimiento(*, conversation_id):
    with demo_db() as conn:
        products = conn.execute('''SELECT pr.id, pr.sku, pr.nombre, pr.proveedor, pr.stock, pr.stock_minimo, pr.costo,
            coalesce((SELECT sum(i.cantidad) FROM tienda.pedido_items i JOIN tienda.pedidos p ON p.id=i.pedido_id
              WHERE i.producto_id=pr.id AND p.estado NOT IN ('cancelado','devolucion') AND p.fecha >= now()-interval '30 days' AND p.fecha <= now()),0)::int AS ventas_30_dias,
            coalesce((SELECT sum(i.cantidad) FROM tienda.orden_compra_items i JOIN tienda.ordenes_compra o ON o.id=i.orden_id
              WHERE i.producto_id=pr.id AND o.estado='en_camino'),0)::int AS en_camino
            FROM tienda.productos pr WHERE pr.stock <= pr.stock_minimo ORDER BY pr.proveedor, pr.sku''').fetchall()
    items = []
    for product in products:
        quantity = max(0, max(product['stock_minimo']*2, math.ceil(product['ventas_30_dias'])) - product['stock'] - product['en_camino'])
        if quantity:
            items.append({'producto_id': str(product['id']), 'sku': product['sku'], 'nombre': product['nombre'],
                          'proveedor': product['proveedor'], 'cantidad': quantity, 'costo_unitario': product['costo'],
                          'total': quantity*product['costo'], 'ventas_30_dias': product['ventas_30_dias'], 'en_camino': product['en_camino']})
    if not items:
        return {'resumen_hablado': 'No hace falta reabastecer: el stock y las compras en camino cubren la reposición.'}
    total = sum(i['total'] for i in items)
    detail = '; '.join(f"{i['nombre']}: {i['cantidad']} unidades × {cop(i['costo_unitario'])} COP = {cop(i['total'])} COP, proveedor {i['proveedor']}" for i in items)
    description = f'Propuesta de compra: {detail}. Total: {cop(total)} COP. Cantidades para 30 días de ventas, con mínimo de dos veces el stock mínimo y descontando lo que viene en camino.'
    payload = {'items': items, 'total': total, 'resumen': description}
    with agent_db() as conn:
        conn.execute("UPDATE pending_actions SET status='cancelled', resolved_at=now() WHERE conversation_id=%s AND status='pending'", (conversation_id,))
        row = conn.execute("INSERT INTO pending_actions(conversation_id,kind,status,payload) VALUES (%s,'orden_compra','pending',%s) RETURNING id", (conversation_id, Jsonb(payload))).fetchone()
    return {'pendingAction': {'id': str(row['id']), 'kind': 'orden_compra', 'description': description, 'payload': payload}, 'resumen_hablado': description}


def confirmar_compra(conn, action_id, payload):
    """Idempotencia en la base demo incluso si falla el commit de pending_actions."""
    existing = conn.execute('SELECT id, total FROM tienda.ordenes_compra WHERE accion_ref=%s ORDER BY id', (action_id,)).fetchall()
    if not existing:
        suppliers = sorted({i['proveedor'] for i in payload['items']})
        for supplier in suppliers:
            items = [i for i in payload['items'] if i['proveedor'] == supplier]
            order = conn.execute('INSERT INTO tienda.ordenes_compra(accion_ref,proveedor,total) VALUES (%s,%s,%s) ON CONFLICT (accion_ref,proveedor) DO NOTHING RETURNING id',
                                 (action_id, supplier, sum(i['total'] for i in items))).fetchone()
            if order:
                for item in items:
                    conn.execute('INSERT INTO tienda.orden_compra_items(orden_id,producto_id,cantidad,costo_unitario,total) VALUES (%s,%s,%s,%s,%s)',
                                 (order['id'], item['producto_id'], item['cantidad'], item['costo_unitario'], item['total']))
    return {'ok': True, 'tipo': 'orden_compra', 'message': f"Orden de compra guardada por {cop(payload['total'])} COP. El inventario comprado queda en camino; el stock disponible no aumenta hasta recibirlo."}


def parse_fecha(text, now=None):
    now = now or datetime.now(BOGOTA)
    text = normalized(text)
    words = {'una': 1, 'un': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10, 'once': 11, 'doce': 12}
    relative = re.search(r'\ben (\d+|una?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce) (horas?|minutos?|dias?)\b', text)
    if relative:
        raw, unit = relative.groups()
        value = int(raw) if raw.isdigit() else words[raw]
        return now + timedelta(**{('hours' if unit.startswith('hora') else 'minutes' if unit.startswith('minuto') else 'days'): value})
    day = now.date()
    explicit_day = False
    if 'pasado manana' in text:
        day += timedelta(days=2)
        explicit_day = True
    elif re.search(r'(?<!de la )\bmanana\b', text):
        day += timedelta(days=1)
        explicit_day = True
    elif re.search(r'\bhoy\b', text):
        explicit_day = True
    else:
        for index, weekday in enumerate(('lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo')):
            if re.search(r'\b' + weekday + r'\b', text):
                day += timedelta(days=(index-now.weekday()) % 7 or 7)
                explicit_day = True
                break
    clock = re.search(r'\ba las?\s+(\d{1,2}|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)(?::(\d{2}))?(?:\s*(am|pm|a\.m\.|p\.m\.|de la manana|de la tarde|de la noche))?\b', text)
    if clock:
        raw, minute, meridian = clock.groups()
        hour = int(raw) if raw.isdigit() else words[raw]
        if meridian and not 1 <= hour <= 12:
            raise ValueError('Con mañana/tarde/noche o am/pm usa una hora entre 1 y 12.')
        if meridian and ('pm' in meridian.replace('.', '') or 'tarde' in meridian or 'noche' in meridian):
            hour = hour % 12 + 12
        elif meridian:
            hour %= 12
        if hour > 23 or int(minute or 0) > 59:
            raise ValueError('La hora indicada no es válida.')
        stamp = datetime.combine(day, datetime.min.time(), BOGOTA).replace(hour=hour, minute=int(minute or 0))
        if not explicit_day and stamp <= now:
            stamp += timedelta(days=1)
    elif explicit_day:
        stamp = datetime.combine(day, datetime.min.time(), BOGOTA).replace(hour=9)
    else:
        raise ValueError('Dime cuándo: hoy a las 15, mañana a las 9, el lunes o en 2 horas.')
    if stamp <= now:
        raise ValueError('Esa fecha ya pasó; indica una fecha u hora futura.')
    return stamp


def crear_tarea(texto, fecha=None):
    try:
        stamp = parse_fecha(fecha or texto)
    except ValueError as exc:
        return {'error': str(exc), 'resumen_hablado': str(exc)}
    # Conservar tildes y nombres en el texto original, no la versión normalizada.
    prefix = re.match(r'^\s*(?:recu[eé]rdame|recordarme|recordatorio:?|crea(?:r)? (?:una )?tarea:?|anota(?:r)?(?: una tarea)?:?)\s*', texto, re.I)
    content = texto[prefix.end():].strip() if prefix else texto.strip()
    if not content:
        return {'error': 'Dime el texto del recordatorio.'}
    with demo_db() as conn:
        task = conn.execute('INSERT INTO tienda.tareas(texto,fecha_hora) VALUES (%s,%s) RETURNING id', (content,stamp)).fetchone()
    return {'tarea': {'id': task['id'], 'texto': content, 'fecha_hora': stamp.isoformat(), 'estado': 'pendiente'},
            'resumen_hablado': f"Tarea {task['id']} guardada: {content}. Fecha: {stamp.strftime('%d/%m/%Y a las %H:%M')}, hora de Colombia. Puedes consultarla en tus pendientes."}


def listar_tareas():
    with demo_db() as conn:
        tasks = conn.execute("SELECT id,texto,fecha_hora,estado FROM tienda.tareas WHERE estado='pendiente' ORDER BY fecha_hora,id LIMIT 50").fetchall()
    for task in tasks:
        task['fecha_hora'] = task['fecha_hora'].astimezone(BOGOTA).isoformat()
    return {'tareas': tasks, 'resumen_hablado': ('Tienes estas tareas pendientes: ' + '; '.join(f"{t['id']}: {t['texto']} ({datetime.fromisoformat(t['fecha_hora']).strftime('%d/%m %H:%M')})" for t in tasks) + '. Hora de Colombia.') if tasks else 'No tienes tareas pendientes.'}


def completar_tarea(id):
    with demo_db() as conn:
        task = conn.execute("UPDATE tienda.tareas SET estado='completada' WHERE id=%s RETURNING id", (int(id),)).fetchone()
    return {'resumen_hablado': f'Tarea {id} marcada como completada.' if task else f'No encontré la tarea {id}.'}


def borrar_tarea(id):
    with demo_db() as conn:
        task = conn.execute('DELETE FROM tienda.tareas WHERE id=%s RETURNING id', (int(id),)).fetchone()
    return {'resumen_hablado': f'Tarea {id} borrada.' if task else f'No encontré la tarea {id}.'}


def route_operaciones(text):
    text_norm = normalized(text)
    if re.search(r'\breabastec|\breponer\b|\breposicion\b|\borden(?:es)? de compra\b|\bcomprar (?:stock|inventario)\b', text_norm):
        return 'proponer_reabastecimiento', {}
    task_id = re.search(r'\b(?:tarea|recordatorio)\s*(?:numero\s*)?#?(\d+)\b', text_norm)
    if task_id and re.search(r'\b(?:completa|completar|termina|terminar|hecha|listo|completada)\b', text_norm):
        return 'completar_tarea', {'id': int(task_id[1])}
    if task_id and re.search(r'\b(?:borra|borrar|elimina|eliminar|cancela|cancelar)\b', text_norm):
        return 'borrar_tarea', {'id': int(task_id[1])}
    if re.search(r'\brecuerdame\b|\brecordarme\b|\b(?:crea|crear|anota|anotar) (?:una )?(?:tarea|recordatorio)\b', text_norm):
        return 'crear_tarea', {'texto': text}
    if re.search(r'\b(?:tareas|recordatorios)\b|que tengo pendiente|mis pendientes', text_norm):
        return 'listar_tareas', {}
    return None
