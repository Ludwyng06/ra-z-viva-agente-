"""Herramientas del modo demostración para Raíz Viva Naturales."""
import re
from .guardrails import normalize_text, is_order_feedback, is_short_field_reply, is_order_correction
from .catalogo_match import buscar_producto, leer_catalogo_demo
from datetime import date, datetime, timedelta
from psycopg.types.json import Jsonb
from .config import BOGOTA, ROOT
from .db import demo_db, agent_db
from .ollama import embed, vector_literal
from .operaciones_demo import (proponer_reabastecimiento, confirmar_compra,
                              crear_tarea, listar_tareas, completar_tarea, borrar_tarea)
from .geo_co import (
    resolve_ciudad_dpto,
    resolve_departamento,
    split_city_and_department,
    remove_prefixes,
    CIUDAD_DPTO_MAP,
    TODOS_LOS_DEPARTAMENTOS,
    DEPT_EXTRA_ALIASES,
)


def ensure_order_contact_schema():
    with demo_db() as conn:
        conn.execute((ROOT / 'sql/012_contacto_pedidos.sql').read_text(encoding='utf-8'))


PHONE_WORDS = {'cero': '0', 'un': '1', 'uno': '1', 'una': '1', 'dos': '2', 'tres': '3',
               'cuatro': '4', 'cinco': '5', 'seis': '6', 'siete': '7', 'ocho': '8', 'nueve': '9'}


def phone_digits(value):
    text = normalize_text(value)
    for word, digit in PHONE_WORDS.items():
        text = re.sub(r'\b' + word + r'\b', digit, text)
    return re.sub(r'\D', '', text)


def normalize_phone(value):
    digits = phone_digits(value)
    if len(digits) == 12 and digits.startswith('57'):
        digits = digits[2:]
    return digits if re.fullmatch(r'3\d{9}', digits) else None


def pickup_office(text):
    norm = normalize_text(text)
    match = re.search(r'\ben\s+(?:la\s+)?oficina(?:\s+de)?\s+(?:la\s+)?(?:transportadora\s+)?'
                      r'(inter\s*rapidisimo|servientrega|envia|coordinadora|tcc)\b', norm)
    if not match:
        return None, None
    carrier = {'interrapidisimo': 'Interrapidísimo', 'servientrega': 'Servientrega', 'envia': 'Envía',
               'coordinadora': 'Coordinadora', 'tcc': 'TCC'}[re.sub(r'\s', '', match.group(1))]
    return 'Reclama en oficina ' + carrier, carrier


def phone_candidate(text, expected_phone=False):
    norm = normalize_text(text)
    words = '|'.join(PHONE_WORDS)
    # A comma/semicolon ends the contact field in a combined order; the
    # following "una unidad" must never append a 1 to the phone number.
    sequence = rf'(?:\d+|{words})(?:[\s+().\-]*(?:\d+|{words}))*'
    marker = r'\b(?:telefono|celular|cel|contacto)' + (r'|\bnumero' if expected_phone else '')
    marked = re.search(r'(?:' + marker + r')\b\s*(?:(?:de (?:ella|el)|es|numero)\s*[:=]?\s*)*[:=]?\s*(' + sequence + r')(?!\w)', norm)
    if marked:
        return phone_digits(marked.group(1))
    if re.fullmatch(r'[\s+(),.\-]*' + sequence + r'[\s+(),.\-]*', norm):
        digits = phone_digits(norm)
        return digits if expected_phone or len(digits) >= 7 else None
    # Unlabelled phone inside a combined order must be a whole valid token;
    # never accept ten digits cut out of an eleven-digit dictated number.
    match = re.search(r'(?<![\d-])(?:\+?57[ -]?)?3(?:[ -]?\d){9}(?![ -]?\d)', text)
    return phone_digits(match.group(0)) if match else None


def phone_problem(value):
    digits = phone_digits(value)
    if len(digits) == 12 and digits.startswith('57'):
        digits = digits[2:]
    if len(digits) != 10:
        return f'El teléfono {digits} tiene {len(digits)} dígitos; necesito el celular de 10 dígitos.'
    return f'El teléfono {digits} no empieza por 3; necesito un celular colombiano de 10 dígitos que empiece por 3.'


def mask_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return '***' + digits[-4:] if len(digits) >= 4 else None


def public_order_payload(payload):
    result = dict(payload or {})
    if 'telefono' in result:
        result['telefono'] = mask_phone(result['telefono'])
    return result


def extract_order_contact(text, expected_phone=False):
    phone_match = re.search(r'(?<!\d)(?:\+?57[ -]?)?(3(?:[ -]?\d){9})(?!\d)', text)
    phone = phone_candidate(text, expected_phone)
    address_match = re.search(r'\b(calle|carrera|cra|cr|cl|diagonal|transversal|avenida|av)\b\s+\d', text, re.I)
    address, _ = pickup_office(text)
    if address_match:
        address = text[address_match.start():]
        address = re.split(r'[,;]?\s*(?:y\s+)?(?:mi\s+)?(?:tel[eé]fono|celular|cel|contacto)\s*(?:es|:)?', address, maxsplit=1, flags=re.I)[0]
        if phone_match:
            address = address.split(phone_match.group(0))[0]
        address = address.strip(' ,;.')
        address = re.sub(r'\s+y$', '', address, flags=re.I)
        numbered = re.match(r'(calle|carrera|cra|cr|cl|diagonal|transversal|avenida|av)\s+(\d+\w*)\s*(?:n[uú]mero|#)?\s+(\d+\w*)[ -]+(\d+\w*)(.*)', address, re.I)
        if numbered:
            street, first, second, third, tail = numbered.groups()
            street = {'cra': 'Carrera', 'cr': 'Carrera', 'cl': 'Calle', 'av': 'Avenida'}.get(street.lower(), street.title())
            tail = tail.strip(' ,;')
            tail = re.sub(r'^barrio\s+', '', tail, flags=re.I)
            suffix = ', barrio ' + tail.title() if tail else ''
            address = f'{street} {first} # {second}-{third}{suffix}'
        else:
            address = address[0].upper() + address[1:]
    return address, phone


def limit_value(value):
    try:
        return max(1, min(int(value), 50))
    except (ValueError, TypeError):
        return 10


def order_number(value):
    val = str(value or '').strip().lstrip('#')
    m = re.search(r'(?:rv-)?(\d+)', val, re.I)
    if m:
        return f'RV-{m.group(1)}'
    if re.fullmatch(r'[\w-]{1,30}', val):
        return val.upper()
    raise ValueError(f'Número de pedido inválido: {value}')


def parse_periodo(periodo):
    p = str(periodo or 'mes').strip().lower()
    now = datetime.now(BOGOTA)
    today = now.date()

    if p in ('hoy', 'dia', 'día'):
        ini = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=BOGOTA)
        fin = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=BOGOTA)
        comp_ini = ini - timedelta(days=1)
        comp_fin = fin - timedelta(days=1)
        comp_nombre = 'ayer'
        label = 'hoy'
    elif p in ('semana', 'esta_semana', 'ultimos_7_dias'):
        # Últimos 7 días
        ini = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=BOGOTA) - timedelta(days=6)
        fin = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=BOGOTA)
        comp_ini = ini - timedelta(days=7)
        comp_fin = ini - timedelta(seconds=1)
        comp_nombre = 'la semana anterior'
        label = 'esta semana'
    elif p in ('mes_anterior', 'anterior'):
        # Mes anterior (Octubre 2026)
        first_this_month = today.replace(day=1)
        last_prev_month = first_this_month - timedelta(days=1)
        first_prev_month = last_prev_month.replace(day=1)
        ini = datetime(first_prev_month.year, first_prev_month.month, 1, 0, 0, 0, tzinfo=BOGOTA)
        fin = datetime(last_prev_month.year, last_prev_month.month, last_prev_month.day, 23, 59, 59, tzinfo=BOGOTA)
        # Comparación: dos meses atrás (Septiembre 2026)
        first_two_ago = (first_prev_month - timedelta(days=1)).replace(day=1)
        comp_ini = datetime(first_two_ago.year, first_two_ago.month, 1, 0, 0, 0, tzinfo=BOGOTA)
        comp_fin = ini - timedelta(seconds=1)
        comp_nombre = 'dos meses atrás'
        label = 'mes anterior'
    else:  # 'mes' por defecto
        ini = datetime(today.year, today.month, 1, 0, 0, 0, tzinfo=BOGOTA)
        # fin hasta fin de mes o momento actual
        fin = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=BOGOTA)
        first_prev = (ini.date().replace(day=1) - timedelta(days=1)).replace(day=1)
        last_prev = ini.date().replace(day=1) - timedelta(days=1)
        comp_ini = datetime(first_prev.year, first_prev.month, 1, 0, 0, 0, tzinfo=BOGOTA)
        comp_fin = datetime(last_prev.year, last_prev.month, last_prev.day, 23, 59, 59, tzinfo=BOGOTA)
        comp_nombre = 'el mes anterior'
        label = 'este mes'

    return label, ini, fin, comp_nombre, comp_ini, comp_fin


def fmt_cop(n):
    return f"{int(n):,}".replace(',', '.')


def metricas_periodo(periodo='mes'):
    label, ini, fin, comp_nombre, comp_ini, comp_fin = parse_periodo(periodo)

    sql = '''
        SELECT
            count(*)::int AS total,
            count(*) FILTER (WHERE estado = 'confirmado')::int AS confirmados,
            count(*) FILTER (WHERE estado = 'nuevo')::int AS nuevos,
            count(*) FILTER (WHERE estado = 'despachado')::int AS despachados,
            count(*) FILTER (WHERE estado = 'entregado')::int AS entregados,
            count(*) FILTER (WHERE estado = 'cancelado')::int AS cancelados,
            count(*) FILTER (WHERE estado = 'devolucion')::int AS devoluciones,
            count(*) FILTER (WHERE estado = 'novedad')::int AS novedades,
            count(*) FILTER (WHERE metodo_pago = 'pagado')::int AS pagados,
            count(*) FILTER (WHERE metodo_pago = 'contra_entrega')::int AS contra_entrega,
            coalesce(sum(total) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS ventas_cop,
            coalesce(sum(flete) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS fletes_cop
        FROM tienda.pedidos
        WHERE fecha BETWEEN %s AND %s
    '''

    with demo_db() as conn:
        curr = conn.execute(sql, (ini, fin)).fetchone()
        prev = conn.execute(sql, (comp_ini, comp_fin)).fetchone()

    total = curr['total']
    ventas = curr['ventas_cop']
    activos = total - curr['cancelados']
    ticket = round(ventas / activos) if activos > 0 else 0
    tasa_entrega = round(100.0 * curr['entregados'] / max(1, curr['entregados'] + curr['devoluciones'] + curr['despachados']), 1)

    prev_total = prev['total']
    prev_ventas = prev['ventas_cop']
    dif_pedidos = total - prev_total
    dif_ventas = ventas - prev_ventas

    prefijo = "Hoy van" if label == 'hoy' else f"En {label} van"
    resumen_hablado = f"{prefijo} {total} pedidos con ventas totales de {fmt_cop(ventas)} COP, incluido el envío y excluyendo cancelados de las ventas."
    for fields in ((('pagados', 'pagados por anticipado'), ('contra_entrega', 'contra entrega')),
                   (('confirmados', 'confirmados'), ('cancelados', 'cancelados'),
                    ('entregados', 'entregados'), ('despachados', 'despachados'),
                    ('nuevos', 'nuevos'), ('novedades', 'con novedad'))):
        parts = [f"{curr[key]} {description}" for key, description in fields if curr[key]]
        if parts:
            resumen_hablado += ' De ellos, ' + ', '.join(parts) + '.'
    if not total:
        resumen_hablado = ('Hoy todavía no hay pedidos.' if label == 'hoy'
                           else f'En {label} todavía no hay pedidos.')

    return {
        'periodo': label,
        'resumen_hablado': resumen_hablado,
        'resumen_natural': resumen_hablado,
        'total_pedidos': total,
        'ventas_totales_cop': fmt_cop(ventas),
        'pagados_por_anticipado': curr['pagados'],
        'pago_contra_entrega': curr['contra_entrega'],
        'confirmados': curr['confirmados'],
        'cancelados': curr['cancelados'],
        'entregados': curr['entregados'],
        'despachados': curr['despachados'],
        'nuevos': curr['nuevos'],
        'novedades': curr['novedades'],
        'devoluciones': curr['devoluciones'],
        'fecha_inicio': ini.isoformat(),
        'fecha_fin': fin.isoformat(),
        'metricas': curr,
        'ticket_promedio_cop': fmt_cop(ticket),
        'tasa_entrega_pct': tasa_entrega,
        'comparacion': {
            'periodo_comparado': comp_nombre,
            'pedidos_anterior': prev_total,
            'ventas_anterior_cop': fmt_cop(prev_ventas),
            'diferencia_pedidos': dif_pedidos,
            'diferencia_ventas_cop': fmt_cop(dif_ventas),
        },
    }


CARRIERS_MAP = {
    'interrapid': 'Interrapidísimo',
    'inter': 'Interrapidísimo',
    'envia': 'Envía',
    'coordinadora': 'Coordinadora',
    'servientrega': 'Servientrega',
    'tcc': 'TCC',
    'veloces': 'Veloces',
    '99': '99 Minutos',
    'noventa y nueve': '99 Minutos',
}


def contar_pedidos(estado=None, periodo='mes', transportadora=None, ciudad=None, metodo_pago=None):
    label, ini, fin, _, _, _ = parse_periodo(periodo)

    raw_st = str(estado or '').strip().lower()
    norm_st = raw_st.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')

    filtro_estado = None

    if any(x in norm_st for x in ('devoluc', 'devuelt')):
        filtro_estado = 'devolucion'
    elif any(x in norm_st for x in ('cancel', 'anulad')):
        filtro_estado = 'cancelado'
    elif any(x in norm_st for x in ('entreg',)):
        filtro_estado = 'entregado'
    elif any(x in norm_st for x in ('despach', 'enviad', 'en ruta')):
        filtro_estado = 'despachado'
    elif any(x in norm_st for x in ('novedad',)):
        filtro_estado = 'novedad'
    elif any(x in norm_st for x in ('nuev', 'pendient')):
        filtro_estado = 'nuevo'
    elif any(x in norm_st for x in ('confirm',)):
        filtro_estado = 'confirmado'

    # Transportadora can come in its own argument or inside 'estado'
    if transportadora:
        t_norm = str(transportadora).lower().replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
        for ckw, cname in CARRIERS_MAP.items():
            if ckw in t_norm:
                transportadora = cname
                break
    else:
        for ckw, cname in CARRIERS_MAP.items():
            if ckw in norm_st:
                transportadora = cname
                break

    # Método de pago can come in its own argument or inside 'estado'
    if not metodo_pago:
        if any(x in norm_st for x in ('pagad', 'anticip', 'prepag')):
            metodo_pago = 'pagado'
        elif any(x in norm_st for x in ('contra entrega', 'contraentrega', 'contra-entrega')):
            metodo_pago = 'contra_entrega'
    else:
        mp_norm = str(metodo_pago).lower()
        if any(x in mp_norm for x in ('pagad', 'anticip', 'prepag')):
            metodo_pago = 'pagado'
        elif any(x in mp_norm for x in ('contra', 'entrega')):
            metodo_pago = 'contra_entrega'

    # Ciudad can come in its own argument or inside 'estado'
    if not ciudad:
        for c_key, (c_nom, _) in sorted(CIUDAD_DPTO_MAP.items(), key=lambda x: -len(x[0])):
            if re.search(r'\b' + re.escape(c_key) + r'\b', norm_st):
                ciudad = c_nom
                break
    if ciudad:
        geo_c = resolve_ciudad_dpto(ciudad)
        ciudad = geo_c.get('ciudad') or ciudad

    # Construir descripción conceptual del conteo
    if filtro_estado == 'devolucion':
        desc_concepto = 'devoluciones'
    elif filtro_estado == 'cancelado':
        desc_concepto = 'pedidos cancelados'
    elif filtro_estado == 'entregado':
        desc_concepto = 'pedidos entregados'
    elif filtro_estado == 'despachado':
        desc_concepto = 'pedidos despachados'
    elif filtro_estado == 'novedad':
        desc_concepto = 'pedidos con novedad'
    elif filtro_estado == 'nuevo':
        desc_concepto = 'pedidos nuevos'
    elif filtro_estado == 'confirmado':
        desc_concepto = 'pedidos confirmados'
    else:
        desc_concepto = 'pedidos'

    if metodo_pago == 'pagado':
        if desc_concepto == 'pedidos':
            desc_concepto = 'pedidos pagados por anticipado'
        else:
            desc_concepto = f"{desc_concepto} pagados por anticipado"
    elif metodo_pago == 'contra_entrega':
        if desc_concepto == 'pedidos':
            desc_concepto = 'pedidos con pago contra entrega'
        else:
            desc_concepto = f"{desc_concepto} con pago contra entrega"

    if transportadora:
        desc_concepto = f"{desc_concepto} por {transportadora}"

    if ciudad:
        desc_concepto = f"{desc_concepto} en {ciudad}"

    where_clauses = ['p.fecha BETWEEN %s AND %s']
    params = [ini, fin]

    if filtro_estado:
        where_clauses.append('p.estado = %s')
        params.append(filtro_estado)

    if metodo_pago:
        where_clauses.append('p.metodo_pago = %s')
        params.append(metodo_pago)

    if transportadora:
        where_clauses.append('t.nombre ILIKE %s')
        params.append(f"%{transportadora}%")

    if ciudad:
        where_clauses.append('p.ciudad ILIKE %s')
        params.append(f"%{ciudad}%")

    sql = f'''
        SELECT
            count(*)::int AS conteo,
            coalesce(sum(p.total), 0)::bigint AS total_cop
        FROM tienda.pedidos p
        LEFT JOIN tienda.transportadoras t ON t.id = p.transportadora_id
        WHERE {' AND '.join(where_clauses)}
    '''

    with demo_db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        total_periodo = conn.execute('SELECT count(*)::int AS tot FROM tienda.pedidos WHERE fecha BETWEEN %s AND %s',
                                     (ini, fin)).fetchone()['tot']

    conteo = row['conteo']
    total_cop = row['total_cop']
    tasa_pct = round((conteo / total_periodo * 100.0), 1) if total_periodo > 0 else 0.0
    tasa_str = str(tasa_pct).replace('.', ',')

    label_cap = label.capitalize()
    partic = 'registradas' if 'devoluc' in desc_concepto or 'novedad' in desc_concepto else 'registrados'
    if conteo == 0:
        resumen_hablado = f"{label_cap} no hay {desc_concepto} {partic} en la tienda."
    elif conteo == 1:
        desc_singular = (
            desc_concepto.replace('pedidos', 'pedido')
                         .replace('cancelados', 'cancelado')
                         .replace('entregados', 'entregado')
                         .replace('despachados', 'despachado')
                         .replace('confirmados', 'confirmado')
                         .replace('nuevos', 'nuevo')
                         .replace('devoluciones', 'devolución')
                         .replace('pagados', 'pagado')
        )
        resumen_hablado = f"{label_cap} hay 1 {desc_singular} ({tasa_str}% del total), con ventas por {fmt_cop(total_cop)} COP."
    else:
        resumen_hablado = f"{label_cap} hay {conteo} {desc_concepto} ({tasa_str}% del total), con ventas por {fmt_cop(total_cop)} COP."

    return {
        'periodo': label,
        'estado_consultado': filtro_estado,
        'metodo_pago_consultado': metodo_pago,
        'transportadora_consultada': transportadora,
        'ciudad_consultada': ciudad,
        'conteo': conteo,
        'total_periodo': total_periodo,
        'tasa_porcentaje': tasa_pct,
        'total_ventas_cop': fmt_cop(total_cop),
        'resumen_hablado': resumen_hablado,
    }


def productos_vendidos(periodo='hoy'):
    label, ini, fin, _, _, _ = parse_periodo(periodo)
    with demo_db() as conn:
        rows = conn.execute("""
            SELECT pr.sku, pr.nombre,
                   sum(pi.cantidad)::int AS unidades_vendidas,
                   sum(pi.total)::bigint AS ventas_cop
            FROM tienda.pedido_items pi
            JOIN tienda.pedidos p ON p.id = pi.pedido_id
            JOIN tienda.productos pr ON pr.id = pi.producto_id
            WHERE p.fecha BETWEEN %s AND %s AND p.estado != 'cancelado'
            GROUP BY pr.sku, pr.nombre
            ORDER BY unidades_vendidas DESC, ventas_cop DESC, pr.sku
        """, (ini, fin)).fetchall()
        totals = conn.execute("""
            SELECT count(*)::int AS pedidos,
                   count(*) FILTER (WHERE estado = 'cancelado')::int AS cancelados,
                   coalesce(sum(total) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS total,
                   coalesce(sum(flete) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS flete
            FROM tienda.pedidos WHERE fecha BETWEEN %s AND %s
        """, (ini, fin)).fetchone()
    total = sum(r['unidades_vendidas'] for r in rows)
    ventas = sum(r['ventas_cop'] for r in rows)
    detalle = '; '.join(f"{r['nombre']}: {r['unidades_vendidas']} {'unidad' if r['unidades_vendidas'] == 1 else 'unidades'}, {fmt_cop(r['ventas_cop'])} COP" for r in rows)
    difference = totals['total'] - ventas - totals['flete']
    reconciliation = f"{fmt_cop(ventas)} en productos + {fmt_cop(totals['flete'])} de envío"
    if difference:
        reconciliation += f" {'+' if difference > 0 else '-'} {fmt_cop(abs(difference))} de diferencia no atribuida en los registros"
    reconciliation += f" = {fmt_cop(totals['total'])} COP en ventas totales."
    resumen = (f"En los pedidos de {label}, excluyendo cancelados, se vendieron {total} {'unidad' if total == 1 else 'unidades'}. "
               f"{reconciliation} {detalle}." if rows else
               f"No hay productos vendidos en los pedidos de {label}, excluyendo cancelados. {reconciliation}")
    if difference:
        resumen += ' Hay una diferencia entre los ítems y los totales de pedidos; no puedo atribuirla a descuentos sin evidencia.'
    return {'periodo': label, 'productos': rows, 'total_unidades': total,
            'ventas_cop': ventas, 'fletes_cop': totals['flete'], 'ventas_totales_cop': totals['total'],
            'diferencia_no_atribuida_cop': difference, 'pedidos': totals['pedidos'],
            'cancelados': totals['cancelados'], 'resumen_hablado': resumen}



def resumen_hoy():
    _, ini, fin, _, _, _ = parse_periodo('hoy')
    with demo_db() as conn:
        return conn.execute("""
            SELECT count(*)::int AS pedidos,
                   coalesce(sum(total) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS ventas_cop,
                   count(*) FILTER (WHERE estado = 'nuevo')::int AS por_confirmar
            FROM tienda.pedidos WHERE fecha BETWEEN %s AND %s
        """, (ini, fin)).fetchone()


def top_productos(periodo='mes', limite=5):
    label, ini, fin, _, _, _ = parse_periodo(periodo)
    lim = limit_value(limite)

    sql = '''
        SELECT pr.sku, pr.nombre, pr.categoria,
               sum(pi.cantidad)::int AS unidades_vendidas,
               sum(pi.total)::bigint AS ventas_cop
        FROM tienda.pedido_items pi
        JOIN tienda.pedidos p ON p.id = pi.pedido_id
        JOIN tienda.productos pr ON pr.id = pi.producto_id
        WHERE p.fecha BETWEEN %s AND %s AND p.estado != 'cancelado'
        GROUP BY pr.sku, pr.nombre, pr.categoria
        ORDER BY unidades_vendidas DESC, ventas_cop DESC
        LIMIT %s
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (ini, fin, lim)).fetchall()

    return {
        'periodo': label,
        'limite': lim,
        'top_productos': rows,
        **({'resumen_hablado': 'Todavía no hay ventas de productos en este período.'} if not rows else {}),
    }


def ventas_por_ciudad(periodo='mes', limite=5):
    label, ini, fin, _, _, _ = parse_periodo(periodo)
    lim = limit_value(limite)

    sql = '''
        SELECT p.ciudad, p.departamento,
               count(*)::int AS pedidos,
               sum(p.total)::bigint AS ventas_cop
        FROM tienda.pedidos p
        WHERE p.fecha BETWEEN %s AND %s AND p.estado != 'cancelado'
        GROUP BY p.ciudad, p.departamento
        ORDER BY pedidos DESC, ventas_cop DESC
        LIMIT %s
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (ini, fin, lim)).fetchall()

    return {
        'periodo': label,
        'limite': lim,
        'ciudades': rows,
        **({'resumen_hablado': 'Todavía no hay ventas por ciudad en este período.'} if not rows else {}),
    }


def rendimiento_transportadoras(periodo='mes'):
    label, ini, fin, _, _, _ = parse_periodo(periodo)

    sql = '''
        SELECT t.nombre AS transportadora,
               t.dias_promedio,
               count(p.id)::int AS total_pedidos,
               count(p.id) FILTER (WHERE p.estado = 'entregado')::int AS entregados,
               count(p.id) FILTER (WHERE p.estado = 'devolucion')::int AS devoluciones,
               count(p.id) FILTER (WHERE p.estado = 'novedad')::int AS con_novedad,
               round(coalesce(avg(p.flete), 0))::int AS flete_promedio_cop,
               round(100.0 * count(p.id) FILTER (WHERE p.estado = 'entregado') / nullif(count(p.id), 0), 1) AS efectividad_pct
        FROM tienda.transportadoras t
        LEFT JOIN tienda.pedidos p ON p.transportadora_id = t.id AND p.fecha BETWEEN %s AND %s
        WHERE t.activo = true
        GROUP BY t.id, t.nombre, t.dias_promedio
        ORDER BY total_pedidos DESC, entregados DESC
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (ini, fin)).fetchall()

    return {
        'periodo': label,
        'transportadoras': rows,
        **({'resumen_hablado': 'Todavía no hay pedidos con transportadoras en este período.'}
           if not any(r['total_pedidos'] for r in rows) else {}),
    }


def buscar_pedido(numero):
    num = order_number(numero)
    sql = '''
        SELECT p.id, p.numero, p.fecha, c.nombre AS cliente,
               p.ciudad, p.departamento, t.nombre AS transportadora, p.estado,
               p.metodo_pago, p.subtotal, p.flete, p.total, p.creado_por, p.motivo_revision
        FROM tienda.pedidos p
        JOIN tienda.clientes c ON c.id = p.cliente_id
        LEFT JOIN tienda.transportadoras t ON t.id = p.transportadora_id
        WHERE p.numero = %s
    '''
    with demo_db() as conn:
        row = conn.execute(sql, (num,)).fetchone()
        if not row:
            # Reintentar quitando o agregando prefijo RV-
            alt = num[3:] if num.startswith('RV-') else f'RV-{num}'
            row = conn.execute(sql, (alt,)).fetchone()

        if not row:
            return {
                'encontrado': False,
                'numero': num,
                'mensaje': f'No se encontró ningún pedido con el número {num} en Raíz Viva Naturales.',
            }

        row['metodo_pago_visible'] = 'pagado por anticipado' if row['metodo_pago'] == 'pagado' else 'contra entrega'
        row['subtotal_cop'] = fmt_cop(row['subtotal'])
        row['flete_cop'] = fmt_cop(row['flete'])
        row['total_cop'] = fmt_cop(row['total'])

        items = conn.execute('''
            SELECT pr.sku, pr.nombre, pi.cantidad, pi.precio_unitario, pi.total
            FROM tienda.pedido_items pi
            JOIN tienda.productos pr ON pr.id = pi.producto_id
            WHERE pi.pedido_id = %s
            ORDER BY pi.id
        ''', (row['id'],)).fetchall()

        for it in items:
            it['precio_unitario_cop'] = fmt_cop(it['precio_unitario'])
            it['total_cop'] = fmt_cop(it['total'])

        novedades = conn.execute('''
            SELECT tipo, detalle, estado, fecha
            FROM tienda.novedades
            WHERE pedido_id = %s
            ORDER BY fecha DESC
        ''', (row['id'],)).fetchall()

    return {
        'encontrado': True,
        'pedido': row,
        'items': items,
        'novedades': novedades,
    }


def pedidos_por_estado(estado, limite=10):
    norm = str(estado or '').strip().lower()
    aliases = {
        'nuevos': 'nuevo', 'nuevo': 'nuevo',
        'confirmados': 'confirmado', 'confirmado': 'confirmado',
        'despachados': 'despachado', 'despachado': 'despachado', 'enviados': 'despachado',
        'entregados': 'entregado', 'entregado': 'entregado',
        'cancelados': 'cancelado', 'cancelado': 'cancelado',
        'devoluciones': 'devolucion', 'devolucion': 'devolucion', 'devueltos': 'devolucion',
        'novedades': 'novedad', 'novedad': 'novedad', 'con novedad': 'novedad',
    }
    val = aliases.get(norm, norm)
    lim = limit_value(limite)

    sql = '''
        SELECT p.numero, c.nombre AS cliente, p.ciudad, p.departamento,
               t.nombre AS transportadora, p.total, p.metodo_pago, p.fecha, p.motivo_revision
        FROM tienda.pedidos p
        JOIN tienda.clientes c ON c.id = p.cliente_id
        LEFT JOIN tienda.transportadoras t ON t.id = p.transportadora_id
        WHERE p.estado = %s
        ORDER BY p.fecha DESC
        LIMIT %s
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (val, lim)).fetchall()
        count = conn.execute('SELECT count(*)::int AS count FROM tienda.pedidos WHERE estado = %s', (val,)).fetchone()['count']

    for r in rows:
        r['metodo_pago_visible'] = 'pagado por anticipado' if r['metodo_pago'] == 'pagado' else 'contra entrega'
        r['total_cop'] = fmt_cop(r['total'])

    return {
        'estado': val,
        'total_registrados': count,
        'limite': lim,
        'pedidos': rows,
    }


def novedades_abiertas(limite=10):
    lim = limit_value(limite)
    sql = '''
        SELECT n.id, p.numero AS pedido_numero, n.tipo, n.detalle, n.fecha,
               c.nombre AS cliente, p.ciudad, p.departamento, t.nombre AS transportadora
        FROM tienda.novedades n
        JOIN tienda.pedidos p ON p.id = n.pedido_id
        JOIN tienda.clientes c ON c.id = p.cliente_id
        LEFT JOIN tienda.transportadoras t ON t.id = p.transportadora_id
        WHERE n.estado = 'abierta'
        ORDER BY n.fecha DESC
        LIMIT %s
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (lim,)).fetchall()
        count = conn.execute("SELECT count(*)::int AS count FROM tienda.novedades WHERE estado = 'abierta'").fetchone()['count']

    return {
        'total_abiertas': count,
        'limite': lim,
        'novedades': rows,
        'nota': 'Novedades de transporte abiertas que requieren contacto con el cliente o transportadora.',
    }


def alertas_stock():
    sql = '''
        SELECT sku, nombre, categoria, stock, stock_minimo, precio_venta,
               CASE WHEN stock = 0 THEN 'agotado' ELSE 'stock_bajo' END AS nivel
        FROM tienda.productos
        WHERE stock <= stock_minimo
        ORDER BY stock ASC, nombre ASC
    '''
    with demo_db() as conn:
        rows = conn.execute(sql).fetchall()

    return {
        'productos': rows,
        'total_alertas': len(rows),
        'nota': 'Productos con stock igual o inferior a su stock mínimo de seguridad.',
    }


def explicar_revision(numero):
    res = buscar_pedido(numero)
    if not res.get('encontrado'):
        return {
            'encontrado': False,
            'numero': numero,
            'error': f'No se encontró el pedido {numero} en la tienda.',
        }

    p = res['pedido']
    motivo = p.get('motivo_revision')
    estado = p.get('estado')

    if motivo:
        explicacion = f"El pedido {p['numero']} está en estado '{estado}' y requiere revisión manual debido a: {motivo}."
    else:
        explicacion = f"El pedido {p['numero']} está en estado '{estado}' y no tiene motivos de revisión pendientes."

    return {
        'encontrado': True,
        'numero': p['numero'],
        'estado': estado,
        'motivo_revision': motivo,
        'cliente': p['cliente'],
        'ciudad': p['ciudad'],
        'explicacion': explicacion,
    }


def buscar_en_documentos(pregunta, k=3):
    lim = max(1, min(int(k or 3), 3))
    query_text = str(pregunta or '')[:3000]
    vector = vector_literal(embed(query_text))

    with agent_db() as conn:
        rows = conn.execute('''
            SELECT d.id AS document_id, d.title, d.slug, d.source, c.content,
                   1 - (c.embedding <=> %s::vector) AS similitud
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE d.source LIKE 'demo/%%'
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        ''', (vector, vector, max(lim, 12))).fetchall()

        if rows:
            question = normalize_text(query_text)
            words = set(re.findall(r'[a-z]{4,}', question)) - {'politica', 'cual', 'como', 'para', 'tienda'}
            def relevance(row):
                title = normalize_text(row['title'])
                topic = 2 if 'devol' in question and 'devol' in title else 0
                return topic + len(words & set(re.findall(r'[a-z]{4,}', title))) * .2 + float(row['similitud'])
            best = max(rows, key=relevance)
            chunks = conn.execute('SELECT content FROM chunks WHERE document_id=%s ORDER BY ord',
                                  (best['document_id'],)).fetchall()
            # Overlap is part of the stored evidence. Reassemble in ordinal
            # order so neither a sentence nor a word begins halfway through.
            content = ''
            for chunk in chunks:
                piece = chunk['content'].strip()
                overlap = next((size for size in range(min(len(content), len(piece)), 0, -1)
                                if content.endswith(piece[:size])), 0)
                content += piece[overlap:] if overlap else ('\n\n' if content else '') + piece
            best['content'] = content
            best.pop('document_id', None)
            rows = [best]

    # Preserve retrieved paragraphs: the renderer selects a relevant excerpt.
    for row in rows:
        row['content'] = row['content'][:4500]
    sources = {r['slug']: {'title': r['title'], 'slug': r['slug']} for r in rows}

    # Si aún no hay documentos con prefijo demo/ en chunks (por estar en ingesta paralela),
    # devolvemos una respuesta contextual con fuentes fijas realistas
    if not rows:
        return {
            'fragmentos': [],
            'sources': [],
            'nota': 'Base de conocimiento de Raíz Viva Naturales en proceso de carga.',
        }

    return {
        'fragmentos': rows,
        'sources': list(sources.values()),
    }


def cotizar_envio(ciudad, departamento=None, productos=None):
    geo_res = resolve_ciudad_dpto(ciudad, departamento)
    if geo_res.get('estado') == 'opciones':
        return {
            'ambiguous': True,
            'ciudad': geo_res.get('ciudad'),
            'opciones': geo_res.get('opciones', []),
            'mensaje': geo_res.get('mensaje'),
            'pregunta_sugerida': geo_res.get('mensaje'),
            'resumen': geo_res.get('mensaje'),
            'instruccion': f"Hay varias opciones para {geo_res.get('ciudad')}: {', '.join(str(o) for o in geo_res.get('opciones', []))}. Pregunta al usuario a cuál se refiere ofreciendo las opciones.",
        }
    if geo_res.get('estado') == 'no_encontrado':
        return {
            'error': geo_res.get('mensaje'),
            'ciudad': None,
            'opciones': [],
            'mensaje': geo_res.get('mensaje'),
            'instruccion': f"{geo_res.get('mensaje')} Pide al usuario que repita o aclare indicando la ciudad y departamento.",
        }
    c_nom = geo_res.get('ciudad') or ciudad
    d_nom = geo_res.get('departamento') or departamento or 'Nacional'

    # Estimar peso en kg según cantidad de productos
    peso_kg = 1
    if isinstance(productos, list):
        total_items = sum(int(p.get('cantidad', 1)) for p in productos if isinstance(p, dict))
        peso_kg = max(1, round(total_items * 0.5))

    sql = '''
        SELECT t.nombre AS transportadora, t.dias_promedio,
               tar.departamento, tar.valor_base, tar.valor_por_kg,
               (tar.valor_base + tar.valor_por_kg * %s)::int AS flete_total
        FROM tienda.tarifas tar
        JOIN tienda.transportadoras t ON t.id = tar.transportadora_id
        WHERE (tar.departamento = %s OR tar.departamento = 'Nacional')
          AND t.activo = true
        ORDER BY flete_total ASC, t.dias_promedio ASC
    '''
    with demo_db() as conn:
        rows = conn.execute(sql, (peso_kg, d_nom)).fetchall()

    if not rows:
        # Fallback de tarifas si el departamento no tiene fila directa
        with demo_db() as conn:
            rows = conn.execute(sql, (peso_kg, 'Nacional')).fetchall()

    # Descartar duplicados si hay tarifa específica y Nacional para la misma transportadora
    opciones = {}
    for r in rows:
        t_name = r['transportadora']
        if t_name not in opciones or r['departamento'] == d_nom:
            opciones[t_name] = r

    lista_opciones = sorted(opciones.values(), key=lambda x: (x['flete_total'], x['dias_promedio']))
    mas_barata = min(lista_opciones, key=lambda x: (x['flete_total'], x['dias_promedio'])) if lista_opciones else None
    mas_rapida = min(lista_opciones, key=lambda x: (x['dias_promedio'], x['flete_total'])) if lista_opciones else None

    return {
        'ciudad': c_nom,
        'departamento': d_nom,
        'peso_estimado_kg': peso_kg,
        'opciones': lista_opciones,
        'mas_barata': mas_barata,
        'mas_rapida': mas_rapida,
        'resumen': (
            f"Envío a {c_nom} ({d_nom}): la opción más económica es {mas_barata['transportadora']} "
            f"por {fmt_cop(mas_barata['flete_total'])} COP ({mas_barata['dias_promedio']} días) y "
            f"la más rápida es {mas_rapida['transportadora']} ({mas_rapida['dias_promedio']} día(s)) "
            f"por {fmt_cop(mas_rapida['flete_total'])} COP."
        ) if mas_barata else f"Sin cotizaciones disponibles para {c_nom}.",
    }


WORD_TO_NUM = {
    'un': 1, 'una': 1, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4,
    'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10
}


def parse_productos_arg(productos):
    items = []
    if isinstance(productos, list):
        for item in productos:
            if isinstance(item, dict):
                items.append({
                    'nombre_o_sku': item.get('sku') or item.get('nombre') or item.get('producto', ''),
                    'cantidad': int(item.get('cantidad', 1)),
                })
            elif isinstance(item, str):
                items.append({'nombre_o_sku': item.strip(), 'cantidad': 1})
    elif isinstance(productos, str):
        # Ej: "2 tés verdes", "dos mieles", "miel x1"
        num_words = '|'.join(WORD_TO_NUM.keys())
        num_pattern = rf'(?:^|\b)(\d+|{num_words})\b\s*(?:x\s*|unidad(?:es)?\s+de\s+|tarros?\s+de\s+|frascos?\s+de\s+|cajas?\s+de\s+)?(.+)'
        for part in productos.split(','):
            part = part.strip()
            if not part:
                continue
            m = re.match(num_pattern, part, re.I)
            if m:
                qty_raw = m.group(1).lower()
                qty = int(qty_raw) if qty_raw.isdigit() else WORD_TO_NUM.get(qty_raw, 1)
                items.append({'nombre_o_sku': m.group(2).strip(), 'cantidad': qty})
            else:
                items.append({'nombre_o_sku': part, 'cantidad': 1})
    return items


def find_matching_product(query, db_prods):
    """Contrato único para nombres y SKU, sin selecci?n implícita ambigua."""
    noun = normalize_text(query).strip(' .!')
    if re.fullmatch(r'[a-z]+', noun):
        forms = {noun, noun[:-1] if noun.endswith('s') else noun,
                 noun[:-2] if noun.endswith('es') else noun}
        primary = [product for product in db_prods if normalize_text(product['nombre']).split()[0] in forms]
        if len(primary) == 1:
            product = primary[0]
            return {'estado': 'ok', 'producto': product, 'opciones': [],
                    'mensaje': f"Entendí {product['nombre']}."}
    result = buscar_producto(query, db_prods)
    if result['estado'] == 'ok':
        product = result['producto']
        corrected = str(query).strip().casefold() not in (
            product['nombre'].casefold(), product['sku'].casefold())
        result['mensaje'] = f"Entendí {product['nombre']}." if corrected else ''
    elif result['estado'] == 'no_encontrado':
        populares = top_productos(limite=3).get('top_productos', [])
        result['sugerencias'] = populares
        names = '; '.join(p['nombre'] for p in populares)
        result['mensaje'] = ('No encontré ese producto en el catálogo. ¿Puedes repetir el nombre o indicar el SKU? ' +
                             (f"Entre los más vendidos están: {names}." if names else
                              'Puedes consultar el catálogo para elegir un producto.'))
    return result


def product_question(result, borrador=None):
    response = dict(result)
    response['ambiguous'] = result['estado'] == 'opciones'
    response['pregunta_sugerida'] = result['mensaje']
    response['instruccion'] = result['mensaje'] + ' Pregunta antes de continuar; no elijas por el usuario.'
    if borrador is not None:
        response['borrador'] = borrador
    return response


def consultar_producto(nombre_o_sku, consulta=False, producto_contexto=None):
    """Consulta precio, disponibilidad y cotización unitaria sin crear pedidos."""
    catalogo = leer_catalogo_demo()
    if consulta:
        from .guardrails import normalize_text
        stop = set('hay mas otras otros version versiones presentacion presentaciones cantidades tamanos o solo de del la el los las un una cuanto cuantos cuantas cuesta vale precio stock queda quedan disponible disponibles tienen tiene tienes en gramos gr kg ml unidades que cual cuales es son y para por me puedes decir'.split())
        words = set(re.findall(r'[a-z]+', normalize_text(nombre_o_sku))) - stop
        candidates = [p for p in catalogo if words & (set(re.findall(r'[a-z]+', normalize_text(p['nombre']))) - stop)]
        if not candidates and not words and producto_contexto:
            # "¿Hay más cantidades?" se refiere a la última línea del borrador.
            family = re.split(r'\d', producto_contexto, maxsplit=1)[0].strip(' (')
            resolved = buscar_producto(family, catalogo)
            candidates = ([resolved['producto']] if resolved['estado'] == 'ok' else resolved['opciones'])
        if candidates:
            scores = [(len(words & set(re.findall(r'[a-z]+', normalize_text(p['nombre'])))), p) for p in candidates]
            best = max(score for score, _ in scores)
            candidates = [p for score, p in scores if score == best]
            details = '; '.join(f"{p['nombre']}: {fmt_cop(p['precio_venta'])} COP, {p['stock']} unidades disponibles" for p in candidates)
            return {'estado': 'ok', 'opciones': candidates,
                    'resumen_hablado': 'En el catálogo tengo ' + details + '.'}
        return {'estado': 'no_encontrado', 'resumen_hablado': '¿De qué producto quieres consultar las presentaciones, el precio o el stock?'}
    result = find_matching_product(nombre_o_sku, catalogo)
    return result if result['estado'] == 'ok' else product_question(result)


def parse_items_from_text(text, db_prods):
    clean = text.lower().replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    clean = re.sub(r'\b(?:crea|crear|quiero|hagamos)?\s*(?:un|nuevo)?\s*pedido\b', '', clean)
    clean = re.sub(r'\b(?:para\s+(?:(?:mi|el|la)\s+)?cliente|para|a nombre de)\s+[a-z ]+?(?=[:;,]|\s+en\b|\s+con\b|$)', '', clean)
    parts = re.split(r'[,+:]|\by\b|\be\b', clean)
    num_words = '|'.join(sorted(WORD_TO_NUM, key=len, reverse=True))
    pattern = rf'(?:^|\b)(\d+|{num_words})\b\s*(?:x\s*|unidad(?:es)?\s+de\s+|tarros?\s+de\s+|frascos?\s+de\s+|cajas?\s+de\s+)?([a-z\s]+)'

    results = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.search(pattern, part)
        if m:
            qty_raw, name_raw = m.group(1), m.group(2).strip()
            name_raw = re.split(r'\b(?:contra entrega|pagad[oa]|con pago|a nombre de|para)\b', name_raw)[0].strip()
            location_parts = name_raw.rsplit(' en ', 1)
            if len(location_parts) == 2 and location_parts[1] in CIUDAD_DPTO_MAP:
                name_raw = location_parts[0].strip()
            qty = int(qty_raw) if qty_raw.isdigit() else WORD_TO_NUM.get(qty_raw, 1)
            known_name = any(normalize_text(re.sub(r'\s*\([^)]*\)', '', p['nombre'])).strip() == name_raw for p in db_prods)
            if not is_short_field_reply(name_raw) and not known_name:
                continue
            matched = find_matching_product(name_raw, db_prods)
            if matched['estado'] != 'no_encontrado' or m:
                results.append(({'cantidad': qty, 'nombre_o_sku': name_raw}, matched))
        else:
            if not is_short_field_reply(part):
                continue
            p_strip = remove_prefixes(part).strip()
            if not p_strip or p_strip in CIUDAD_DPTO_MAP or resolve_departamento(p_strip).get('estado') == 'ok':
                continue
            matched = find_matching_product(part, db_prods)
            if matched['estado'] == 'ok':
                results.append(({'cantidad': 1, 'nombre_o_sku': part}, matched))
    return results


def get_or_create_borrador(conversation_id):
    borrador = None
    with demo_db() as conn:
        row = conn.execute('SELECT * FROM tienda.borradores_pedido WHERE conversation_id = %s',
                           (conversation_id,)).fetchone()
        if row:
            borrador = {
                'conversation_id': str(row['conversation_id']),
                'cliente_nombre': row['cliente_nombre'],
                'ciudad': row['ciudad'],
                'departamento': row['departamento'],
                'direccion': row['direccion'],
                'telefono': row.get('telefono'),
                'productos': row['productos'] or [],
                'transportadora': row['transportadora'],
                'metodo_pago': row['metodo_pago'],
                'estado': row['estado'],
            }

    if not borrador:
        borrador = {
            'conversation_id': str(conversation_id),
            'cliente_nombre': None,
            'ciudad': None,
            'departamento': None,
            'direccion': None,
            'telefono': None,
            'productos': [],
            'transportadora': None,
            'metodo_pago': None,
            'estado': 'en_progreso',
        }

    # Si faltan datos en el borrador, intentar recuperar de una propuesta previa en pending_actions
    if not borrador.get('cliente_nombre') or not borrador.get('productos'):
        try:
            with agent_db() as aconn:
                p_row = aconn.execute(
                    "SELECT payload FROM pending_actions WHERE conversation_id = %s ORDER BY created_at DESC LIMIT 1",
                    (conversation_id,)
                ).fetchone()
                if p_row and p_row['payload']:
                    pay = p_row['payload']
                    if not borrador.get('cliente_nombre'):
                        borrador['cliente_nombre'] = pay.get('cliente_nombre')
                    if not borrador.get('ciudad'):
                        borrador['ciudad'] = pay.get('ciudad')
                    if not borrador.get('departamento'):
                        borrador['departamento'] = pay.get('departamento')
                    if not borrador.get('transportadora'):
                        borrador['transportadora'] = pay.get('transportadora')
                    if not borrador.get('metodo_pago'):
                        borrador['metodo_pago'] = pay.get('metodo_pago')
                    if not borrador.get('productos') and pay.get('items'):
                        borrador['productos'] = pay.get('items', [])
        except Exception:
            pass

    return borrador


def save_borrador(borrador):
    with demo_db() as conn:
        conn.execute('''
            INSERT INTO tienda.borradores_pedido(conversation_id, cliente_nombre, ciudad, departamento, direccion, telefono, productos, transportadora, metodo_pago, estado, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (conversation_id) DO UPDATE SET
                cliente_nombre = EXCLUDED.cliente_nombre,
                ciudad = EXCLUDED.ciudad,
                departamento = EXCLUDED.departamento,
                direccion = EXCLUDED.direccion,
                telefono = EXCLUDED.telefono,
                productos = EXCLUDED.productos,
                transportadora = EXCLUDED.transportadora,
                metodo_pago = EXCLUDED.metodo_pago,
                estado = EXCLUDED.estado,
                updated_at = now()
        ''', (borrador['conversation_id'], borrador['cliente_nombre'], borrador['ciudad'],
              borrador['departamento'], borrador['direccion'], borrador.get('telefono'), Jsonb(borrador['productos']),
              borrador['transportadora'], borrador['metodo_pago'], borrador['estado']))
    if borrador['estado'] == 'en_progreso':
        # An edited draft must never confirm the previous proposal's payload.
        with agent_db() as conn:
            conn.execute("UPDATE pending_actions SET status='cancelled', resolved_at=now() "
                         "WHERE conversation_id=%s AND status='pending' AND kind='crear_pedido'",
                         (borrador['conversation_id'],))


def clear_borrador(conversation_id):
    with demo_db() as conn:
        conn.execute("UPDATE tienda.borradores_pedido SET estado = 'propuesto', updated_at = now() WHERE conversation_id = %s",
                     (conversation_id,))


def requested_order_field(conversation_id):
    with agent_db() as conn:
        row = conn.execute("SELECT content FROM messages WHERE conversation_id=%s AND role='assistant' ORDER BY id DESC LIMIT 1",
                           (str(conversation_id),)).fetchone()
    question = normalize_text(row['content']) if row else ''
    for field, pattern in (
        ('cliente_nombre', r'a nombre de quien|nombre del cliente|como se llama'),
        ('ciudad', r'en que ciudad|cual.*ciudad|indica.*ciudad|varias opciones.*ciudad|repite.*ciudad|ciudad y departamento|no identifique.*ciudad'),
        ('telefono', r'a que telefono|necesito.*celular|celular.*10 digitos'),
        ('direccion', r'a que direccion|no identifique.*direccion'),
        ('productos', r'que productos|no identifique el producto'), ('metodo_pago', r'contra entrega o pagado'),
        ('transportadora', r'que transportadora|no identifique la transportadora'),
    ):
        if re.search(pattern, question):
            return field
    return None


def actualizar_borrador_pedido(cliente_nombre=None, ciudad=None, departamento=None, direccion=None, telefono=None,
                               productos=None, metodo_pago=None, transportadora=None,
                               correccion=None, texto_usuario=None, *, conversation_id):
    borrador = get_or_create_borrador(conversation_id)
    # Si estaba en propuesto, se reabre a en_progreso para permitir correcciones en la misma conversación
    if borrador['estado'] == 'propuesto':
        borrador['estado'] = 'en_progreso'
    elif borrador['estado'] != 'en_progreso':
        borrador = {
            'conversation_id': str(conversation_id),
            'cliente_nombre': None,
            'ciudad': None,
            'departamento': None,
            'direccion': None,
            'telefono': None,
            'productos': [],
            'transportadora': None,
            'metodo_pago': None,
            'estado': 'en_progreso',
        }

    with demo_db() as conn:
        db_prods = conn.execute('SELECT id, sku, nombre, precio_venta, stock, stock_minimo FROM tienda.productos').fetchall()

    texto = f"{texto_usuario or ''} {correccion or ''}".strip()
    expected_field = requested_order_field(conversation_id)
    # Questions, complaints and long prose never reach the fuzzy product/city search.
    if ((is_order_feedback(texto) and not is_order_correction(texto)) or '?' in texto or '¿' in texto
            or re.match(r'^(?:por que|como|cual|que|cuando|donde|puedo|hay)\b', normalize_text(texto))):
        return {'borrador': borrador, 'faltan': [expected_field or 'cliente_nombre'],
                'pregunta_sugerida': 'Disculpa la confusión. ' + {
                    'cliente_nombre': '¿A nombre de quién va el pedido?', 'ciudad': '¿En qué ciudad se entrega?',
                    'direccion': '¿A qué dirección lo enviamos?', 'telefono': '¿A qué teléfono te contactamos?',
                    'productos': '¿Qué productos y cantidades agrego?', 'metodo_pago': '¿Contra entrega o pagado por anticipado?',
                    'transportadora': '¿Qué transportadora prefieres?'
                }.get(expected_field, '¿A nombre de quién va el pedido?')}
    if (expected_field == 'cliente_nombre' and is_short_field_reply(texto)
            and not is_order_correction(texto) and not re.search(r'\b(?:crear|pedido|contra entrega|pagado|oficina|ciudad)\b', normalize_text(texto))
            and not re.match(r'^(?:un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b', normalize_text(texto))
            and normalize_text(texto).strip(' .!') not in CIUDAD_DPTO_MAP
            and re.fullmatch(r"[A-Za-zÁÉÍÓÚáéíóúñÑüÜ' -]+[.!]?", texto)):
        cliente_nombre = texto.strip(' .!')
        texto = ''
    address_from_text, phone_from_text = extract_order_contact(texto, expected_field == 'telefono')
    if expected_field == 'productos' and re.fullmatch(r'\s*-?\d+\s*', texto):
        phone_from_text = None
    _, office_carrier = pickup_office(texto)
    if office_carrier:
        transportadora = office_carrier
    direccion = direccion or address_from_text
    telefono = telefono or phone_from_text
    contact_only = bool(address_from_text or phone_from_text) and not re.search(
        r'crear? (?:un )?pedido|nuevo pedido|agrega|producto|cliente|a nombre|contra entrega|pagad|\bpara\b|\ben\s+(?!oficina|la oficina)', texto, re.I)
    city_reply = (expected_field == 'ciudad' and is_short_field_reply(texto)
                  and not re.search(r'\d|\b(?:un|una|uno|dos|tres|cuatro|contra entrega|pagado|oficina|barata|rapida|transportadora|producto|unidades)\b', normalize_text(texto)))
    # Números y barrios no son productos, ciudades ni nombres de clientes.
    if contact_only:
        texto = ''
    if texto:
        changed_name = re.search(r'\b(?:cambia|corrige)\s+(?:el\s+)?nombre\s+(?:a|por)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ ]+)', texto, re.I)
        if changed_name:
            cliente_nombre = changed_name.group(1).strip(' .!')
        # Detectar cliente si aún no está asignado o si se menciona un nuevo cliente
        m_cli = re.search(r'(?:para\s+(?:(?:mi|el|la)\s+)?cliente|para|a nombre de|cliente:?)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ ]+?)(?:\s+(?:en|de|con|para|por)\b|[:,.!?]|$)', texto, re.I)
        if m_cli:
            c_words = [w for w in m_cli.group(1).strip().split() if w.lower() not in ('un', 'una', 'el', 'la', 'nuevo', 'pedido', 'crear', 'venta')]
            if c_words:
                found_name = ' '.join(c_words)
                if (borrador['cliente_nombre'] and found_name.lower() != borrador['cliente_nombre'].lower()
                        and not is_order_correction(texto)):
                    # Nuevo cliente en la misma conversación -> reiniciar borrador para ese cliente
                    borrador = {
                        'conversation_id': str(conversation_id),
                        'cliente_nombre': found_name,
                        'ciudad': None,
                        'departamento': None,
                        'direccion': None,
                        'telefono': None,
                        'productos': [],
                        'transportadora': None,
                        'metodo_pago': None,
                        'estado': 'en_progreso',
                    }
                else:
                    cliente_nombre = found_name

        # Detectar departamento en texto o corrección
        detected_dept = None
        for d_name in sorted(TODOS_LOS_DEPARTAMENTOS, key=len, reverse=True):
            aliases = [d_name] + DEPT_EXTRA_ALIASES.get(d_name, [])
            for a in aliases:
                if len(a) >= 4 and re.search(r'\b' + re.escape(a) + r'\b', texto, re.I):
                    detected_dept = d_name
                    break
            if detected_dept:
                break
        if detected_dept:
            departamento = detected_dept

        # Detectar ciudad si viene en texto o corrección
        detected_city = None
        if is_order_correction(texto):
            correction_tail = re.split(r'\b(?:a|por|era|es)\s+|[:;]', texto, flags=re.I)[-1].strip(' .!')
            # Exact matches only here; a correction to a name/product must not
            # become a geographically similar municipality.
            for key, (city_name, department_name) in CIUDAD_DPTO_MAP.items():
                if normalize_text(correction_tail) == normalize_text(key):
                    detected_city, departamento = city_name, department_name
                    break
        # 1. Probar división ciudad + departamento en texto
        c_cand, d_cand = split_city_and_department(texto)
        if not detected_city and city_reply and d_cand:
            if c_cand:
                res_c = resolve_ciudad_dpto(c_cand, d_cand)
                if res_c['estado'] in ('ok', 'confirmar', 'opciones') and res_c.get('ciudad'):
                    detected_city = res_c['ciudad']
            if not departamento:
                departamento = d_cand

        # 2. Buscar en CIUDAD_DPTO_MAP (cubre los 1122 municipios)
        if not detected_city:
            for c_key, (c_nom, d_nom) in sorted(CIUDAD_DPTO_MAP.items(), key=lambda x: -len(x[0])):
                if len(c_key) >= 4 and re.search(r'\b' + re.escape(c_key) + r'\b', texto.lower()):
                    detected_city = c_nom
                    if not departamento:
                        departamento = d_nom
                    break

        # 3. Buscar patrones de ubicación (en ..., ciudad ..., destino ...)
        if not detected_city and city_reply:
            m_geo = re.search(r'\b(?:en|ciudad(?:\s+de|\s+es)?|destino:?)\s+([A-Za-zÁÉÍÓÚáéíóúñÑ ]+?)(?:[,:\.\n]|\s+(?:con|de|por|\d|y\b|para\b|mejor|que|por\s+favor)|$)', texto, re.I)
            if m_geo:
                cand = m_geo.group(1).strip()
                res_cand = resolve_ciudad_dpto(cand, departamento or borrador.get('departamento'))
                if res_cand['estado'] in ('ok', 'confirmar', 'opciones') and res_cand.get('ciudad'):
                    detected_city = res_cand['ciudad']
                    if not departamento and res_cand.get('departamento'):
                        departamento = res_cand['departamento']

        # 4. Si el texto completo es una ciudad válida (fragmento solo en borrador o corrección)
        if not detected_city and city_reply:
            clean_txt = remove_prefixes(texto).strip()
            if clean_txt:
                res_single = resolve_ciudad_dpto(clean_txt, departamento or borrador.get('departamento'))
                if res_single['estado'] in ('ok', 'confirmar', 'opciones') and res_single.get('ciudad'):
                    detected_city = res_single['ciudad']
                    if not departamento and res_single.get('departamento'):
                        departamento = res_single['departamento']

        if detected_city:
            ciudad = detected_city

        # Regla 5: "como siempre" / "lo normal" -> contra entrega y la más barata
        if any(cs in texto.lower() for cs in ('como siempre', 'lo normal', 'de costumbre', 'habitual')):
            if not borrador['metodo_pago'] and not metodo_pago:
                metodo_pago = 'contra_entrega'
            if not borrador['transportadora'] and not transportadora:
                transportadora = 'la más barata'

        # Detectar método de pago en texto
        if not metodo_pago:
            t_low = texto.lower()
            if any(p in t_low for p in ('pagado', 'anticipado', 'prepagado')):
                metodo_pago = 'pagado'
            elif any(p in t_low for p in ('contra entrega', 'contraentrega', 'al recibir', 'en efectivo')):
                metodo_pago = 'contra_entrega'

        # Detectar transportadora en texto
        if not transportadora:
            t_low = normalize_text(texto).replace('inter rapidisimo', 'interrapidisimo')
            if 'mas barata' in t_low or 'más barata' in t_low or 'economica' in t_low:
                transportadora = 'la más barata'
            elif 'mas rapida' in t_low or 'más rápida' in t_low:
                transportadora = 'la más rápida'
            else:
                for t in ('interrapidísimo', 'interrapidisimo', 'envía', 'envia', 'coordinadora', 'tcc', 'servientrega', 'veloces', '99 minutos'):
                    if t in t_low:
                        transportadora = 'Interrapidísimo' if 'inter' in t else ('Envía' if 'env' in t else ('Coordinadora' if 'coord' in t else ('Servientrega' if 'servi' in t else ('Veloces' if 'velo' in t else ('99 Minutos' if '99' in t else 'TCC')))))
                        break

    # Asignar cliente y ciudad/departamento
    geo_res = None
    geo_mensaje = None
    if cliente_nombre:
        borrador['cliente_nombre'] = cliente_nombre.strip()

    # Persist independently valid contact fields before any city/product error.
    if direccion:
        normalized_address, _ = extract_order_contact(direccion)
        borrador['direccion'] = normalized_address or direccion.strip()
    if office_carrier:
        borrador['transportadora'] = office_carrier
    phone_error = None
    if telefono:
        normalized_phone = normalize_phone(telefono)
        if normalized_phone:
            borrador['telefono'] = normalized_phone
        else:
            phone_error = phone_problem(telefono)
            borrador['telefono'] = None
    save_borrador(borrador)

    if ciudad or departamento:
        geo_res = resolve_ciudad_dpto(ciudad or borrador.get('ciudad'), departamento or borrador.get('departamento'))
        if geo_res['estado'] == 'opciones':
            return {
                'borrador': borrador,
                'estado': 'opciones',
                'ambiguous': True,
                'ciudad': geo_res.get('ciudad'),
                'departamento': None,
                'opciones': geo_res.get('opciones', []),
                'mensaje': geo_res.get('mensaje'),
                'pregunta_sugerida': geo_res.get('mensaje'),
                'resumen': geo_res.get('mensaje'),
                'instruccion': f"Hay varias opciones para la ciudad: {', '.join(str(o) for o in geo_res.get('opciones', []))}. Pregunta al usuario a cuál se refiere ofreciendo las opciones.",
                'faltan': ['ciudad'],
            }
        elif geo_res['estado'] == 'confirmar':
            return {
                'borrador': borrador,
                'estado': 'confirmar',
                'ciudad': geo_res.get('ciudad'),
                'departamento': geo_res.get('departamento'),
                'opciones': geo_res.get('opciones', []),
                'mensaje': geo_res.get('mensaje'),
                'pregunta_sugerida': geo_res.get('mensaje'),
                'resumen': geo_res.get('mensaje'),
                'instruccion': geo_res.get('mensaje'),
                'faltan': ['ciudad'],
            }
        elif geo_res['estado'] == 'no_encontrado':
            return {
                'borrador': borrador,
                'estado': 'no_encontrado',
                'ciudad': None,
                'departamento': None,
                'opciones': [],
                'mensaje': geo_res.get('mensaje'),
                'error': geo_res.get('mensaje'),
                'instruccion': f"{geo_res.get('mensaje')} Pide al usuario que repita o aclare indicando ciudad y departamento.",
                'faltan': ['ciudad'],
            }
        elif geo_res['estado'] == 'ok':
            if geo_res.get('ciudad'):
                borrador['ciudad'] = geo_res['ciudad']
            if geo_res.get('departamento'):
                borrador['departamento'] = geo_res['departamento']
            if geo_res.get('mensaje') and 'Entendí' in geo_res['mensaje']:
                geo_mensaje = geo_res['mensaje']

    save_borrador(borrador)

    # Procesar corrección de cantidad
    if texto:
        num_words = '|'.join(WORD_TO_NUM.keys())
        if re.search(r'(?<!\w)-\d+\s*(?:unidades?|miel|te|aceite|producto|$)', normalize_text(texto)):
            return {'borrador': borrador, 'error': 'La cantidad debe ser mayor que cero; dime cuántas unidades quieres.', 'faltan': ['productos']}
        m_correc_qty = re.search(rf'\b(?:cambia|corrige|ajusta)\s+(?:la\s+)?cantidad(?:\s+de\s+.+?)?\s+(?:a|por)\s+(\d+|{num_words})\b[.!]?\s*$', texto.lower())
        if not m_correc_qty:
            m_correc_qty = re.search(rf'(?:mejor\s+que\s+sean|que\s+sean|cambia(?:lo)?\s+a|pon(?:le)?|ajusta\s+a|solo|deja(?:lo)?\s+en)\s+(\d+|{num_words})', texto.lower())
        if m_correc_qty and texto[m_correc_qty.end():].strip(' .!') not in ('', 'unidad', 'unidades'):
            # Una cantidad con nombre de producto se resuelve contra el catálogo;
            # no cambia por accidente la última línea al pedir otro producto.
            m_correc_qty = None
        if not m_correc_qty and borrador['productos']:
            m_correc_qty = re.fullmatch(rf'\s*(\d+|{num_words})(?:\s+unidad(?:es)?)?[.!]?\s*', texto.lower())
        if m_correc_qty and borrador['productos']:
            raw_val = m_correc_qty.group(1)
            new_qty = int(raw_val) if raw_val.isdigit() else WORD_TO_NUM.get(raw_val, 1)
            target_prod = borrador['productos'][-1]
            named_products = [p for p in borrador['productos']
                              if normalize_text(re.sub(r'\s*\([^)]*\)', '', p['nombre'])) in normalize_text(texto)
                              or normalize_text(p['sku']) in normalize_text(texto)]
            if len(named_products) == 1:
                target_prod = named_products[0]
            db_match = next((p for p in db_prods if p['sku'] == target_prod['sku']), None)
            if new_qty <= 0:
                return {'borrador': borrador, 'error': 'La cantidad debe ser mayor que cero; dime cuántas unidades quieres.', 'faltan': ['productos']}
            if db_match and db_match['stock'] < new_qty:
                return {
                    'borrador': borrador,
                    'error': f"No hay suficiente stock para {db_match['nombre']}. Solo quedan {db_match['stock']} unidades disponibles (solicitaste {new_qty}).",
                    'faltan': ['correccion_stock'],
                }
            target_prod['cantidad'] = new_qty
            target_prod['total'] = target_prod['precio_unitario'] * new_qty

    product_messages = []
    # Procesar productos
    if productos:
        items_to_add = parse_productos_arg(productos)
        for it in items_to_add:
            matched = find_matching_product(it['nombre_o_sku'], db_prods)
            if matched['estado'] != 'ok':
                return product_question(matched, borrador)
            if matched['mensaje']:
                product_messages.append(matched['mensaje'])
            matched = matched['producto']
            qty = it['cantidad']
            if qty <= 0:
                return {'borrador': borrador, 'error': 'La cantidad debe ser mayor que cero; dime cuántas unidades quieres.', 'faltan': ['productos']}
            if matched['stock'] == 0:
                return {
                    'borrador': borrador,
                    'error': f"No hay stock disponible para {matched['nombre']}. El producto se encuentra agotado (stock: 0)."
                }
            if matched['stock'] < qty:
                return {
                    'borrador': borrador,
                    'error': f"No hay suficiente stock para {matched['nombre']}. Solo quedan {matched['stock']} unidad(es) disponible(s) (solicitaste {qty}). ¿Deseas llevar las {matched['stock']} disponibles?"
                }
            existing = next((p for p in borrador['productos'] if p['sku'] == matched['sku']), None)
            if existing:
                existing['cantidad'] = qty
                existing['total'] = existing['precio_unitario'] * qty
            else:
                borrador['productos'].append({
                    'producto_id': str(matched['id']),
                    'sku': matched['sku'],
                    'nombre': matched['nombre'],
                    'cantidad': qty,
                    'precio_unitario': matched['precio_venta'],
                    'total': matched['precio_venta'] * qty,
                })
    elif texto and not m_correc_qty:
        num_words_regex = r'\b(?:\d+|un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b'
        has_product_qty = bool(re.search(num_words_regex, texto, re.I))
        is_geo_only = (detected_city is not None or detected_dept is not None) and not has_product_qty
        metadata_only = is_geo_only or (bool(re.search(r'crear? (?:un )?pedido|nuevo pedido|contra entrega|pagado|transportadora', texto, re.I)) and not has_product_qty)
        product_input = has_product_qty or expected_field == 'productos' or any(
            re.search(r'\b' + re.escape(word) + r'\b', normalize_text(texto))
            for product in db_prods for word in normalize_text(product['nombre']).split() if len(word) > 3)
        # Long combined orders only contribute short quantity/product fragments.
        parse_products = not metadata_only and product_input and (len(texto.split()) <= 4 or has_product_qty)
        items_parsed = parse_items_from_text(texto, db_prods) if parse_products else []
        if expected_field == 'productos' and not items_parsed and not metadata_only:
            return {'borrador': borrador, 'error': 'No identifiqué el producto y su cantidad; indica un nombre del catálogo o SKU y cuántas unidades quieres.', 'faltan': ['productos']}
        for it, matched in items_parsed:
            if matched['estado'] != 'ok':
                return product_question(matched, borrador)
            if matched['mensaje']:
                product_messages.append(matched['mensaje'])
            matched = matched['producto']
            qty = it['cantidad']
            if qty <= 0:
                return {'borrador': borrador, 'error': 'La cantidad debe ser mayor que cero; dime cuántas unidades quieres.', 'faltan': ['productos']}
            if matched['stock'] == 0:
                return {
                    'borrador': borrador,
                    'error': f"No hay stock disponible para {matched['nombre']}. El producto se encuentra agotado (stock: 0)."
                }
            if matched['stock'] < qty:
                return {
                    'borrador': borrador,
                    'error': f"No hay suficiente stock para {matched['nombre']}. Solo quedan {matched['stock']} unidad(es) disponible(s) (solicitaste {qty}). ¿Deseas llevar las {matched['stock']} disponibles?"
                }
            if borrador['productos'] and re.search(r'\b(?:cambia|cambiar|reemplaza)\b.*\bpor\b', texto, re.I):
                before = re.split(r'\bpor\b', texto, maxsplit=1, flags=re.I)[0].lower()
                target = next((p for p in borrador['productos'] if any(
                    w in before.split() for w in p['nombre'].lower().split() if len(w) > 2)), borrador['productos'][-1])
                borrador['productos'].remove(target)
            existing = next((p for p in borrador['productos'] if p['sku'] == matched['sku']), None)
            if existing:
                if re.search(r'\b(?:agrega|agregar|anade|añade|suma)\b', texto, re.I):
                    qty += existing['cantidad']
                if qty > matched['stock']:
                    return {'error': f"Solo quedan {matched['stock']} unidades disponibles de {matched['nombre']}."}
                existing.update(cantidad=qty, total=existing['precio_unitario'] * qty)
                continue
            borrador['productos'].append({
                'producto_id': str(matched['id']),
                'sku': matched['sku'],
                'nombre': matched['nombre'],
                'cantidad': qty,
                'precio_unitario': matched['precio_venta'],
                'total': matched['precio_venta'] * qty,
            })

    geo_mensaje = ' '.join(filter(None, [geo_mensaje, *product_messages]))

    # Asignar método de pago y transportadora
    if metodo_pago:
        payment_text = normalize_text(str(metodo_pago))
        if any(x in payment_text for x in ('pagad', 'anticipad')):
            borrador['metodo_pago'] = 'pagado'
        elif any(x in payment_text for x in ('contra_entrega', 'contra entrega', 'contraentrega', 'al recibir', 'efectivo')):
            borrador['metodo_pago'] = 'contra_entrega'
        else:
            save_borrador(borrador)
            return {'borrador': borrador, 'error': 'No identifiqué un método de pago válido; elige contra entrega o pagado por anticipado.', 'faltan': ['metodo_pago']}
    if transportadora:
        borrador['transportadora'] = transportadora.strip()

    # Resolver transportadora relativa si la ciudad ya está definida
    if borrador['transportadora'] in ('la más barata', 'la mas barata', 'la más rápida', 'la mas rapida'):
        if borrador['ciudad']:
            cotiz = cotizar_envio(borrador['ciudad'], borrador['departamento'], borrador['productos'] or None)
            if 'barata' in borrador['transportadora'].lower() and cotiz.get('mas_barata'):
                borrador['transportadora'] = cotiz['mas_barata']['transportadora']
            elif ('rapida' in borrador['transportadora'].lower() or 'rápida' in borrador['transportadora'].lower()) and cotiz.get('mas_rapida'):
                borrador['transportadora'] = cotiz['mas_rapida']['transportadora']

    save_borrador(borrador)

    if phone_error:
        accepted = 'Guardé la dirección. ' if direccion else ''
        return {'borrador': borrador, 'error': accepted + phone_error,
                'pregunta_sugerida': accepted + phone_error, 'faltan': ['telefono']}

    # Evaluar qué campos faltan
    faltan = []
    if not borrador['cliente_nombre']:
        faltan.append('cliente')
    if not borrador['ciudad']:
        faltan.append('ciudad')
    if not borrador.get('direccion'):
        faltan.append('direccion')
    if not normalize_phone(borrador.get('telefono')):
        faltan.append('telefono')
    if not borrador['productos']:
        faltan.append('productos')
    if not borrador['metodo_pago']:
        faltan.append('metodo_pago')
    if not borrador['transportadora']:
        faltan.append('transportadora')

    if faltan:
        cotizacion_info = ''
        if borrador['ciudad']:
            cotiz = cotizar_envio(borrador['ciudad'], borrador['departamento'], borrador['productos'] or None)
            if cotiz.get('mas_barata') and cotiz.get('mas_rapida'):
                mb = cotiz['mas_barata']
                mr = cotiz['mas_rapida']
                cotizacion_info = f" Opciones de envío para {borrador['ciudad']}: {mb['transportadora']} ({fmt_cop(mb['flete_total'])} COP, más barata) y {mr['transportadora']} ({fmt_cop(mr['flete_total'])} COP, más rápida, {mr['dias_promedio']} días)."

        resumen_parcial = []
        if borrador['cliente_nombre']:
            resumen_parcial.append(f"Cliente: {borrador['cliente_nombre']}")
        if borrador['ciudad']:
            resumen_parcial.append(f"Destino: {borrador['ciudad']} ({borrador['departamento']})")
        if borrador['productos']:
            prods_txt = ', '.join(f"{p['cantidad']}x {p['nombre']}" for p in borrador['productos'])
            resumen_parcial.append(f"Productos: {prods_txt}")
        if borrador.get('direccion'):
            resumen_parcial.append(f"Dirección: {borrador['direccion']}")
        if borrador.get('telefono'):
            resumen_parcial.append(f"Teléfono: {mask_phone(borrador['telefono'])}")
        if borrador['transportadora']:
            resumen_parcial.append(f"Transportadora: {borrador['transportadora']}")
        if borrador['metodo_pago']:
            met_vis = 'pagado por anticipado' if borrador['metodo_pago'] == 'pagado' else 'contra entrega'
            resumen_parcial.append(f"Pago: {met_vis}")

        estado_str = '; '.join(resumen_parcial) if resumen_parcial else 'Sin datos'

        instruccion_base = f"Borrador actual: [{estado_str}]. Faltan: {', '.join(faltan)}.{cotizacion_info} Pregunta al usuario de forma natural y breve por los datos faltantes."
        if geo_mensaje:
            instruccion_base = f"{geo_mensaje} {instruccion_base}"

        return {
            'borrador': borrador,
            'completo': False,
            'faltan': faltan,
            'estado': 'ok',
            'ciudad': borrador['ciudad'],
            'departamento': borrador['departamento'],
            'opciones': [],
            'mensaje': geo_mensaje or (f"{borrador['ciudad']}, {borrador['departamento']}" if borrador['ciudad'] else None),
            'estado_actual': estado_str,
            'cotizacion_envio': cotizacion_info,
            'instruccion': instruccion_base,
        }

    # ¡Todos los datos completos! Proponer el pedido formalmente
    prop_res = proponer_pedido(
        cliente_nombre=borrador['cliente_nombre'],
        ciudad=borrador['ciudad'],
        departamento=borrador['departamento'],
        direccion=borrador['direccion'],
        telefono=borrador['telefono'],
        productos=borrador['productos'],
        transportadora=borrador['transportadora'],
        metodo_pago=borrador['metodo_pago'],
        conversation_id=conversation_id,
    )

    clear_borrador(conversation_id)

    return {
        'borrador': borrador,
        'completo': True,
        'estado': 'ok',
        'ciudad': borrador['ciudad'],
        'departamento': borrador['departamento'],
        'opciones': [],
        'mensaje': geo_mensaje,
        'pendingAction': prop_res.get('pendingAction'),
        'resumen': prop_res.get('resumen'),
    }


def proponer_pedido(cliente_nombre, ciudad, departamento=None, productos=None, transportadora=None, metodo_pago='contra_entrega', direccion=None, telefono=None, *, conversation_id):
    if not str(direccion or '').strip() or not normalize_phone(telefono):
        return actualizar_borrador_pedido(cliente_nombre=cliente_nombre, ciudad=ciudad,
            departamento=departamento, productos=productos, transportadora=transportadora,
            metodo_pago=metodo_pago, direccion=direccion, telefono=telefono, conversation_id=conversation_id)
    telefono = normalize_phone(telefono)
    geo_res = resolve_ciudad_dpto(ciudad, departamento)
    if geo_res.get('estado') == 'opciones':
        return {
            'error': geo_res['mensaje'],
            'ambiguous': True,
            'opciones': geo_res['opciones'],
            'pregunta_sugerida': geo_res['mensaje'],
        }
    if geo_res.get('estado') == 'no_encontrado':
        return {'error': geo_res['mensaje']}
    c_nom = geo_res.get('ciudad') or ciudad
    d_nom = geo_res.get('departamento') or departamento or 'Nacional'
    items_parsed = parse_productos_arg(productos)

    if not items_parsed:
        return {'error': 'Debes indicar al menos un producto para proponer el pedido.'}

    # Validar productos en la base
    with demo_db() as conn:
        db_prods = conn.execute('SELECT id, sku, nombre, precio_venta, stock, stock_minimo FROM tienda.productos').fetchall()

    product_messages = []
    resolved_items = []
    subtotal = 0

    for item in items_parsed:
        query = item['nombre_o_sku']
        qty = item['cantidad']

        matched = find_matching_product(query, db_prods)

        if matched['estado'] != 'ok':
            return product_question(matched)
        if matched['mensaje']:
            product_messages.append(matched['mensaje'])
        matched = matched['producto']

        if matched['stock'] < qty:
            return {
                'error': f"No hay suficiente stock para '{matched['nombre']}'. Disponible: {matched['stock']} unidad(es), solicitado: {qty}."
            }

        item_total = matched['precio_venta'] * qty
        subtotal += item_total
        resolved_items.append({
            'producto_id': str(matched['id']),
            'sku': matched['sku'],
            'nombre': matched['nombre'],
            'cantidad': qty,
            'precio_unitario': matched['precio_venta'],
            'total': item_total,
        })

    # Cotizar flete
    cotiz = cotizar_envio(c_nom, d_nom, resolved_items)
    flete = 12000
    chosen_transp = 'Interrapidísimo'

    t_low = str(transportadora or '').strip().lower()
    if not transportadora or t_low in ('la más barata', 'la mas barata', 'barata', 'economica', 'económica'):
        if cotiz.get('mas_barata'):
            chosen_transp = cotiz['mas_barata']['transportadora']
            flete = cotiz['mas_barata']['flete_total']
    elif t_low in ('la más rápida', 'la mas rapida', 'rapida', 'rápida'):
        if cotiz.get('mas_rapida'):
            chosen_transp = cotiz['mas_rapida']['transportadora']
            flete = cotiz['mas_rapida']['flete_total']
    else:
        matched_opt = None
        for opt in cotiz.get('opciones', []):
            if opt['transportadora'].lower() in t_low or t_low in opt['transportadora'].lower():
                matched_opt = opt
                break
        if matched_opt:
            chosen_transp = matched_opt['transportadora']
            flete = matched_opt['flete_total']
        elif cotiz.get('mas_barata'):
            chosen_transp = transportadora.strip()
            flete = cotiz['mas_barata']['flete_total']

    total = subtotal + flete

    # Generar próximo número provisional de pedido
    with demo_db() as conn:
        last_row = conn.execute("SELECT max(substring(numero from 4)::int) AS max_num FROM tienda.pedidos WHERE numero LIKE 'RV-%'").fetchone()
        next_num = (last_row['max_num'] or 1140) + 1
        nuevo_numero = f'RV-{next_num}'

    # Normalizar método de pago
    metodo = 'pagado' if 'pagad' in str(metodo_pago).lower() or 'anticipad' in str(metodo_pago).lower() else 'contra_entrega'
    metodo_visible = 'pagado por anticipado' if metodo == 'pagado' else 'contra entrega'

    payload = {
        'numero': nuevo_numero,
        'cliente_nombre': cliente_nombre,
        'ciudad': c_nom,
        'departamento': d_nom,
        'direccion': direccion,
        'telefono': telefono,
        'transportadora': chosen_transp,
        'metodo_pago': metodo,
        'metodo_pago_visible': metodo_visible,
        'subtotal': subtotal,
        'subtotal_cop': fmt_cop(subtotal),
        'flete': flete,
        'flete_cop': fmt_cop(flete),
        'total': total,
        'total_cop': fmt_cop(total),
        'items': resolved_items,
    }

    prods_desc = ', '.join(f"{it['cantidad']}x {it['nombre']}" for it in resolved_items)
    desc = (
        f"Propuesta de pedido {nuevo_numero} para {cliente_nombre} en {c_nom} ({d_nom}): {prods_desc}. "
        f"Flete: {fmt_cop(flete)} COP ({chosen_transp}), Total: {fmt_cop(total)} COP, Pago: {metodo_visible}."
    )

    desc += f' Dirección: {direccion}. Teléfono: {mask_phone(telefono)}.'
    desc = ' '.join(product_messages + [desc])
    payload['resumen'] = desc

    # Cancelar cualquier propuesta previa pendiente en esta conversación para que solo haya UNA activa
    with agent_db() as conn:
        conn.execute('''
            UPDATE pending_actions
            SET status = 'cancelled', resolved_at = now()
            WHERE conversation_id = %s AND status = 'pending'
        ''', (conversation_id,))
        action_row = conn.execute('''
            INSERT INTO pending_actions(conversation_id, kind, order_ref, status, payload)
            VALUES (%s, 'crear_pedido', %s, 'pending', %s)
            RETURNING id
        ''', (conversation_id, nuevo_numero, Jsonb(payload))).fetchone()

    # Guardar borrador en estado propuesto para permitir correcciones futuras
    save_borrador({
        'conversation_id': conversation_id,
        'cliente_nombre': cliente_nombre,
        'ciudad': c_nom,
        'departamento': d_nom,
        'direccion': direccion,
        'telefono': telefono,
        'productos': resolved_items,
        'transportadora': chosen_transp,
        'metodo_pago': metodo,
        'estado': 'propuesto',
    })

    return {
        'pendingAction': {
            'id': str(action_row['id']),
            'kind': 'crear_pedido',
            'orderNumber': nuevo_numero,
            'description': desc,
            'payload': public_order_payload(payload),
        },
        'resumen': desc,
    }


def proponer_cambio_estado(numero, nuevo_estado, *, conversation_id):
    norm_accion = str(nuevo_estado or '').strip().lower()
    if norm_accion in ('confirmar', 'confirmado'):
        accion = 'confirmar'
    elif norm_accion in ('cancelar', 'cancelado'):
        accion = 'cancelar'
    else:
        raise ValueError("El estado propuesto debe ser 'confirmar' o 'cancelar'")

    res = buscar_pedido(numero)
    if not res.get('encontrado'):
        return {'error': f"Pedido {numero} no encontrado en la tienda; no se puede proponer el cambio."}

    p = res['pedido']
    num = p['numero']

    desc = f"{accion.capitalize()} el pedido {num} ({p['cliente']} en {p['ciudad']}). Requiere confirmación humana para registrarse en la tienda."

    with agent_db() as conn:
        conn.execute('''
            UPDATE pending_actions
            SET status = 'cancelled', resolved_at = now()
            WHERE conversation_id = %s AND status = 'pending'
        ''', (conversation_id,))
        action_row = conn.execute('''
            INSERT INTO pending_actions(conversation_id, kind, order_ref, status, payload)
            VALUES (%s, %s, %s, 'pending', %s)
            RETURNING id
        ''', (conversation_id, accion, num, Jsonb({'accion': accion, 'numero': num}))).fetchone()

    return {
        'pendingAction': {
            'id': str(action_row['id']),
            'kind': accion,
            'orderNumber': num,
            'description': desc,
        },
        'resumen': desc,
    }


def confirmar_accion_demo(action_id):
    with agent_db() as conn:
        row = conn.execute('SELECT * FROM pending_actions WHERE id = %s FOR UPDATE', (action_id,)).fetchone()
        if not row:
            return {'ok': False, 'error': 'Acción no encontrada.'}
        if row['status'] != 'pending':
            return {'ok': False, 'error': f"Esta acción ya se resolvió ({row['status']})."}

        kind = row['kind']
        order_ref = row['order_ref']
        payload = row['payload'] or {}
        conversation_id = str(row['conversation_id'])

        with demo_db() as dconn:
            if kind == 'orden_compra':
                result = confirmar_compra(dconn, action_id, payload)
                conn.execute("UPDATE pending_actions SET status='confirmed', resolved_at=now() WHERE id=%s", (action_id,))
                return result
            elif kind == 'crear_pedido':
                if not str(payload.get('direccion') or '').strip() or not normalize_phone(payload.get('telefono')):
                    return {'ok': False, 'error': 'Faltan dirección y teléfono válidos; completa el borrador antes de confirmar.'}
                # Asignar número definitivo secuencial sin huecos en la base
                last_row = dconn.execute("SELECT max(substring(numero from 4)::int) AS max_num FROM tienda.pedidos WHERE numero LIKE 'RV-%'").fetchone()
                next_num = (last_row['max_num'] or 1140) + 1
                def_numero = f'RV-{next_num}'

                c_nombre = payload.get('cliente_nombre', 'Cliente Demo')
                c_ciudad = payload.get('ciudad', 'Bogotá')
                c_dpto = payload.get('departamento', 'Bogotá D.C.')

                c_row = dconn.execute('''
                    SELECT id FROM tienda.clientes WHERE nombre ILIKE %s AND ciudad ILIKE %s AND telefono = %s LIMIT 1
                ''', (c_nombre, c_ciudad, payload['telefono'])).fetchone()
                if not c_row:
                    c_row = dconn.execute('''
                        INSERT INTO tienda.clientes(nombre, ciudad, departamento, telefono)
                        VALUES (%s, %s, %s, %s) RETURNING id
                    ''', (c_nombre, c_ciudad, c_dpto, payload['telefono'])).fetchone()
                cliente_id = c_row['id']

                t_nombre = payload.get('transportadora', 'Interrapidísimo')
                t_row = dconn.execute('SELECT id FROM tienda.transportadoras WHERE nombre = %s', (t_nombre,)).fetchone()
                transportadora_id = t_row['id'] if t_row else None

                nuevo_pedido = dconn.execute('''
                    INSERT INTO tienda.pedidos(numero, fecha, cliente_id, ciudad, departamento,
                                              transportadora_id, estado, metodo_pago, subtotal,
                                              flete, total, direccion, telefono, creado_por, motivo_revision)
                    VALUES (%s, now(), %s, %s, %s, %s, 'confirmado', %s, %s, %s, %s, %s, %s, 'copiloto', NULL)
                    RETURNING id, numero
                ''', (def_numero, cliente_id, c_ciudad, c_dpto, transportadora_id,
                      payload.get('metodo_pago', 'contra_entrega'), payload.get('subtotal', 0),
                      payload.get('flete', 0), payload.get('total', 0), payload['direccion'], payload['telefono'])).fetchone()

                for item in payload.get('items', []):
                    dconn.execute('''
                        INSERT INTO tienda.pedido_items(pedido_id, producto_id, cantidad, precio_unitario, total)
                        VALUES (%s, %s, %s, %s, %s)
                    ''', (nuevo_pedido['id'], item['producto_id'], item['cantidad'], item['precio_unitario'], item['total']))

                    dconn.execute('''
                        UPDATE tienda.productos SET stock = greatest(0, stock - %s)
                        WHERE id = %s
                    ''', (item['cantidad'], item['producto_id']))

                dconn.execute('''
                    INSERT INTO tienda.acciones(accion_ref, tipo, pedido_numero, detalle)
                    VALUES (%s, 'crear_pedido', %s, %s)
                ''', (action_id, def_numero, Jsonb(payload)))

                conn.execute('''
                    UPDATE pending_actions
                    SET status = 'confirmed', order_ref = %s, resolved_at = now()
                    WHERE id = %s
                ''', (def_numero, action_id))

                dconn.execute("UPDATE tienda.borradores_pedido SET estado = 'propuesto', updated_at = now() WHERE conversation_id = %s", (conversation_id,))

                metodo_vis = 'pagado por anticipado' if payload.get('metodo_pago') == 'pagado' else 'contra entrega'
                msg = f"Pedido {def_numero} creado y confirmado exitosamente en Raíz Viva Naturales para {c_nombre} con pago {metodo_vis} por un total de {fmt_cop(payload.get('total', 0))} COP."
                return {
                    'ok': True,
                    'tipo': 'crear_pedido',
                    'numero': def_numero,
                    'cliente': c_nombre,
                    'total': payload.get('total'),
                    'message': msg
                }

            elif kind == 'confirmar':
                dconn.execute("UPDATE tienda.pedidos SET estado = 'confirmado' WHERE numero = %s", (order_ref,))
                dconn.execute('''
                    INSERT INTO tienda.acciones(accion_ref, tipo, pedido_numero, detalle)
                    VALUES (%s, 'confirmar_pedido', %s, %s)
                ''', (action_id, order_ref, Jsonb({'accion': 'confirmar', 'numero': order_ref})))
                conn.execute('''
                    UPDATE pending_actions SET status = 'confirmed', resolved_at = now() WHERE id = %s
                ''', (action_id,))
                msg = f"Pedido {order_ref} confirmado exitosamente en Raíz Viva Naturales."
                return {'ok': True, 'tipo': 'confirmar', 'numero': order_ref, 'message': msg}

            elif kind == 'cancelar':
                dconn.execute("UPDATE tienda.pedidos SET estado = 'cancelado' WHERE numero = %s", (order_ref,))
                items = dconn.execute('''
                    SELECT pi.producto_id, pi.cantidad
                    FROM tienda.pedido_items pi
                    JOIN tienda.pedidos p ON p.id = pi.pedido_id
                    WHERE p.numero = %s
                ''', (order_ref,)).fetchall()
                for it in items:
                    dconn.execute('UPDATE tienda.productos SET stock = stock + %s WHERE id = %s', (it['cantidad'], it['producto_id']))

                dconn.execute('''
                    INSERT INTO tienda.acciones(accion_ref, tipo, pedido_numero, detalle)
                    VALUES (%s, 'cancelar_pedido', %s, %s)
                ''', (action_id, order_ref, Jsonb({'accion': 'cancelar', 'numero': order_ref})))
                conn.execute('''
                    UPDATE pending_actions SET status = 'confirmed', resolved_at = now() WHERE id = %s
                ''', (action_id,))
                msg = f"Pedido {order_ref} cancelado exitosamente en Raíz Viva Naturales."
                return {'ok': True, 'tipo': 'cancelar', 'numero': order_ref, 'message': msg}


def tool_spec(name, description, properties, required=()):
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'properties': properties,
                'required': list(required),
                'additionalProperties': False,
            },
        },
    }


STRING = {'type': 'string'}
INTEGER = {'type': 'integer'}
ARRAY = {'type': 'array', 'items': {'type': 'object'}}

TOOL_SPECS_DEMO = [
    tool_spec('proponer_reabastecimiento', 'Proponer compra al proveedor para stock bajo según ventas de 30 días; requiere Confirmar o Cancelar.', {}),
    tool_spec('crear_tarea', 'Guardar recordatorio con fecha relativa en español y hora de Colombia. No envía notificaciones.',
              {'texto': STRING, 'fecha': STRING}, ['texto']),
    tool_spec('listar_tareas', 'Consultar tareas y recordatorios pendientes.', {}),
    tool_spec('completar_tarea', 'Marcar una tarea como completada por su número.', {'id': INTEGER}, ['id']),
    tool_spec('borrar_tarea', 'Borrar una tarea por su número.', {'id': INTEGER}, ['id']),
    tool_spec('metricas_periodo', 'Totales globales, conteos y balance de ventas de pedidos en un periodo (hoy, semana, mes, mes_anterior). NO USAR para saber el producto más vendido ni para un pedido puntual.',
              {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']}}),
    tool_spec('contar_pedidos', 'Contar pedidos específicos por estado (devoluciones, cancelados, entregados, despachados, novedades, nuevos, confirmados), transportadora, ciudad o método de pago en un periodo (hoy, semana, mes, mes_anterior). Devuelve el conteo exacto, valor COP y tasa porcentual. USAR SIEMPRE para preguntas cuantitativas de cuántos/tasa/porcentaje.',
              {'estado': STRING, 'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']},
               'transportadora': STRING, 'ciudad': STRING, 'metodo_pago': STRING}),
    tool_spec('productos_vendidos', 'Unidades y ventas por cada producto, sin truncar, total de unidades; excluye cancelados. Para qué productos fueron los pedidos y seguimientos como de esos pedidos.',
              {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']}}),
    tool_spec('top_productos', 'Ranking de productos más vendidos / qué producto se vende más / productos estrella en un periodo.',
              {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']}, 'limite': INTEGER}),
    tool_spec('ventas_por_ciudad', 'Top 5 ciudades con mayores ventas de pedidos.',
              {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']}, 'limite': INTEGER}),
    tool_spec('rendimiento_transportadoras', 'Rendimiento, efectividad de entrega y flete promedio por transportadora.',
              {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes', 'mes_anterior']}}),
    tool_spec('buscar_pedido', 'Consultar un pedido puntual por su número (ej. RV-1081 o 1081), incluyendo su estado, cliente, transporte e items. USAR SIEMPRE que la pregunta mencione un número de pedido específico.',
              {'numero': STRING}, ['numero']),
    tool_spec('pedidos_por_estado', 'Listar múltiples pedidos según un estado general (nuevo, confirmado, despachado, entregado, cancelado, devolucion, novedad). NO USAR si la pregunta incluye un número de pedido puntual.',
              {'estado': STRING, 'limite': INTEGER}, ['estado']),
    tool_spec('novedades_abiertas', 'Consultar novedades de entrega abiertas que requieren gestión.', {'limite': INTEGER}),
    tool_spec('consultar_producto', 'Consultar precio, stock o cotización unitaria de un producto por nombre o SKU. Devuelve opciones si es ambiguo.', {'nombre_o_sku': STRING}, ['nombre_o_sku']),
    tool_spec('alertas_stock', 'Consultar productos naturales con stock bajo o agotados.', {}),
    tool_spec('explicar_revision', 'Explicar los motivos por los cuales un pedido nuevo requiere revisión.', {'numero': STRING}, ['numero']),
    tool_spec('buscar_en_documentos', 'Buscar en los documentos y políticas de la tienda (devoluciones, pagos contra entrega, envíos, productos).',
              {'pregunta': STRING, 'k': INTEGER}, ['pregunta']),
    tool_spec('cotizar_envio', 'Cotizar envío a una ciudad y recomendar la transportadora más económica y más rápida.',
              {'ciudad': STRING, 'departamento': STRING}, ['ciudad']),
    tool_spec('proponer_pedido', 'Proponer la creación de un nuevo pedido; genera una acción pendiente con confirmación humana.',
              {'cliente_nombre': STRING, 'ciudad': STRING, 'departamento': STRING, 'direccion': STRING, 'telefono': STRING, 'productos': {'type': 'string'}, 'metodo_pago': STRING},
              ['cliente_nombre', 'ciudad', 'productos']),
    tool_spec('proponer_cambio_estado', 'Proponer confirmar o cancelar un pedido existente; requiere confirmación humana.',
              {'numero': STRING, 'nuevo_estado': {'type': 'string', 'enum': ['confirmar', 'cancelar']}}, ['numero', 'nuevo_estado']),
    tool_spec('actualizar_borrador_pedido', 'Nunca asumas pago o transportadora: pregunta si faltan, salvo como siempre. Comunica correcciones y opciones geográficas o de producto; no confirmes sin elección. Crear o actualizar un pedido en borrador en varios turnos. Reúne cliente, ciudad, productos (nombres flexibles como "dos mieles"), transportadora ("la más barata", "la más rápida" o nombre), pago (contra entrega o pagado) y aplica correcciones ("mejor que sean 3", "cámbialo a Envía"). Genera la propuesta al completarse.',
              {'cliente_nombre': STRING, 'ciudad': STRING, 'departamento': STRING, 'direccion': STRING, 'telefono': STRING,
               'productos': STRING, 'metodo_pago': STRING, 'transportadora': STRING,
               'correccion': STRING, 'texto_usuario': STRING}),
]

FUNCTIONS_DEMO = {
    'proponer_reabastecimiento': proponer_reabastecimiento,
    'crear_tarea': crear_tarea,
    'listar_tareas': listar_tareas,
    'completar_tarea': completar_tarea,
    'borrar_tarea': borrar_tarea,
    'metricas_periodo': metricas_periodo,
    'contar_pedidos': contar_pedidos,
    'productos_vendidos': productos_vendidos,
    'top_productos': top_productos,
    'ventas_por_ciudad': ventas_por_ciudad,
    'rendimiento_transportadoras': rendimiento_transportadoras,
    'buscar_pedido': buscar_pedido,
    'pedidos_por_estado': pedidos_por_estado,
    'novedades_abiertas': novedades_abiertas,
    'consultar_producto': consultar_producto,
    'alertas_stock': alertas_stock,
    'explicar_revision': explicar_revision,
    'buscar_en_documentos': buscar_en_documentos,
    'cotizar_envio': cotizar_envio,
    'proponer_pedido': proponer_pedido,
    'proponer_cambio_estado': proponer_cambio_estado,
    'actualizar_borrador_pedido': actualizar_borrador_pedido,
}


def execute_demo(name, args, conversation_id):
    if name not in FUNCTIONS_DEMO or not isinstance(args, dict):
        raise ValueError(f'Herramienta no permitida en modo demo: {name}')
    func = FUNCTIONS_DEMO[name]
    if name in ('proponer_pedido', 'proponer_cambio_estado', 'actualizar_borrador_pedido', 'proponer_reabastecimiento'):
        return func(**args, conversation_id=conversation_id)
    return func(**args)
