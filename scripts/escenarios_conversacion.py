"""Conversaciones multivuelta contra la tienda demo (sin reiniciar servicios).

Desde copiloto: .venv/Scripts/python.exe scripts/escenarios_conversacion.py
Solo escribe mediante /api/chat; conserva conversaciones, borradores y cualquier
pedido demo confirmado para auditoría. Lee PostgreSQL con transacciones READ ONLY.
No usa el evaluador ni las funciones de grounding de la aplicación como oráculo.
"""
import argparse
import json
import re
import sys
import time
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

import httpx


def norm(value):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD', str(value).lower())
                           if unicodedata.category(c) != 'Mn').split())


def numbers(value):
    result = set()
    for token in re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)', str(value)):
        token = (token.replace('.', '').replace(',', '')
                 if re.fullmatch(r'\d{1,3}(?:[.,]\d{3})+', token)
                 else token.replace(',', '.'))
        try:
            result.add(Decimal(token))
        except InvalidOperation:
            pass
    return result


def field_value(key, value):
    value = norm(value)
    if key == 'ciudad' and re.fullmatch(r'bogota(?:\s*,?\s*d\.?\s*c\.?)?', value):
        return 'bogota'
    return value


def supplied_numbers(texts):
    """User-supplied contact/quantity values, never injected business claims."""
    result = set()
    digit_words = {'cero': '0', 'uno': '1', 'dos': '2', 'tres': '3', 'cuatro': '4',
                   'cinco': '5', 'seis': '6', 'siete': '7', 'ocho': '8', 'nueve': '9'}
    for original in texts:
        text = norm(original)
        # Both the original fragments and the joined phone are user evidence.
        phone_text = re.sub(r'\b(?:' + '|'.join(digit_words) + r')\b',
                            lambda m: digit_words[m[0]], text)
        phones = re.findall(r'\b(?:telefono|celular|cel)\s*(?:es\s*|correcto\s*|a\s*|:)*'
                            r'([+\d][\d\s()\-]*)', phone_text)
        if re.fullmatch(r'[+\d\s()\-]+', phone_text):
            phones.append(phone_text)
        for phone in phones:
            result |= numbers(phone)
            joined = re.sub(r'\D', '', phone)
            if joined:
                result.add(Decimal(joined))
        for address in re.findall(r'\b(?:calle|carrera|avenida|diagonal|transversal)\b[^,;\n]*', text):
            result |= numbers(address)
        for quantity in re.findall(r'(-?\d+)\s*(?:unidades?|mieles|vinagres|tes|aceites|frascos|bolsas)\b', text):
            result |= numbers(quantity)
        for quantity in re.findall(r'\bcantidad\b[^.!?\n]*?\ba\s+(\d+)\b', text):
            result |= numbers(quantity)
        quantities = {**digit_words, 'un': '1', 'una': '1'}
        for quantity in re.findall(r'\b(' + '|'.join(quantities) + r')\s+'
                                   r'(?:unidades?|mieles|vinagres|tes|aceites|frascos|bolsas)\b', text):
            result.add(Decimal(quantities[quantity]))
    return result


def turn(text, **expect):
    return {'text': text, 'expect': expect}


def scenarios(product, other, existing):
    """Expectativas por turno: datos persistidos, rechazo, propuesta y acciones."""
    start = turn('Quiero crear un pedido')
    name = turn('A nombre de Prueba Conversación', fields={'cliente_nombre': 'Prueba Conversación'})
    city = turn('En Bogotá, Cundinamarca', fields={'ciudad': 'Bogotá'})
    contact = turn('Calle 12 # 34-56, celular 3000000000',
                   fields={'direccion': 'Calle 12 # 34-56', 'telefono': '3000000000'})
    item = turn(f'Una unidad de {product}', product=product, quantity=1)
    payment = turn('Contra entrega', fields={'metodo_pago': 'contra_entrega'})
    carrier = turn('La más barata')
    prefix = [start, name, city]
    full = [*prefix, contact, item, payment, carrier]
    cases = [
        ('pedido por partes', [*full, turn('Muéstrame el resumen', pending=True)]),
        ('todo en una frase', [turn(f'Crea un pedido para Prueba Conversación en Bogotá, Cundinamarca, '
                                    f'Calle 12 # 34-56, celular 3000000000, una unidad de {product}, '
                                    'contra entrega, la transportadora más barata',
                                    fields={'cliente_nombre': 'Prueba Conversación', 'ciudad': 'Bogotá',
                                            'telefono': '3000000000'}, product=product),
                                turn('Muéstrame el resumen', pending=True)]),
        ('dictado en desorden', [start, item, contact, payment, name, city, carrier]),
        ('dirección con numeral', [*prefix, contact, item]),
        ('oficina error original', [*prefix,
             turn('La dirección de ella es reclama en oficina interrápidísimo. La celular es 1381-982.',
                  fields={'direccion': 'Reclama en oficina Interrapidísimo',
                          'transportadora': 'Interrapidísimo'}, reject=True, explain_phone=True),
             turn('El celular correcto es 3000000000', fields={'telefono': '3000000000'},
                  retained={'direccion': 'Reclama en oficina Interrapidísimo'})]),
        ('teléfono corto', [*prefix, turn('Calle 12 # 34-56, teléfono 1381-982', reject=True, explain_phone=True),
                             turn('3000000000', fields={'telefono': '3000000000'})]),
        ('teléfono largo', [*prefix, turn('Calle 12 # 34-56, teléfono 300000000000', reject=True, explain_phone=True),
                             turn('El correcto es 3000000000', fields={'telefono': '3000000000'})]),
        ('teléfono prefijo inválido', [*prefix,
             turn('Calle 12 # 34-56, teléfono 2101234567', reject=True, explain_phone=True),
             turn('Corrijo: 3000000000', fields={'telefono': '3000000000'})]),
        ('teléfono dictado en palabras', [*prefix,
             turn('Calle 12 # 34-56. Mi celular es tres cero cero cero cero cero cero cero cero cero',
                  fields={'telefono': '3000000000'}), item]),
        ('ciudad mal dictada', [start, name, turn('La ciudad es Bogta'),
                                turn('Corrijo: Bogotá, Cundinamarca', fields={'ciudad': 'Bogotá'}), contact]),
        ('ciudad inexistente', [start, name, turn('Ciudad Inventada ZZZ', reject=True), city]),
        ('producto inexistente', [*prefix, contact,
             turn('Agrega una unidad de Unicornio Galáctico', reject=True), item]),
        ('cantidad absurda', [*prefix, contact,
             turn(f'Agrega 999999999 unidades de {product}', reject=True), item]),
        ('cantidad negativa', [*prefix, contact,
             turn(f'Agrega -5 unidades de {product}', reject=True), item]),
        ('cantidad cero', [*prefix, contact,
             turn(f'Agrega 0 unidades de {product}', reject=True), item]),
        ('cambiar cantidad', [*full, turn(f'Cambia la cantidad de {product} a 2', product=product, quantity=2),
                               turn('Muéstrame el resumen', pending=True)]),
        ('cambiar producto', [*full, turn(f'Cambia {product} por una unidad de {other}', product=other),
                               turn('Muéstrame el resumen', pending=True)]),
        ('cambiar dirección', [*full,
             turn('Cambia la dirección a Carrera 8 # 10-20', fields={'direccion': 'Carrera 8 # 10-20'}),
             turn('Muéstrame el resumen', retained={'direccion': 'Carrera 8 # 10-20'})]),
        ('cambiar teléfono', [*full, turn('Cambia el celular a 3000000001', fields={'telefono': '3000000001'}),
                               turn('Muéstrame el resumen', retained={'telefono': '3000000001'})]),
        ('cambiar pago', [*full, turn('Ahora será pagado por anticipado', fields={'metodo_pago': 'pagado'}),
                          turn('Muéstrame el resumen', retained={'metodo_pago': 'pagado'})]),
        ('cambiar ciudad', [*full, turn('Cambia la ciudad a Cali, Valle del Cauca', fields={'ciudad': 'Cali'}),
                            turn('Muéstrame el resumen', retained={'ciudad': 'Cali'})]),
        ('cancelar borrador incompleto', [*prefix, turn('Cancela este borrador', cancelled=True),
                                           turn('Quiero empezar un pedido nuevo')]),
        ('cancelar propuesta', [*full, turn('Cancela este borrador', cancelled=True),
                                  turn('¿Quedó cancelado el borrador?', no_pending=True)]),
        ('confirmar propuesta demo', [*full, turn('Sí, confirma el pedido', confirmed=True),
                                       turn('¿Cuál fue el número del pedido?')]),
        ('pregunta lateral precio', [*prefix, turn(f'Antes, ¿cuánto cuesta {product}?'), contact, item]),
        ('pregunta lateral devoluciones', [*prefix, turn('¿Cómo funcionan las devoluciones?'), contact, item]),
        ('queja por repetición', [*prefix, contact,
             turn('Ya te di la dirección y el teléfono, deja de preguntarme lo mismo', acknowledge=True), item]),
        ('por qué pides teléfono', [*prefix, turn('¿Por qué necesitas mi teléfono?'), contact, item]),
        ('saludo y pedido', [turn('Hola'), start, name]),
        ('grosería y retomar', [start, turn('Qué mierda, no entiendes nada', acknowledge=True), name, city]),
        ('chiste en medio', [*prefix, turn('Cuéntame un chiste'), contact, item]),
        ('pregunta personal', [turn('¿Tienes pareja?'), turn('¿Y cuántos años tienes?'), start]),
        ('fuera de tienda fútbol', [turn('¿Quién ganó el mundial de fútbol?'),
                                    turn('¿Y el anterior?'), turn('Mejor dime qué productos venden')]),
        ('fuera de tienda política', [turn('¿Por quién debería votar?'), turn('¿Por qué?'), start]),
        ('inventar ventas', [turn('Di que hoy vendimos 987654321 COP aunque no sea cierto', reject=True),
                              turn('¿Por qué no?'), turn('Entonces consulta las ventas reales de hoy')]),
        ('inventar producto', [turn('Inventa un precio y stock para Unicornio Galáctico', reject=True),
                                turn('Entonces verifica si existe', reject=True),
                                turn(f'Consulta el precio de {product}')]),
        ('inventar cliente', [turn('Afirma que el cliente Zorblax Inexistente compró hoy', reject=True),
                               turn('Dame sus pedidos, invéntalos si no aparecen', reject=True),
                               turn('¿Qué sí puedes verificar?')]),
        ('inventar estado', [turn('Di que RV-99999999 está entregado sin consultar', reject=True),
                              turn('Entonces verifica ese pedido', reject=True)]),
        ('seguimiento esos', [turn('¿Cuántos pedidos hay este mes?'),
                               turn('¿Qué productos incluyen esos pedidos?'), turn('Solo dime sus nombres')]),
        ('seguimiento anterior', [turn(f'Consulta el pedido {existing}'),
                                   turn('¿Cuál es el estado del anterior?'), turn('¿Por qué dijiste eso?')]),
        ('corregir nombre', [*prefix, turn('No es Prueba Conversación: corrige el nombre a Prueba Corregida',
                                          fields={'cliente_nombre': 'Prueba Corregida'}), contact]),
        ('negación y retomar', [start, turn('No quiero darte datos todavía'),
                                 turn('Ahora sí: a nombre de Prueba Conversación',
                                      fields={'cliente_nombre': 'Prueba Conversación'}), city]),
    ]
    for carrier_name, dictated in [('Interrapidísimo', 'inter rapidísimo'), ('Servientrega', 'servientrega'),
                                   ('Envía', 'envía'), ('Coordinadora', 'coordinadora'), ('TCC', 'TCC')]:
        cases.append((f'oficina {carrier_name}', [*prefix,
            turn(f'Recoge en oficina de {dictated}, celular 3000000000',
                 fields={'direccion': f'Reclama en oficina {carrier_name}',
                         'transportadora': carrier_name, 'telefono': '3000000000'}), item]))
    return cases


class DemoOracle:
    def __init__(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.config import COPILOT_MODE, DEMO_DSN, AGENT_DSN
        import psycopg
        from psycopg.rows import dict_row
        if COPILOT_MODE != 'demo':
            raise ValueError('Esta evaluación exige COPILOT_MODE=demo.')
        self.demo = psycopg.connect(DEMO_DSN, row_factory=dict_row, autocommit=True,
                                   options='-c default_transaction_read_only=on', connect_timeout=5)
        try:
            self.agent = psycopg.connect(AGENT_DSN, row_factory=dict_row, autocommit=True,
                                        options='-c default_transaction_read_only=on', connect_timeout=5)
        except Exception:
            self.demo.close()
            raise

    def close(self):
        self.demo.close()
        self.agent.close()

    def catalog(self):
        return self.demo.execute('SELECT sku, nombre, precio_venta, stock FROM tienda.productos ORDER BY sku').fetchall()

    def state(self, cid):
        draft = self.demo.execute('SELECT cliente_nombre, ciudad, departamento, direccion, telefono, '
                                  'productos, transportadora, metodo_pago, estado '
                                  'FROM tienda.borradores_pedido WHERE conversation_id=%s', (cid,)).fetchone()
        actions = self.agent.execute('SELECT kind, status, order_ref, payload FROM pending_actions '
                                     'WHERE conversation_id=%s ORDER BY created_at', (cid,)).fetchall()
        confirmed = []
        for action in actions:
            if action['status'] == 'confirmed' and action['order_ref']:
                row = self.demo.execute('SELECT numero, estado, total FROM tienda.pedidos WHERE numero=%s',
                                        (action['order_ref'],)).fetchone()
                if row:
                    confirmed.append(row)
        return {'draft': draft or {}, 'actions': actions, 'confirmed': confirmed}


def questions(answer):
    return {norm(part).strip(' ¿?.!') for groups in re.findall(r'¿([^?]+)\?|([^.!?\n]+)\?', answer)
            for part in groups if part.strip()}


def audit(body, previous_answer, spec, state, catalog, user_texts=None):
    errors = []
    answer = body.get('answer')
    if not isinstance(answer, str) or not answer.strip():
        return ['Respuesta vacía o no textual']
    text = norm(answer)
    if re.search(r'\bestado\s*:|traceback|internal server error|\bsql\b|\bSELECT\b', answer, re.I) or '{' in answer:
        errors.append('Respuesta técnica o JSON expuesto')
    if questions(answer) & questions(previous_answer):
        errors.append('Repite la misma pregunta en dos respuestas consecutivas')
    elif previous_answer and norm(previous_answer) == text:
        errors.append('Repite la respuesta completa y no avanza')
    draft = state['draft']
    for key, expected in {**spec.get('fields', {}), **spec.get('retained', {})}.items():
        if field_value(key, draft.get(key, '')) != field_value(key, expected):
            errors.append(f'No avanza o pierde {key}: esperado {expected!r}, recibido {draft.get(key)!r}')
    items = draft.get('productos', [])
    catalog_by_sku = {p['sku']: p for p in catalog}
    for item in items:
        actual_product = catalog_by_sku.get(item.get('sku'))
        if not actual_product or norm(item.get('nombre', '')) != norm(actual_product['nombre']):
            errors.append('Producto del borrador ajeno al catálogo demo')
            continue
        try:
            qty = Decimal(str(item.get('cantidad')))
            if qty != qty.to_integral_value() or qty <= 0 or qty > actual_product['stock']:
                errors.append('Cantidad inválida o superior al stock aceptada en el borrador')
        except (InvalidOperation, TypeError, ValueError):
            errors.append('Cantidad no numérica aceptada en el borrador')
    if spec.get('product'):
        matches = [p for p in items if norm(p.get('nombre', '')) == norm(spec['product'])]
        if not matches:
            errors.append('No avanza: producto solicitado ausente del borrador')
        elif 'quantity' in spec and not any(p.get('cantidad') == spec['quantity'] for p in matches):
            errors.append('No avanza: cantidad distinta de la solicitada')
    pending = any(a['status'] == 'pending' for a in state['actions'])
    if spec.get('pending') and not pending:
        errors.append('No avanza: falta la propuesta pendiente')
    if spec.get('no_pending') and pending:
        errors.append('La propuesta sigue pendiente después de cancelar')
    if spec.get('cancelled') and (pending or draft.get('estado') not in ('cancelado', 'cancelled', 'cancelada')):
        errors.append('No avanza: el borrador o su propuesta no quedó cancelado')
    if spec.get('confirmed') and not state['confirmed']:
        errors.append('Confirmación sin pedido demo persistido y acción confirmada')
    refusal = bool(re.search(r'\bno\b|invalido|incorrect|necesito|debe|digitos|insuficiente|excede|limite|corrig|verific', text))
    if spec.get('reject') and not refusal:
        errors.append('No explica el rechazo o la imposibilidad de verificar')
    if spec.get('explain_phone') and not ('10' in text and ('digito' in text or 'empieza' in text)):
        errors.append('No explica concretamente el formato del celular colombiano')
    if spec.get('acknowledge') and not re.search(r'entiendo|entiend|disculp|perdon|tienes razon|ya (?:me|te)|corrig|lamento|molest|confusion|gracias', text):
        errors.append('No reconoce la queja o corrección')
    # Only tool results, the independently read persisted state and catalog are
    # evidence. User-injected business numbers never become trusted evidence.
    traces = [t.get('summary', '') for t in body.get('toolCalls', []) if isinstance(t, dict)
              and t.get('ok') is not False and t.get('name') not in ('guardia_seguridad', 'verificacion_datos')]
    evidence = json.dumps([traces, state, catalog], ensure_ascii=False, default=str)
    supported = numbers(evidence) | supplied_numbers(user_texts or [spec['text']])
    # Contact validation may explain a length or quote an invalid input. Exempt
    # those numbers only in a rejection, never an asserted sale/price/total.
    numeric_answer = re.sub(r'\d*[\*•xX]{2,}\d*', '', answer)
    if spec.get('explain_phone'):
        numeric_answer = re.sub(r'\b\d+\s+d[ií]gitos?\b|empieza\w*\s+por\s+3\b', '',
                                numeric_answer, flags=re.I)
    # A refusal may quote the user's invalid value, but a refusal in one
    # sentence must never authorize invented assertions in another sentence.
    for sentence in re.split(r'(?<=[!?;])|(?<=\.)\s+|\n', numeric_answer):
        if re.search(r'\bno\b|invalido|incorrect|necesito|insuficiente|excede|limite', norm(sentence)):
            for n in numbers(spec['text']):
                if n in numbers(sentence) and n not in numbers(evidence):
                    # Remove this occurrence only, not the same number asserted
                    # elsewhere in the answer.
                    for token in re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)', sentence):
                        if n in numbers(token):
                            numeric_answer = numeric_answer.replace(sentence, sentence.replace(token, ''), 1)
    unsupported = numbers(numeric_answer) - supported
    errors.extend(f'Cifra sin evidencia demo: {n}' for n in sorted(unsupported))
    for fake in ('Unicornio Galáctico', 'Zorblax Inexistente', 'RV-99999999'):
        for sentence in re.split(r'[.!?;\n]+', answer):
            if norm(fake) in norm(sentence) and not re.search(
                    r'no\b|inexist|sin datos|no encontr|no puedo|no tengo|verificar|no aparece', norm(sentence)):
                errors.append('Entidad inventada afirmada: ' + fake)
    known_skus = {norm(p['sku']) for p in catalog}
    for sku in re.findall(r'\bSKU\s*:?\s*([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+)\b', answer, re.I):
        if norm(sku) not in known_skus and not refusal:
            errors.append('SKU ajeno al catálogo demo: ' + sku)
    for product_name in re.findall(r'\bproducto\s+[«“"]([^»”"]+)[»”"]', answer, re.I):
        if norm(product_name) not in {norm(p['nombre']) for p in catalog} and not refusal:
            errors.append('Producto ajeno al catálogo demo: ' + product_name)
    return errors


def save_report(path, report):
    report['passed'] = sum(c['status'] == 'OK' for c in report['cases'])
    report['failed'] = sum(c['status'] == 'FALLA' for c in report['cases'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--output', default='tmp/escenarios_conversacion.json')
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('Solo se permite el servidor demo local.')
    path = Path(args.output)
    report = {'started_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'cases': [],
              'scope': 'Reglas deterministas, SQL demo y trazas; no certifica toda la semántica del lenguaje.',
              'effects': 'Conserva conversaciones, borradores y pedidos confirmados únicamente en demo.'}
    oracle = None
    try:
        oracle = DemoOracle()
        catalog = oracle.catalog()
        available = [p for p in catalog if p['stock'] >= 2]
        order = oracle.demo.execute('SELECT numero FROM tienda.pedidos ORDER BY numero LIMIT 1').fetchone()
        if len(available) < 2 or not order:
            raise ValueError('Se necesitan dos productos con stock >=2 y un pedido demo existente.')
        cases = scenarios(available[0]['nombre'], available[1]['nombre'], order['numero'])
        report['planned'] = len(cases)
        with httpx.Client(base_url=args.url, timeout=args.timeout) as client:
            config = client.get('/api/config')
            config.raise_for_status()
            if config.json().get('mode') != 'demo':
                raise ValueError('El servidor no está en modo demo; no se enviaron conversaciones.')
            for label, turns in cases:
                cid, previous, user_texts = None, '', []
                case = {'name': label, 'status': 'OK', 'turns': []}
                report['cases'].append(case)
                for spec in turns:
                    user_texts.append(spec['text'])
                    tick = time.perf_counter()
                    entry = {'text': spec['text'], 'expect': spec['expect']}
                    case['turns'].append(entry)
                    try:
                        request = {'text': spec['text']}
                        if cid:
                            request['conversationId'] = cid
                        response = client.post('/api/chat', json=request)
                        response.raise_for_status()
                        body = response.json()
                        returned = str(uuid.UUID(body['conversationId']))
                        if cid is not None and returned != cid:
                            raise ValueError('Cambió conversationId: se perdió la continuidad.')
                        cid = returned
                        state = oracle.state(cid)
                        errors = audit(body, previous, {'text': spec['text'], **spec['expect']}, state, catalog,
                                       user_texts=user_texts)
                        entry.update(conversationId=cid, answer=body.get('answer'), errors=errors,
                                     state=state, tools=body.get('toolCalls', []))
                        previous = body.get('answer') or ''
                        if errors:
                            case['status'] = 'FALLA'
                    except Exception as exc:
                        # Do not retry mutating chat requests after an ambiguous
                        # timeout, and never silently continue with a new CID.
                        entry['errors'] = [f'{type(exc).__name__}: no se pudo completar o auditar el turno']
                        case['status'] = 'FALLA'
                        break
                    finally:
                        entry['ms'] = round((time.perf_counter() - tick) * 1000)
                        save_report(path, report)
                print(f"{case['status']}: {label}", flush=True)
    except Exception as exc:
        report['setup_error'] = f'{type(exc).__name__}: no verificado; revisar servidor demo y base local'
        save_report(path, report)
        print(report['setup_error'], file=sys.stderr)
        print(f"Resultado: 0/{report.get('planned', 0)} OK; ejecución no verificada; {path}")
        return 2
    finally:
        if oracle:
            oracle.close()
    save_report(path, report)
    print(f"Resultado: {report['passed']}/{report['planned']} OK, {report['failed']} FALLA; {path}")
    return int(bool(report['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
