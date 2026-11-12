"""Resolución conservadora de nombres dictados contra el catálogo demo.

No escribe en la base. Las opciones y el producto conservan los diccionarios
originales del catálogo, sin añadir puntuaciones ni cambiar sus campos.
"""
import re
import unicodedata
from difflib import SequenceMatcher

try:
    from rapidfuzz.fuzz import ratio as _ratio
except ImportError:
    _ratio = None

_PREFIXES = set('el la los las un una unos unas uno dos tres cuatro cinco seis siete ocho nueve diez once doce veinte treinta de del'.split())


def _tokens(text):
    text = ''.join(c for c in unicodedata.normalize('NFD', str(text or '').lower())
                   if unicodedata.category(c) != 'Mn')
    text = re.sub(r'\braiz\s*viva(?:\s*naturales)?\b|\bmarca\b', ' ', text)
    words = re.findall(r'[a-z0-9]+', text)
    while words and (words[0] in _PREFIXES or words[0].isdigit()):
        words.pop(0)
    return words


def _phonetic(words):
    result = []
    for word in words:
        if len(word) > 4 and word.endswith(('les', 'nes', 'res')):
            word = word[:-2]
        elif len(word) > 3 and word.endswith('s'):
            word = word[:-1]
        word = word.replace('h', '').replace('v', 'b').replace('ll', 'y')
        word = re.sub(r'c(?=[ei])', 's', word).replace('z', 's')
        word = re.sub(r'g(?=[ei])', 'j', word)
        result.append(word)
    return ''.join(result)


def _similarity(left, right):
    if not left or not right:
        return 0.0
    if _ratio is not None:
        return _ratio(left, right) / 100.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _score(query, product):
    sku = _phonetic(_tokens(product.get('sku', '')))
    name = re.sub(r'\([^)]*\)', ' ', str(product.get('nombre', '')))
    words = _tokens(name)
    candidates = [sku, _phonetic(words), _phonetic(_tokens(product.get('nombre', '')))]
    best = max((_similarity(query, candidate) for candidate in candidates), default=0.0)
    # Ventanas permiten omitir presentación/adjetivos sin favorecer un producto:
    # "aceite" da el mismo puntaje a los tres aceites y pide aclaración.
    # Exigir similitud >= 0.75 en subventanas para evitar falsos positivos con palabras cortas no relacionadas.
    for start in range(len(words)):
        for end in range(start + 1, len(words) + 1):
            sub_cand = _phonetic(words[start:end])
            sim = _similarity(query, sub_cand)
            if sim >= 0.75:
                best = max(best, sim)
    return best


def buscar_producto(texto, catalogo):
    """Devuelve estado, producto (o None), opciones (diccionarios) y mensaje.

    Acepta >= .85 con ventaja >= .08; ofrece hasta tres candidatos >= .6.
    Un candidato único entre .6 y .85 también requiere confirmación.
    """
    query = _phonetic(_tokens(texto))
    ranked = sorted(((_score(query, p), index, p) for index, p in enumerate(catalogo)),
                    key=lambda item: (-item[0], item[1])) if len(query) >= 2 else []
    ranked = [item for item in ranked if item[0] >= .6]
    if not ranked:
        return {'estado': 'no_encontrado', 'producto': None, 'opciones': [],
                'mensaje': 'No encontramos el producto. Indica su nombre o SKU.'}
    best = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] >= .85 and best[0] - second >= .08 - 1e-12:
        return {'estado': 'ok', 'producto': best[2], 'opciones': [],
                'mensaje': f"Encontramos {best[2]['nombre']}."}
    options = [item[2] for item in ranked[:3]]
    return {'estado': 'opciones', 'producto': None, 'opciones': options,
            'mensaje': '¿Cuál producto quieres? ' + '; '.join(p['nombre'] for p in options)}


def leer_catalogo_demo():
    """Lee tienda.productos usando DEMO_DSN (config resuelve a 127.0.0.1:5434).

    Errores de conexión se propagan: no se confunden con catálogo vacío.
    """
    from .db import demo_db

    with demo_db() as connection:
        return connection.execute('SELECT * FROM tienda.productos ORDER BY nombre, sku').fetchall()
