"""Módulo de guardia de seguridad (guardrails) previo al modelo de lenguaje."""
import re
import time
import httpx
from .config import OLLAMA_URL, LLM_MODEL


RESPUESTA_MANIPULACION = "No puedo hacer eso. Solo te ayudo con la operación de la tienda y no comparto datos personales ni cambio mis reglas."
RESPUESTA_FUERA_DE_DOMINIO = "Solo puedo ayudarte con la tienda: pedidos, ventas, envíos, productos y clientes."

TERMINOS_NEGOCIO = (
    'recuerdame', 'recordarme', 'recordatorio', 'tarea', 'pendiente', 'reabastec', 'reposicion', 'reponer',
    'pedido', 'pedidos', 'venta', 'ventas', 'envio', 'envios', 'envío', 'envíos',
    'transportadora', 'transportadoras', 'producto', 'productos', 'stock', 'inventario',
    'cliente', 'clientes', 'novedad', 'novedades', 'devolucion', 'devoluciones', 'devolución',
    'flete', 'fletes', 'pago', 'pagos', 'tienda', 'metrica', 'metricas', 'métrica', 'métricas',
    'cotiza', 'cotizar', 'cotizacion', 'cotización', 'tarifa', 'tarifas', 'balance',
    'revision', 'revisión', 'recaudo', 'contra entrega', 'contraentrega', 'rv-',
    # Productos y categorías del catálogo demo y naturales
    'manzanilla', 'miel', 'coco', 'lavanda', 'colageno', 'colágeno', 'curcuma', 'cúrcuma',
    'jabon', 'jabón', 'moringa', 'vinagre', 'granola', 'mani', 'maní', 'romero', 'chia', 'chía',
    'te verde', 'té verde', 'tes verdes', 'tés verdes', 'aceite', 'aceites', 'mantequilla',
    'avena', 'almendra', 'almendras', 'infusion', 'infusiones',
    # Transportadoras y logística
    'interrapidisimo', 'interrapidísimo', 'envia', 'envía', 'coordinadora', 'servientrega', 'tcc',
    'veloces', '99 minutos', 'comercio', 'despacho', 'despachados', 'entregados', 'confirmados',
    'cancelados', 'borrador', 'catalogo', 'catálogo',
    'barata', 'barato', 'rapida', 'rápida', 'economica', 'económica',
    # Intenciones de orden, edición y confirmación
    'creado', 'creada', 'crear', 'confirma', 'confirmar', 'confirmado', 'confirmo', 'confirmalo', 'confírmalo',
    'cancela', 'cancelar', 'cancelado', 'cancelalo', 'cancélalo', 'anular', 'anula', 'descartar', 'descarta',
    'pendiente', 'propuesta', 'proponer', 'comprar', 'compra', 'orden', 'ordenes', 'órdenes',
    'dale', 'hazlo', 'procede', 'adelante', 'agrega', 'agregar', 'cambia', 'cambiar', 'ajusta', 'ajustar',
    # Ubicación y destino comunes en pedidos colombianos
    'ciudad', 'municipio', 'departamento', 'depto', 'direccion', 'dirección', 'destino',
    'bogota', 'bogotá', 'medellin', 'medellín', 'cali', 'barranquilla', 'cartagena',
    'pasto', 'pereira', 'manizales', 'bucaramanga', 'cucuta', 'cúcuta', 'ibague', 'ibagué',
    'orito', 'horito', 'putumayo', 'puto mayo', 'antioquia', 'nariño', 'narino',
    # Conversacionales propios del flujo de tienda
    'precio', 'precios', 'costo', 'costos', 'cuanto cuesta', 'cuánto cuesta', 'cuanto vale', 'cuánto vale',
    'unidad', 'unidades', 'ayuda', 'gracias', 'muchas gracias', 'perfecto', 'de acuerdo',
    'versiones', 'presentaciones', 'cantidades', 'tamanos', 'disponibilidad'
)

FUERA_DE_DOMINIO_PATTERNS = [
    (r'\b(?:futbol|fútbol|partido|partidos|champions|champions league|copa|liga|gol|goles|messi|ronaldo|barcelona|real madrid|uefa|libertadores|mundial|baloncesto|nba|tenis|beisbol)\b', 'deportes'),
    (r'\b(?:clima|tiempo hace|va a llover|llover|lluvia|temperatura|pronostico|pronóstico)\b', 'clima'),
    (r'\b(?:chiste|chistes|broma|bromas|adivinanza|poema|cuento|cancion|canción|musica|música|pelicula|película|cine|serie|netflix|receta|cocinar|horoscopo|horóscopo|signo del zodiaco)\b', 'entretenimiento'),
    (r'\b(?:politica|política|elecciones|presidente|senador|congreso|alcalde|votaciones|petro|uribe|reforma tributaria)\b', 'politica'),
    (r'\b(?:tarea de matematicas|tarea escolar|ecuacion|ecuación|programar en|codigo en c\+\+|quien descubrio america|quién descubrió américa)\b', 'tareas_ajenas'),
]


def normalize_text(text):
    t = str(text or '').lower()
    return (t.replace('á', 'a')
             .replace('é', 'e')
             .replace('í', 'i')
             .replace('ó', 'o')
             .replace('ú', 'u')
             .replace('ü', 'u'))


def is_order_feedback(text):
    norm = normalize_text(text)
    return bool(re.search(r'por que (?:me |te )?(?:sugieres|preguntas|pides|buscas)|en vez de|confusion|no (?:te )?entiendo|eso no|te equivoc|estas confund|no era|no te pedi|me entendiste mal|queja|ya te (?:di|dije)|deja de preguntar|no entiendes|mierda|carajo|puta|pendej|malparid', norm))


def is_order_correction(text):
    norm = normalize_text(text)
    return bool('?' not in norm and '¿' not in norm and re.search(
        r'\b(?:cambia|corrige|reemplaza|mejor|no[, ]+era|(?:el |la )?(?:nombre|ciudad|direccion|telefono|celular)\s+(?:es|era))\b', norm))


def is_short_field_reply(text):
    norm = normalize_text(text).strip()
    return bool(norm and len(norm.split()) <= 4 and '?' not in text and '¿' not in text
                and not is_order_feedback(text)
                and not re.match(r'^(?:por que|que|como|cuando|cual|puedo|hay)\b', norm))


def is_order_contact_input(text):
    norm = normalize_text(text)
    # Solo capturar datos aportados; una petición de leer datos conserva las barreras.
    if re.search(r'\b(?:dame|revela|muestra|lista|lee|dime|cual|cuales)\b', norm):
        return False
    return bool(re.search(r'\b(?:calle|carrera|cra|cl|diagonal|transversal|avenida)\s+\d', norm)
                or re.search(r'\ben\s+(?:la\s+)?oficina\b', norm)
                or re.search(r'\b(?:telefono|celular|cel|contacto)\b.*\b(?:\d+|cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b', norm)
                or re.fullmatch(r'[\d\s+().-]+', norm)
                or re.fullmatch(r'(?:(?:cero|un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)[\s,.-]*){4,}', norm)
                or re.search(r'(?<!\d)(?:\+?57[ -]?)?3(?:[ -]?\d){9}(?!\d)', norm))


def check_manipulacion(norm_text, *, allow_order_contact=False):
    if (re.search(r'\b(?:inventa(?:los|las)?|inventar|finge|sup[oó]n)\b|aunque no sea cierto|\b(?:di|afirma)\b.*(?:sin consultar|cliente.*compr[oó])', norm_text)
            and re.search(r'venta|vend|producto|stock|precio|cliente|pedido|rv-', norm_text)):
        return 'inventar_datos'
    # 1. Saltar / ignorar / eludir reglas, directivas, instrucciones o prompt
    if re.search(r'\b(?:saltar(?:te)?|ignora(?:r)?|olvida(?:r)?|omiti(?:r)?|desactiva(?:r)?|bypassea(?:r)?)\b.*?\b(?:instruccion(?:es)?|regla(?:s)?|prompt|directiva(?:s)?|restriccion(?:es)?|filtro(?:s)?|sistema)\b', norm_text):
        return 'saltar_reglas'
    if re.search(r'\b(?:ignora|olvida)\s+(?:todo\s+)?(?:lo\s+anterior|tus\s+instrucciones|tus\s+reglas)\b', norm_text):
        return 'saltar_reglas'

    # 2. Actuar como / cambio de personalidad / jailbreak / modo DAN o desarrollador
    if re.search(r'\b(?:actua|actu[ae]s|finge|comportate|eres ahora|ahora eres)\b.*?\b(?:como|un|una|dan|jailbreak|desarrollador|dios|pirata|asistente sin filtros)\b', norm_text):
        return 'actua_como'
    if re.search(r'\b(?:modo\s+(?:desarrollador|dios|dan|developer))\b', norm_text):
        return 'modo_desarrollador'

    # 3. Revelar prompt de sistema o credenciales
    if re.search(r'\b(?:muestrame|revela|muestra|ensename|dame|escribe|imprime)\b.*?\b(?:(?:tu\s+)?(?:prompt|instrucciones|directivas|reglas\s+del\s+sistema)|system\s+prompt)\b', norm_text):
        return 'revelar_prompt'
    if 'prompt de sistema' in norm_text or 'instrucciones del sistema' in norm_text or 'system prompt' in norm_text:
        return 'revelar_prompt'

    # 4. Solicitud de datos personales / sensibles (teléfonos, correos, cédulas, contraseñas)
    if not allow_order_contact and re.search(r'\b(?:telefonos?|direcci[oó]n(?:es)?(?:\s+completas?)?|correos?|emails?|c[eé]dulas?|passwords?|contrase[nñ]as?|claves?)\b.*?\b(?:(?:de|del)\s+(?:los\s+)?clientes?|de\s+usuarios?|de\s+la\s+base|de\s+datos)\b', norm_text):
        return 'pedir_datos_privados'
    if re.search(r'\b(?:dame|revela|muestra|lista)\b.*?\b(?:telefonos?|correos?|c[eé]dulas?|passwords?|contrase[nñ]as?)\b', norm_text):
        return 'pedir_datos_privados'

    # 5. SQL destructivo o inyección de comandos
    if re.search(r'\b(?:drop\s+table|delete\s+from|truncate\s+table|borra(?:r)?\s+(?:todos?\s+los?\s+)?(?:pedidos?|clientes?|productos?|tablas?|la\s+base)|vaciar\s+la\s+base|;\s*--|\bunion\s+select\b)\b', norm_text):
        return 'sql_destructivo'

    return None


def check_fuera_de_dominio_rapido(norm_text):
    if re.search(r'tienes pareja|cuantos anos tienes|tu edad|eres humano|eres casad|tu familia|donde vives|como te llamas|eres hombre|eres mujer|deberia votar', norm_text):
        return 'personal_o_ajeno'
    has_business = any(term in norm_text for term in TERMINOS_NEGOCIO)
    if not has_business:
        words = set(re.findall(r'\b[a-z0-9_]+\b', norm_text))
        if any(w in words for w in ('te', 'tes', 'miel', 'coco', 'chia', 'mani', 'orden', 'flete', 'pago')):
            has_business = True

    for pattern, cat in FUERA_DE_DOMINIO_PATTERNS:
        if re.search(pattern, norm_text):
            if not has_business:
                return cat
            # Categorías inequívocas como chistes o clima no se confunden con pedidos
            if cat in ('chiste', 'clima', 'deportes') and not any(t in norm_text for t in ('pedido', 'pedidos', 'stock', 'tienda')):
                return cat
    return None


def classify_fuera_de_dominio_llm(raw_text):
    prompt = f'''Responde estrictamente con una sola palabra: SI o NO.
¿La siguiente consulta del usuario está relacionada con la operación de una tienda en línea (pedidos, productos, envíos, clientes, ventas, políticas o inventario)?
Consulta: "{raw_text[:200]}"
Respuesta:'''
    try:
        with httpx.Client(timeout=4) as client:
            res = client.post(f'{OLLAMA_URL}/api/generate', json={
                'model': LLM_MODEL, 'prompt': prompt, 'stream': False, 'think': False,
                'options': {'num_predict': 3, 'temperature': 0.0}
            })
            if res.status_code == 200:
                ans = res.json().get('response', '').strip().upper()
                return 'NO' in ans
    except Exception:
        pass
    return False


def check_guardrails(text, conversation_id=None, *, active_draft=False):
    """Evalúa el texto antes de pasar al LLM o a herramientas.
    Retorna un diccionario indicando si fue bloqueado, tipo, regla, respuesta fija y latencia ms.
    """
    tick = time.perf_counter()
    norm = normalize_text(text)

    # 0. Detección de borrador activo o acción pendiente en la conversación si no fue explícito
    if not active_draft and conversation_id:
        try:
            from .db import demo_db, agent_db
            with agent_db() as aconn:
                act = aconn.execute("SELECT 1 FROM pending_actions WHERE conversation_id = %s AND status = 'pending'", (str(conversation_id),)).fetchone()
                if act:
                    active_draft = True
            if not active_draft:
                with demo_db() as dconn:
                    drow = dconn.execute("SELECT estado FROM tienda.borradores_pedido WHERE conversation_id = %s", (str(conversation_id),)).fetchone()
                    if drow and drow['estado'] == 'en_progreso':
                        active_draft = True
        except Exception:
            pass

    # 1. Comprobar manipulación (máxima prioridad)
    regla_manip = check_manipulacion(norm, allow_order_contact=active_draft and is_order_contact_input(text))
    if regla_manip:
        elapsed = max(1, round((time.perf_counter() - tick) * 1000))
        return {
            'blocked': True,
            'tipo': 'manipulacion',
            'regla': regla_manip,
            'motivo': f'Intento de manipulación detectado ({regla_manip})',
            'respuesta': ('No hay datos verificados que respalden eso; no puedo inventar ventas, productos ni clientes. Puedo consultar los datos reales de la tienda.'
                          if regla_manip == 'inventar_datos' else RESPUESTA_MANIPULACION),
            'ms': elapsed,
        }

    # 2. Comprobar fuera de dominio rápido
    regla_fdd = check_fuera_de_dominio_rapido(norm)
    if regla_fdd:
        elapsed = max(1, round((time.perf_counter() - tick) * 1000))
        return {
            'blocked': True,
            'tipo': 'fuera_de_dominio',
            'regla': regla_fdd,
            'motivo': f'Consulta fuera de dominio ({regla_fdd})',
            'respuesta': RESPUESTA_FUERA_DE_DOMINIO,
            'ms': elapsed,
        }

    # 3. Si no hay términos de negocio claros, clasificar con LLM rápido
    has_business = any(term in norm for term in TERMINOS_NEGOCIO)
    if not has_business:
        words = set(re.findall(r'\b[a-z0-9_]+\b', norm))
        if any(w in words for w in ('te', 'tes', 'miel', 'coco', 'chia', 'mani', 'orden', 'flete', 'pago')):
            has_business = True
    # Excluir saludos o frases muy cortas comunes de saludo
    is_greeting = any(norm.strip().startswith(g) for g in ('hola', 'buenos dias', 'buenas tardes', 'buenas', 'que tal', 'como estas'))
    if not active_draft and not has_business and not is_greeting and len(norm.split()) > 2:
        is_out = classify_fuera_de_dominio_llm(text)
        if is_out:
            elapsed = max(1, round((time.perf_counter() - tick) * 1000))
            return {
                'blocked': True,
                'tipo': 'fuera_de_dominio',
                'regla': 'clasificacion_llm',
                'motivo': 'Consulta clasificada fuera de dominio por LLM',
                'respuesta': RESPUESTA_FUERA_DE_DOMINIO,
                'ms': elapsed,
            }

    elapsed = max(1, round((time.perf_counter() - tick) * 1000))
    return {
        'blocked': False,
        'ms': elapsed,
    }
