"""Lista cerrada de herramientas: ninguna acepta SQL ni devuelve datos de contacto."""
import os
import re
from datetime import date, datetime, timedelta
from .config import BOGOTA
from .db import atlas_db, agent_db
from .ollama import embed, vector_literal

STATUS = {0: 'Nuevo', 1: 'Confirmado', 2: 'Despachado', 3: 'Entregado',
          5: 'Devuelto', 6: 'Cancelado', 8: 'Empacando'}
REASON_TEXT = {
    'no_coverage': 'Sin cobertura', 'out_of_stock': 'Sin inventario',
    'address_incomplete': 'Dirección incompleta', 'city_unresolved': 'Ciudad sin resolver',
    'product_unlinked': 'Producto sin vincular', 'customer_requested_correction': 'El cliente pidió una corrección',
    'rule_conflict': 'Preferencias en conflicto', 'quote_failed': 'No se pudo cotizar',
    'carrier_not_in_pancake': 'Transportadora no disponible en Pancake',
    'price_mismatch': 'Revisa el precio del pedido', 'cost_over_limit': 'Costo por encima del límite',
    'freight_high': 'Flete sobre el máximo', 'freight_above_ideal': 'Flete sobre el ideal',
    'buyer_high_risk': 'Comprador de alto riesgo', 'duplicate_order': 'Posible pedido duplicado',
    'customer_not_confirmed': 'Cliente sin confirmar', 'agent_error': 'No se pudo evaluar el pedido',
    'office_ambiguous': 'Oficina sin transportadora indicada', 'order_changed': 'El pedido cambió',
    'draft_open': 'Hay un borrador abierto', 'write_disabled': 'Escritura desactivada',
}


def store_id(connection):
    configured = os.getenv('ATLAS_STORE_ID')
    row = connection.execute('''SELECT s.id FROM stores s
        LEFT JOIN pancake_order_snapshots p ON p.store_id=s.id
        WHERE (%s::uuid IS NULL OR s.id=%s::uuid)
        GROUP BY s.id ORDER BY count(p.pancake_order_id) DESC,s.id LIMIT 1''',
        (configured, configured)).fetchone()
    if not row:
        raise ValueError('No hay una tienda disponible')
    return row['id']


def limit_value(value):
    return max(1, min(int(value), 30))


def order_number(value):
    value = str(value).strip().lstrip('#')
    if not re.fullmatch(r'[\w-]{1,60}', value):
        raise ValueError('Número de pedido no válido')
    return value


def summarize_decision(row):
    if not row:
        return None
    reasons = row.get('reasons') or []
    warnings = row.get('warnings') or []
    return {'estado': row['status'], 'semaforo': {'green':'verde','yellow':'amarillo','red':'rojo'}.get(row.get('signal'), 'sin evaluar'),
            'motivos': [REASON_TEXT.get(x, 'Revisión requerida') for x in reasons],
            'advertencias': [REASON_TEXT.get(x, 'Advertencia del robot') for x in warnings],
            'nota': 'Decisión registrada; el semáforo en vivo puede cambiar al reevaluar el pedido.'}


def resumen_del_dia(fecha=None):
    day = date.fromisoformat(fecha) if fecha else datetime.now(BOGOTA).date()
    with atlas_db() as connection:
        sid = store_id(connection)
        pos = connection.execute('''SELECT (inserted_at AT TIME ZONE 'America/Bogota')::date AS fecha,
            status, max(status_name) AS estado_registrado, count(*) AS cantidad, count(*) FILTER(WHERE cod=0) AS pagados,
            count(*) FILTER(WHERE cod>0) AS contra_entrega,
            count(*) FILTER(WHERE cod IS NULL) AS pago_desconocido,
            coalesce(sum(total_price),0) AS valor_cop FROM pancake_order_snapshots p
            WHERE store_id=%s AND (inserted_at AT TIME ZONE 'America/Bogota')::date BETWEEN %s AND %s
            AND NOT EXISTS (SELECT 1 FROM prepaid_recreations pr WHERE pr.store_id=p.store_id
                AND pr.connection_id=p.connection_id AND pr.original_order_id=p.pancake_order_id
                AND pr.copy_order_id IS NOT NULL AND pr.cancellation_started_at IS NOT NULL
                AND (pr.status='done' OR p.status=6))
            GROUP BY 1,2 ORDER BY 1,2''', (sid, day-timedelta(days=1), day)).fetchall()
        sheet = connection.execute('''SELECT (o.ordered_at AT TIME ZONE 'America/Bogota')::date AS fecha,
            o.status, count(*) AS cantidad, count(*) FILTER(WHERE o.is_prepaid) AS pagados,
            count(*) FILTER(WHERE NOT o.is_prepaid) AS contra_entrega,
            coalesce(sum(o.subtotal),0) AS valor_cop FROM orders o
            WHERE EXISTS (SELECT 1 FROM folder_data_sources f JOIN stores s ON s.folder_id=f.folder_id
                WHERE s.id=%s AND f.data_source_id=o.data_source_id)
            AND (o.ordered_at AT TIME ZONE 'America/Bogota')::date BETWEEN %s AND %s
            AND NOT EXISTS (SELECT 1 FROM prepaid_recreations pr JOIN order_links pl
                ON pl.store_id=pr.store_id AND pl.connection_id=pr.connection_id
                    AND pl.pancake_order_id=pr.original_order_id
                WHERE pr.store_id=%s AND pr.copy_order_id IS NOT NULL
                    AND pr.cancellation_started_at IS NOT NULL AND (pr.status='done' OR o.status='cancelled')
                    AND (pl.order_id=o.id OR ltrim(pl.shopify_order_number,'#')=ltrim(o.order_number,'#')))
            GROUP BY 1,2 ORDER BY 1,2''', (sid, day-timedelta(days=1), day, sid)).fetchall()
    for row in pos:
        row['estado'] = STATUS.get(row.pop('status'), row.pop('estado_registrado') or 'Desconocido')
    def comparison(rows):
        today = sum(r['cantidad'] for r in rows if r['fecha'] == day)
        yesterday = sum(r['cantidad'] for r in rows if r['fecha'] == day-timedelta(days=1))
        return {'hoy': today, 'ayer': yesterday, 'diferencia': today-yesterday}
    return {'fecha': str(day), 'zona': 'America/Bogota', 'pos': pos, 'hoja': sheet,
            'comparacion_pos': comparison(pos), 'comparacion_hoja': comparison(sheet),
            'nota': 'POS y hoja son fuentes separadas: no sumar, pueden contener el mismo pedido. COD=0 indica pago registrado; COD nulo es desconocido. Datos de la copia local, no producción.'}


def buscar_pedido(numero):
    number = order_number(numero)
    with atlas_db() as connection:
        sid = store_id(connection)
        rows = connection.execute('''SELECT p.pancake_order_id AS numero_pos,
            l.shopify_order_number AS numero_shopify, p.status,p.status_name,
            p.total_price AS valor_cop,p.partner_name AS transportadora,p.tracking_number AS guia,
            ARRAY(SELECT coalesce(i->>'name',i->>'product_name',i->'variation_info'->>'name','Producto')
                FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.items)='array' THEN p.items ELSE '[]'::jsonb END) i) AS productos,
            d.status AS robot_status,d.reasons,d.warnings,d.signal
            FROM pancake_order_snapshots p
            LEFT JOIN LATERAL (SELECT shopify_order_number FROM order_links l
                WHERE l.store_id=p.store_id AND l.connection_id=p.connection_id AND l.pancake_order_id=p.pancake_order_id
                ORDER BY l.updated_at DESC LIMIT 1) l ON true
            LEFT JOIN LATERAL (SELECT status,reasons,warnings,signal FROM logistics_agent_decisions d
                WHERE d.store_id=p.store_id AND d.pancake_order_id=p.pancake_order_id
                ORDER BY d.updated_at DESC LIMIT 1) d ON true
            WHERE p.store_id=%s AND (p.pancake_order_id=%s OR ltrim(l.shopify_order_number,'#')=%s)
            ORDER BY p.observed_at DESC LIMIT 5''', (sid, number, number)).fetchall()
        for row in rows:
            row['estado'] = row.pop('status_name') or STATUS.get(row['status'], 'Desconocido')
            row.pop('status')
            robot_status = row.pop('robot_status')
            signal = row.pop('signal')
            row['robot'] = summarize_decision({'status': robot_status, 'signal': signal, 'reasons': row.pop('reasons'),
                                              'warnings': row.pop('warnings')}) if robot_status else None
        sheets = connection.execute('''SELECT o.order_number AS numero, o.status AS estado,
            o.subtotal AS valor_cop,o.line_name AS producto,o.is_prepaid AS pagado FROM orders o
            WHERE ltrim(o.order_number,'#')=%s AND EXISTS (SELECT 1 FROM folder_data_sources f
                JOIN stores s ON s.folder_id=f.folder_id WHERE s.id=%s AND f.data_source_id=o.data_source_id)
            ORDER BY o.ordered_at DESC LIMIT 5''', (number, sid)).fetchall()
    return {'numero': number, 'encontrado': bool(rows or sheets), 'pos': rows, 'hoja': sheets}


def pedidos_por_estado(estado, limite=10):
    normalized = str(estado).strip().lower()
    aliases = {'nuevos': 0, 'confirmados': 1, 'enviados': 2, 'entregados': 3,
               'cancelados': 6, 'devueltos': 5}
    code = aliases.get(normalized)
    if code is None:
        code = next((k for k,v in STATUS.items() if v.lower() == normalized or str(k) == normalized), None)
    if code is None:
        raise ValueError('Estado desconocido; usa Nuevo, Confirmado, Enviado, Entregado, Devuelto o Cancelado')
    with atlas_db() as connection:
        rows = connection.execute('''SELECT pancake_order_id AS numero,total_price AS valor_cop,
            partner_name AS transportadora FROM pancake_order_snapshots WHERE store_id=%s AND status=%s
            ORDER BY inserted_at DESC NULLS LAST,pancake_order_id LIMIT %s''',
            (store_id(connection), code, limit_value(limite))).fetchall()
    return {'estado': STATUS[code], 'pedidos': rows, 'limite': limit_value(limite)}


def novedades_abiertas(limite=10):
    with atlas_db() as connection:
        rows = connection.execute('''SELECT DISTINCT ON (e.connection_id,e.pancake_order_id)
            e.pancake_order_id AS numero,e.normalized_status AS tipo,e.recorded_at AS fecha,
            s.partner_name AS transportadora FROM logistics_events e
            JOIN pancake_order_snapshots s ON s.connection_id=e.connection_id AND s.pancake_order_id=e.pancake_order_id
            WHERE e.store_id=%s AND e.normalized_status IN ('novelty','office_pickup')
            AND e.event_type IN ('tag_added','status_change') AND s.status NOT IN (3,5,6)
            AND NOT EXISTS (SELECT 1 FROM logistics_events x WHERE x.connection_id=e.connection_id
                AND x.pancake_order_id=e.pancake_order_id AND x.recorded_at>=e.recorded_at
                AND ((x.event_type='tag_removed' AND x.source=e.source AND x.original_status=e.original_status)
                  OR (x.normalized_status IN ('delivered','returned','novelty_resolved')
                      AND x.event_type IN ('status_change','tag_added'))))
            ORDER BY e.connection_id,e.pancake_order_id,e.recorded_at DESC LIMIT %s''',
            (store_id(connection), limit_value(limite))).fetchall()
    return {'novedades': rows, 'nota': 'Eventos locales sin resolución posterior registrada; no es una consulta en vivo a Dropi.'}


def alertas_stock():
    with atlas_db() as connection:
        rows = connection.execute('''SELECT DISTINCT dropi_product_id AS id,dropi_product_name AS producto,
            stock,stock_alert_level AS nivel,stock_checked_at AS actualizado FROM dropi_product_links
            WHERE store_id=%s AND stock_alert_level IN ('warning','critical','unavailable')
            ORDER BY stock NULLS FIRST LIMIT 30''', (store_id(connection),)).fetchall()
    return {'productos': rows, 'nota': 'Stock del proveedor registrado en la copia local.'}


def explicar_semaforo(numero):
    result = buscar_pedido(numero)
    return {'numero': result['numero'], 'encontrado': result['encontrado'],
            'decisiones': [row['robot'] for row in result['pos'] if row['robot']],
            'nota': 'Se explican motivos de la última decisión guardada; no se inventa un color si no hay evaluación en vivo.'}


def buscar_en_ayuda(pregunta, k=4):
    vector = vector_literal(embed(str(pregunta)[:3000]))
    with agent_db() as connection:
        rows = connection.execute('''SELECT d.title,d.slug,c.content,
            1-(c.embedding <=> %s::vector) AS similitud FROM chunks c JOIN documents d ON d.id=c.document_id
            ORDER BY c.embedding <=> %s::vector LIMIT %s''', (vector, vector, max(1,min(int(k),8)))).fetchall()
    return {'fragmentos': rows, 'sources': list({r['slug']: {'title': r['title'], 'slug': r['slug']} for r in rows}.values())}


def proponer_accion(tipo, numero, *, conversation_id):
    if tipo not in ('confirmar', 'cancelar'):
        raise ValueError('Solo se permite confirmar o cancelar de forma simulada')
    order = buscar_pedido(numero)
    if not order['encontrado']:
        return {'error': 'Pedido no encontrado; no se propone una acción sobre un pedido inexistente.'}
    if len(order['pos']) > 1:
        return {'error': 'Número ambiguo: indica el identificador de Pancake.'}
    with agent_db() as connection:
        row = connection.execute('''SELECT id FROM pending_actions WHERE conversation_id=%s
            AND kind=%s AND order_ref=%s AND status='pending' ORDER BY created_at DESC LIMIT 1''',
            (conversation_id,tipo,order['numero'])).fetchone()
        if not row:
            row = connection.execute('''INSERT INTO pending_actions(conversation_id,kind,order_ref)
                VALUES (%s,%s,%s) RETURNING id''', (conversation_id, tipo, order['numero'])).fetchone()
    return {'pendingAction': {'id': str(row['id']), 'kind': tipo, 'orderNumber': order['numero'],
            'description': f'{tipo.capitalize()} el pedido {order["numero"]} de forma simulada. Requiere confirmación humana; no modifica Atlas.'}}


def tool_spec(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object','properties': properties,'required': list(required),'additionalProperties': False}}}

STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}
TOOL_SPECS = [
    tool_spec('resumen_del_dia','Cifras reales de hoy y comparación de ayer, POS y hoja separados. Fecha YYYY-MM-DD opcional.', {'fecha': STRING}),
    tool_spec('buscar_pedido','Buscar por número Shopify o Pancake.', {'numero': STRING}, ['numero']),
    tool_spec('pedidos_por_estado','Listar pedidos por estado.', {'estado': STRING,'limite': INTEGER}, ['estado']),
    tool_spec('novedades_abiertas','Consultar novedades sin resolución registrada.', {'limite': INTEGER}),
    tool_spec('alertas_stock','Consultar alertas de stock del proveedor.', {}),
    tool_spec('explicar_semaforo','Explicar motivos de la decisión del robot de un pedido.', {'numero': STRING}, ['numero']),
    tool_spec('buscar_en_ayuda','Buscar cómo usar Atlas: modo Automático, prepagados, conectar hojas y demás ayuda.', {'pregunta': STRING,'k': INTEGER}, ['pregunta']),
    tool_spec('proponer_accion','Proponer confirmar o cancelar un pedido; siempre requiere confirmación humana simulada.',
              {'tipo': {'type':'string','enum':['confirmar','cancelar']},'numero': STRING}, ['tipo','numero']),
]
FUNCTIONS = {f.__name__: f for f in (resumen_del_dia,buscar_pedido,pedidos_por_estado,novedades_abiertas,
                                   alertas_stock,explicar_semaforo,buscar_en_ayuda,proponer_accion)}


def execute(name, args, conversation_id):
    if name not in FUNCTIONS or not isinstance(args, dict):
        raise ValueError('Herramienta no permitida')
    if name == 'proponer_accion':
        return FUNCTIONS[name](**args, conversation_id=conversation_id)
    return FUNCTIONS[name](**args)
