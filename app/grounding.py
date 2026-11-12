"""Ground responses locally from tool evidence, never from assistant memory.

The final renderer deliberately uses public structured fields or server-authored
summaries. Token overlap alone cannot prove a claim (a correct amount attached to
the wrong order is still false), so unsupported prose is never passed through.
"""
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

UNKNOWN = 'No tengo ese dato en el sistema; no hay datos verificados para responder eso. Puedo consultar pedidos, productos, ventas y cobertura de envíos.'
NUMBER = re.compile(r'(?<!\w)(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?(?:\s*%)?(?!\w)')
PRIVATE = {'telefono', 'direccion', 'cliente', 'cliente_nombre', 'nombre_cliente', 'password', 'token', 'secret', 'credentials'}
BUSINESS = re.compile(r'pedido|venta|producto|precio|stock|inventario|env[ií]o|flete|ciudad|cobertura|gu[ií]a|descuento|devoluci|novedad|ayer|semana|mes|por qu[eé]|por que|era otra', re.I)


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value).lower())
                   if unicodedata.category(c) != 'Mn')


def is_greeting(text):
    return bool(re.fullmatch(r'(?:hola|buenos dias|buenas tardes|buenas noches|buenas|hey)(?:[,! ]+(?:como estas|que tal))?',
                             normalize(text).strip(' ¡!¿?.')))


def avoid_repeated_question(reply, previous, user_text):
    """Explain why a supplied field did not advance instead of looping."""
    if not previous or '?' not in reply:
        return reply
    current_questions = re.findall(r'¿[^?]+\?', reply)
    previous_questions = re.findall(r'¿[^?]+\?', previous)
    current = current_questions[-1] if current_questions else reply
    old = previous_questions[-1] if previous_questions else previous
    if normalize(current).strip() != normalize(old).strip():
        return reply
    norm = normalize(reply)
    reasons = (
        ('nombre', 'No identifiqué el nombre del cliente; escríbelo solo, sin datos de entrega.'),
        ('ciudad', 'No identifiqué la ciudad de entrega; indica el municipio y su departamento.'),
        ('direccion', 'No identifiqué una dirección válida; indica calle y número, o reclama en oficina y la transportadora.'),
        ('telefono', 'No identifiqué un celular válido; necesito el celular colombiano de 10 dígitos que empiece por 3.'),
        ('producto', 'No identifiqué el producto y su cantidad; indica el nombre o SKU y cuántas unidades quieres.'),
        ('contra entrega', 'No identifiqué el método de pago; elige contra entrega o pagado por anticipado.'),
        ('transportadora', 'No identifiqué la transportadora; indica su nombre o elige la más barata o la más rápida.'),
    )
    explanation = next((message for key, message in reasons if key in norm),
                       'No pude usar ese dato para avanzar; indica cuál dato del pedido quieres completar o corregir.')
    if re.search(r'corrig|cambia|no era|equivoc|no te pedi|confusion|por que', normalize(user_text)):
        explanation = 'Entiendo la corrección. ' + explanation
    return explanation


def spoken_text(value):
    """Do not expose protocol states, instructions or serialized objects."""
    if not isinstance(value, str) or not value.strip():
        return None
    if '{' in value or '}' in value or re.search(r'\bestado\s*:', value, re.I):
        return None
    if normalize(value).strip(' .!') in ('ok', 'success', 'null', 'none', 'true', 'false'):
        return None
    return re.sub(r'\b1 unidades\b', '1 unidad', value.strip())


def join_names(names):
    names = list(dict.fromkeys(names))
    return (', '.join(names[:-1]) + ' y ' + names[-1] if len(names) > 1 else names[0]) + '.'


def requested_answer(result, user_text):
    """Select only requested dimensions, using values from this tool result."""
    text = normalize(user_text)
    if result.get('error') or result.get('faltan') or result.get('pendingAction'):
        return None
    if re.search(r'diferencia|concili|no (?:cuadra|coincid)|hay (?:un )?error|totales distintos', text):
        return None  # The tool's reconciliation is the evidence for this question.
    # The last explicit restriction wins over earlier wording in the same turn.
    scope = re.split(r'\b(?:solo|solamente)\b', text)[-1]
    rows = result.get('productos', result.get('top_productos'))
    if isinstance(rows, list) and re.search(r'producto|vendid|vendieron', text):
        if not rows:
            return 'No encontré productos vendidos en ese período.'
        quantity = bool(re.search(r'cuant[oa]s|cantidad|unidades', scope))
        amount = bool(re.search(r'monto|importe|dinero|valor|cuanto (?:se )?vend|total de ventas|ventas totales|en pesos', scope)
                      or ('total' in scope and 'unidades' not in scope))
        if quantity and not amount:
            if 'unidades' in scope and result.get('total_unidades') is not None:
                total = result['total_unidades']
                return f"Se vendieron {total} {'unidad' if total == 1 else 'unidades'}."
            return f"Se vendieron {len(rows)} {'producto distinto' if len(rows) == 1 else 'productos distintos'}."
        if amount and result.get('ventas_cop') is not None:
            return f"El total de productos es {money(result['ventas_cop'])}."
        # Names only is also the default for "qué/cuáles productos". Removing
        # presentations avoids sneaking unit counts into a names-only answer.
        names = [re.sub(r'\s*\([^)]*\)', '', row['nombre']).strip()
                 for row in rows if isinstance(row, dict) and isinstance(row.get('nombre'), str)]
        return join_names(names) if names else None
    if re.search(r'cuant[oa]s\b|cantidad|conteo', text) and 'pedido' in text:
        count = result.get('conteo', result.get('total_pedidos'))
        if count is not None and not re.search(r'porcentaje|tasa|venta|monto|dinero', text):
            return f"Hay {count} {'pedido' if count == 1 else 'pedidos'} en el período consultado."
    if re.search(r'cuanto|total|monto|importe', text) and re.search(r'vend|venta', text):
        total = result.get('ventas_totales_cop', result.get('total_ventas_cop'))
        if total is not None:
            formatted = str(total) + ' COP' if isinstance(total, str) else money(total)
            return f'Las ventas del período consultado son {formatted}.'
    order = result.get('pedido')
    if isinstance(order, dict) and 'estado' in text and order.get('estado'):
        return f"El pedido {order['numero']} está {order['estado']}."
    return None


def numbers(value):
    found = set()
    for token in NUMBER.findall(str(value)):
        token = token.replace('%', '').strip()
        if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', token):
            token = token.replace('.', '').replace(',', '')
        else:
            token = token.replace(',', '.')
        try:
            found.add(Decimal(token))
        except InvalidOperation:
            pass
    return found


def evidence_results(tool_results):
    """Accept raw results or API traces; failed tools only supply their error."""
    results = []
    for result in tool_results or []:
        if not isinstance(result, dict):
            continue
        if result.get('name') in {'guardia_seguridad', 'verificacion_datos'}:
            continue
        value = result.get('result', result.get('summary', result))
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                # Confirmation tools return a server-authored plain summary.
                value = {'message': value} if result.get('name') == 'confirmar_accion' else {}
        if isinstance(value, dict):
            results.append(value)
    return results


def public_data(value):
    if isinstance(value, dict):
        return {key: public_data(item) for key, item in value.items()
                if normalize(key) not in PRIVATE and not any(x in normalize(key) for x in ('password', 'token', 'secret', 'telefono', 'direccion'))}
    if isinstance(value, (list, tuple)):
        return [public_data(item) for item in value]
    return value


def extract_claims(reply):
    """Expose number, order, named product/city, state and date candidates."""
    return {
        'numbers': numbers(reply),
        'orders': re.findall(r'\bRV-\d+\b|\bpedido\s+#?\d+', reply, re.I),
        'entities': re.findall(r'[«“\"]([^»”\"]+)[»”\"]|\b(?:producto|ciudad de|destino)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)*)', reply),
        'states': re.findall(r'\b(?:cancelado|confirmado|despachado|entregado|agotado|devoluci[oó]n)\b', reply, re.I),
        'dates': re.findall(r'\b\d{4}-\d{2}-\d{2}\b', reply),
    }


def unsupported_claims(reply, results):
    evidence = json.dumps(public_data(results), ensure_ascii=False, default=str)
    claims = extract_claims(reply)
    available = numbers(evidence)
    # Bounded pairwise sums: large reports must remain linear in practice.
    operands = list(available)[:100]
    sums = {a + b for i, a in enumerate(operands) for b in operands[i + 1:]}
    missing = [str(n) for n in claims['numbers'] if n not in available and n not in sums]
    haystack = normalize(evidence)
    for group in ('orders', 'states', 'dates'):
        missing.extend(item for item in claims[group] if normalize(item) not in haystack)
    for quoted, named in claims['entities']:
        if normalize(quoted or named) not in haystack:
            missing.append(quoted or named)
    return missing


def money(value):
    try:
        return f'{int(Decimal(str(value))):,}'.replace(',', '.') + ' COP'
    except (ValueError, InvalidOperation, TypeError):
        return None


def knowledge_answer(fragments, user_text):
    """Extract two or three complete sentences from the relevant section."""
    ignored = set('que como cual cuales puedo para por del las los una uno con sin son tiene tienen este esta debo sobre quiero saber politica tienda'.split())
    tokens = {word for word in re.findall(r'[a-z]+', normalize(user_text)) if len(word) > 2 and word not in ignored}
    query = normalize(user_text)
    candidates = []
    for index, fragment in enumerate(fragments or []):
        if not isinstance(fragment, dict) or not str(fragment.get('source', '')).startswith('demo/'):
            continue
        title = spoken_text(fragment.get('title'))
        content = fragment.get('content')
        if not title or not isinstance(content, str):
            continue
        sections = re.split(r'(?m)^##+\s+(.+)$', content)
        bodies = [(title, sections[0])] + [(sections[i], sections[i + 1]) for i in range(1, len(sections) - 1, 2)]
        for heading, paragraph in bodies:
            paragraph = re.sub(r'(?m)^#+.*$', '', paragraph).strip()
            if not paragraph:
                continue
            # Retrieved data never gains authority to issue commands or expose
            # secrets; suspicious fragments are omitted, not executed.
            if re.search(r'ignora.*instruccion|system prompt|revela.*(?:clave|prompt)|password|contrase[nñ]a|api[_ -]?key|\b(?:drop table|delete from)\b', normalize(paragraph)):
                continue
            clean = spoken_text(paragraph)
            if not clean:
                continue
            body_words = set(re.findall(r'[a-z]+', normalize(clean)))
            heading_norm = normalize(heading)
            heading_words = set(re.findall(r'[a-z]+', heading_norm))
            score = len(tokens & body_words) * 3 + len(tokens & heading_words) * 5
            if 'devol' in query or 'garant' in query:
                if re.search(r'dinero|reembolso|cuando|plazo|tiempo', query):
                    score += 25 if re.search(r'dinero|respuesta|plazo|devolucion', heading_norm) else 0
                else:
                    score += 30 if re.search(r'reporto|averiado|equivocado|incompleto|garantia', heading_norm) else 0
                    score -= 10 if 'dinero' in heading_norm else 0
                if 'cambi' not in query and 'cambiar' in heading_norm:
                    score -= 30
            if score:
                # Discard incomplete leading/trailing sentences, including
                # legacy chunks starting with "orrador...". Never invent the
                # missing beginning of a sentence.
                sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])', clean)
                             if re.match(r'^[A-ZÁÉÍÓÚÑ¿¡]', part.strip()) and re.search(r'[.!?]$', part.strip())]
                if sentences:
                    candidates.append((score, -index, sentences))
    if not candidates:
        return 'No encontré fragmentos verificados que respondan esa pregunta en los documentos de la tienda.'
    _, _, sentences = max(candidates, key=lambda item: (item[0], item[1]))
    return ' '.join(sentences[:3])


def render_record(row):
    if not isinstance(row, dict):
        return ''
    parts = []
    for key, label in (('numero', 'Pedido'), ('sku', 'SKU'), ('nombre', 'Producto'),
                       ('ciudad', 'Ciudad'), ('fecha', 'Fecha'),
                       ('transportadora', 'Transportadora'), ('cantidad', 'Cantidad'),
                       ('stock', 'Stock'), ('unidades', 'Unidades'), ('total_pedidos', 'Pedidos'),
                       ('conteo', 'Pedidos'), ('pedidos', 'Pedidos'), ('pedido_numero', 'Pedido'),
                       ('total_registrados', 'Pedidos registrados'), ('total_abiertas', 'Novedades abiertas'),
                       ('unidades_vendidas', 'Unidades vendidas'), ('tipo', 'Novedad')):
        value = row.get(key)
        if value is not None and isinstance(value, (str, int, float, Decimal)):
            parts.append(f'{label}: {value}')
    for key, label in (('precio_venta', 'Precio'), ('precio_unitario', 'Precio unitario'),
                       ('total', 'Total'), ('total_cop', 'Total'), ('ventas', 'Ventas'), ('ventas_cop', 'Ventas'), ('flete', 'Flete')):
        value = row.get(key)
        if key == 'total_cop' and row.get('total') is not None:
            continue
        if value is not None:
            formatted = (str(value) + ' COP' if key.endswith('_cop') and isinstance(value, str)
                         else money(value))
            if formatted:
                parts.append(f'{label}: {formatted}')
    return '; '.join(parts)


def grounded_answer(result, user_text=''):
    """Only tool-authored phrases or labelled database values may leave here."""
    result = public_data(result)
    if 'fragmentos' in result:
        return knowledge_answer(result['fragmentos'], user_text)
    # Workflow fields take precedence over technical status and partial data.
    questions = {
        'cliente': '¿A nombre de quién va el pedido?', 'cliente_nombre': '¿A nombre de quién va el pedido?',
        'ciudad': '¿En qué ciudad se entrega?', 'direccion': '¿A qué dirección lo enviamos y a qué teléfono?',
        'telefono': '¿A qué teléfono te contactamos?', 'productos': '¿Qué productos y cantidades agrego?',
        'metodo_pago': '¿Contra entrega o pagado por anticipado?',
        'transportadora': '¿Qué transportadora prefieres: la más barata, la más rápida u otra?',
    }
    suggested = spoken_text(result.get('pregunta_sugerida'))
    if suggested:
        return suggested
    missing = result.get('faltan')
    if isinstance(missing, list) and missing and missing[0] in questions and not result.get('error'):
        question = questions[missing[0]]
        correction = spoken_text(result.get('mensaje'))
        if correction and correction.startswith(('Entendí', 'Corregí', 'Ajusté')):
            return correction.rstrip('.') + '. ' + question
        return question
    scoped = requested_answer(result, user_text)
    if scoped:
        return scoped
    for key in ('error', 'resumen_hablado', 'pregunta_sugerida', 'resumen_natural', 'message', 'resumen', 'explicacion'):
        value = result.get(key)
        clean = spoken_text(value)
        if clean:
            return clean[:5500]
    pending = result.get('pendingAction')
    if isinstance(pending, dict) and spoken_text(pending.get('description')):
        return spoken_text(pending['description']) + ' Revisa la tarjeta antes de confirmar.'
    rows = []
    if isinstance(result.get('pedido'), dict):
        rows.append(render_record(result['pedido']))
    elif isinstance(result.get('producto'), dict):
        rows.append(render_record(result['producto']))
    elif render_record(result):
        rows.append(render_record(result))
    for key in ('items', 'productos', 'top_productos', 'pedidos', 'novedades', 'opciones', 'tarifas', 'transportadoras', 'ciudades'):
        values = result.get(key)
        if isinstance(values, list):
            rows.extend(render_record(row) for row in values[:5])
    rows = [row for row in rows if row]
    if result.get('encontrado') is False or result.get('estado') in ('no_encontrado', 'opciones'):
        return spoken_text(result.get('mensaje')) or 'No encontré ese dato en el sistema.'
    if rows:
        prefix = f"Período: {result['periodo']}. " if result.get('periodo') else ''
        return prefix + '. '.join(rows[:3])[:5000] + '.'
    return spoken_text(result.get('mensaje')) or UNKNOWN


def check_reply(reply, tool_results, user_text):
    """Return grounded Spanish text, with no I/O and no additional LLM calls.

    Context must contain original tool results, never previous assistant prose
    or user assertions. All business answers are rendered from evidence even
    when token checks pass, preventing invented names and swapped associations.
    """
    reply = str(reply or '').strip()
    if is_greeting(user_text):
        return 'Hola. ¿En qué puedo ayudarte con la tienda?'
    results = evidence_results(tool_results)
    normalized_user = normalize(user_text)
    if results:
        # Unsupported dates must not silently become today's/current-week data.
        wanted = ('ayer' if re.search(r'\bayer\b', normalized_user) else
                  'semana_anterior' if re.search(r'semana (?:pasada|anterior)', normalized_user) else None)
        if wanted and not any(normalize(r.get('periodo', '')) == wanted for r in results):
            return 'No tengo datos verificados para ese período en esta consulta. Puedo consultar hoy, esta semana o este mes.'
        if re.search(r'\b(?:en|ano|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+(?:de\s+)?(?:19\d{2}|20\d{2})\b', normalized_user):
            requested_years = set(re.findall(r'\b(?:19\d{2}|20\d{2})\b', normalized_user))
            if not requested_years <= set(re.findall(r'\b(?:19\d{2}|20\d{2})\b', json.dumps(results, default=str))):
                return UNKNOWN
        if re.search(r'\bduplicad|\bguia\b', normalized_user):
            required = 'duplicad' if 'duplicad' in normalized_user else 'guia'
            if required not in normalize(json.dumps(results, default=str)):
                return UNKNOWN
    if not results:
        # Questions and refusals carry no business assertion. Keep only fixed
        # server prompts; arbitrary model questions may themselves imply facts.
        safe_questions = (
            '¿A nombre de quién va el pedido?', '¿En qué ciudad se entrega?',
            '¿Qué productos y cantidades agrego?', '¿A qué dirección lo enviamos y a qué teléfono?',
            '¿A qué teléfono te contactamos?', '¿Contra entrega o pagado por anticipado?',
            'Para cotizar el envío, dime la ciudad y el departamento de destino.',
            '¿Qué transportadora prefieres: la más barata, la más rápida u otra?',
        )
        question = reply.removeprefix('Disculpa la confusión. ')
        if question in safe_questions:
            return reply
        if not BUSINESS.search(user_text) and normalize(user_text).strip(' ¡!¿?.') in ('hola', 'buenos dias', 'buenas tardes', 'gracias'):
            return 'Hola. Puedo ayudarte con pedidos, productos y ventas de la tienda.'
        return UNKNOWN
    # Extract and validate for callers that inspect claim support. Rendering is
    # mandatory even with zero missing tokens: lexical presence is insufficient.
    unsupported_claims(reply, results)
    return spoken_text(grounded_answer(results[-1], user_text)) or UNKNOWN
