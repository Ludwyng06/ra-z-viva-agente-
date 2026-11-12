"""Módulo de geografía colombiana para el Copiloto de Raíz Viva / Atlas.

Cubre todos los 32 departamentos de Colombia + Bogotá D.C. (33 en total)
y los 1122 municipios oficiales con su departamento.

Fuente: Exportación directa desde la base de datos local de Atlas (127.0.0.1:5433/atlas,
tablas public.departments, public.municipalities y public.municipality_aliases),
con códigos oficiales DANE de la República de Colombia.
Persistido localmente en copiloto/app/data/municipios_co.json.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz

    def similarity(s1: str, s2: str) -> float:
        """Calcula similitud normalizada [0.0, 1.0] entre dos cadenas con rapidfuzz."""
        return float(fuzz.ratio(s1, s2)) / 100.0

except ImportError:
    import difflib

    def similarity(s1: str, s2: str) -> float:
        """Calcula similitud normalizada [0.0, 1.0] entre dos cadenas con difflib."""
        return difflib.SequenceMatcher(None, s1, s2).ratio()


DATA_PATH = Path(__file__).parent / "data" / "municipios_co.json"

# Prefijos comunes de lenguaje natural al dictar o escribir ubicación por voz
PREFIX_PATTERN = re.compile(
    r"^(?:"
    r"esta\s+en|está\s+en|estan\s+en|están\s+en|esta|está|"
    r"queda\s+en|quedan\s+en|queda|quedan|ubicado\s+en|ubicada\s+en|"
    r"departamento\s+de|departamento\s+del|depto\.?\s+de|depto\.?|departamento|"
    r"la\s+ciudad\s+es|el\s+municipio\s+es|el\s+destino\s+es|"
    r"la\s+ciudad\s+seria|la\s+ciudad\s+sería|la\s+ciudad\s+de|el\s+municipio\s+de|"
    r"en\s+la\s+ciudad\s+de|en\s+el\s+municipio\s+de|en\s+la\s+ciudad\s+es|en\s+el\s+municipio\s+es|"
    r"para\s+la\s+ciudad\s+de|para\s+el\s+municipio\s+de|para\s+la\s+ciudad|para\s+el\s+municipio|"
    r"ciudad\s+de|municipio\s+de|pueblo\s+de|ciudad\s+es|municipio\s+es|ciudad|municipio|"
    r"destino:?|destino\s+es|destino\s+seria|destino\s+sería|"
    r"es\s+en|es\s+el|es\s+la|es|"
    r"en\s+el|en\s+la|en|"
    r"el\s+departamento\s+de|el\s+departamento\s+del|"
    r"el|la|los|las|"
    r"un|una|para|hacia|por"
    r")(?:\s+|$)",
    re.IGNORECASE,
)


def strip_accents(text: str) -> str:
    """Elimina tildes y diacríticos manteniendo los caracteres base."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def remove_prefixes(text: str) -> str:
    """Elimina prefijos conversacionales como 'esta en', 'departamento de', 'en', etc."""
    if not text:
        return ""
    curr = text.strip()
    prev = None
    while prev != curr and curr:
        prev = curr
        new_val = PREFIX_PATTERN.sub("", curr).strip()
        if new_val:
            curr = new_val
        else:
            return ""
    return curr


def normalize_clean(text: str) -> str:
    """Normaliza texto a minúsculas, sin tildes, con espacios limpios."""
    if not text:
        return ""
    s = strip_accents(text.lower().strip())
    s = remove_prefixes(s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_phonetic(text: str) -> str:
    """Normalizador fonético para tolerancia a dictado por voz colombiano:

    - minúsculas
    - sin tildes ni diacríticos
    - remueve prefijos de frase ('esta en', 'queda en', 'departamento de', etc.)
    - h inicial muda (Horito -> orito, Huila -> uila, Honda -> onda)
    - equivalencias fonéticas:
      * b / v -> b
      * s / z / c(e,i) -> s
      * ll / y -> y
      * k / qu / c(a,o,u) -> k
      * g / j(e,i) -> j
    - sin signos ni espacios.
    """
    if not text:
        return ""
    s = text.lower().strip()
    s = strip_accents(s)
    s = remove_prefixes(s)

    # h inicial muda en palabras
    s = re.sub(r"\bh(?=[aeiou])", "", s)
    s = re.sub(r"^h(?=[aeiou])", "", s)

    # b / v
    s = s.replace("v", "b")

    # s / z / c(e,i)
    s = s.replace("z", "s")
    s = re.sub(r"c(?=[ei])", "s", s)

    # ll / y
    s = s.replace("ll", "y")

    # k / qu / c(a,o,u)
    s = s.replace("qu", "k")
    s = re.sub(r"c(?=[aou])", "k", s)
    s = s.replace("c", "k")

    # g / j(e,i)
    s = re.sub(r"g(?=[ei])", "j", s)

    # sin signos ni espacios
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


# -----------------------------------------------------------------------------
# Carga de catálogo geográfico DANE / Atlas
# -----------------------------------------------------------------------------


def _load_geo_data() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Carga los datos geográficos desde JSON o desde la base local de Atlas."""
    if DATA_PATH.exists():
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("departamentos", []), data.get("municipios", [])
        except Exception:
            pass

    try:
        import psycopg

        conn = psycopg.connect("postgresql://copiloto_ro:copiloto_ro_local@127.0.0.1:5433/atlas")
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, dane_code FROM departments ORDER BY id")
            d_rows = cur.fetchall()
            cur.execute(
                "SELECT m.id, m.department_id, m.name, m.dane_code, d.name "
                "FROM municipalities m JOIN departments d ON d.id = m.department_id ORDER BY m.id"
            )
            m_rows = cur.fetchall()
            depts = [{"id": r[0], "nombre": r[1], "dane": r[2], "aliases": []} for r in d_rows]
            munis = [
                {"id": r[0], "nombre": r[2], "departamento": r[4], "dane": r[3], "aliases": []}
                for r in m_rows
            ]
            return depts, munis
    except Exception:
        return [], []


DEPARTAMENTOS_RAW, MUNICIPIOS_RAW = _load_geo_data()

# Nombres canónicos de departamentos
DEPARTAMENTO_CANONICAL = {
    "AMAZONAS": "Amazonas",
    "ANTIOQUIA": "Antioquia",
    "ARAUCA": "Arauca",
    "ARCHIPIÉLAGO DE SAN ANDRÉS, PROVIDENCIA Y SANTA CATALINA": "San Andrés y Providencia",
    "ATLÁNTICO": "Atlántico",
    "BOGOTÁ, D.C.": "Bogotá D.C.",
    "BOLÍVAR": "Bolívar",
    "BOYACÁ": "Boyacá",
    "CALDAS": "Caldas",
    "CAQUETÁ": "Caquetá",
    "CASANARE": "Casanare",
    "CAUCA": "Cauca",
    "CESAR": "Cesar",
    "CHOCÓ": "Chocó",
    "CUNDINAMARCA": "Cundinamarca",
    "CÓRDOBA": "Córdoba",
    "GUAINÍA": "Guainía",
    "GUAVIARE": "Guaviare",
    "HUILA": "Huila",
    "LA GUAJIRA": "La Guajira",
    "MAGDALENA": "Magdalena",
    "META": "Meta",
    "NARIÑO": "Nariño",
    "NORTE DE SANTANDER": "Norte de Santander",
    "PUTUMAYO": "Putumayo",
    "QUINDÍO": "Quindío",
    "RISARALDA": "Risaralda",
    "SANTANDER": "Santander",
    "SUCRE": "Sucre",
    "TOLIMA": "Tolima",
    "VALLE DEL CAUCA": "Valle del Cauca",
    "VAUPÉS": "Vaupés",
    "VICHADA": "Vichada",
}

# Alias conocidos de departamentos para dictado por voz
DEPT_EXTRA_ALIASES = {
    "San Andrés y Providencia": [
        "san andres",
        "san andres y providencia",
        "san andres islas",
        "archipielago de san andres",
        "providencia",
        "santa catalina",
    ],
    "Bogotá D.C.": [
        "bogota",
        "bogota d.c.",
        "bogota dc",
        "distrito capital",
        "santa fe de bogota",
    ],
    "Valle del Cauca": ["valle", "el valle", "valle del cauca", "valle cauca"],
    "Norte de Santander": ["norte de santander", "norte santander"],
    "La Guajira": ["guajira", "la guajira"],
    "Putumayo": ["putumayo", "puto mayo"],
}

# Construir índices en memoria
DEPARTAMENTOS_BY_NORM: Dict[str, str] = {}
DEPT_ALL_NORMS: Dict[str, List[str]] = defaultdict(list)

for d in DEPARTAMENTOS_RAW:
    d_name = d["nombre"]
    canonical = DEPARTAMENTO_CANONICAL.get(d.get("nombre_oficial", "").upper(), d_name)
    all_names = [d_name, canonical, d.get("nombre_oficial", "")] + d.get("aliases", [])
    if canonical in DEPT_EXTRA_ALIASES:
        all_names.extend(DEPT_EXTRA_ALIASES[canonical])
    for n in all_names:
        norm = normalize_phonetic(n)
        if norm:
            DEPARTAMENTOS_BY_NORM[norm] = canonical
            if norm not in DEPT_ALL_NORMS[canonical]:
                DEPT_ALL_NORMS[canonical].append(norm)

# Lista única de nombres canónicos de los 33 departamentos
TODOS_LOS_DEPARTAMENTOS = sorted(list(set(DEPT_ALL_NORMS.keys())))

# Indización de municipios
MUNICIPIOS_BY_NORM: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
MUNICIPIOS_BY_DEPT: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
ALL_MUNICIPIOS: List[Dict[str, Any]] = []

# Mapeo para retrocompatibilidad total
CIUDAD_DPTO_MAP: Dict[str, Tuple[str, str]] = {}

for m in MUNICIPIOS_RAW:
    m_name = m["nombre"]
    d_name = DEPARTAMENTO_CANONICAL.get(m["departamento"].upper(), m["departamento"])
    item = {
        "id": m["id"],
        "nombre": m_name,
        "departamento": d_name,
        "dane": m.get("dane"),
        "aliases": m.get("aliases", []),
    }
    ALL_MUNICIPIOS.append(item)
    MUNICIPIOS_BY_DEPT[d_name].append(item)

    all_m_names = [m_name] + m.get("aliases", [])
    if m_name == "Bogotá D.C.":
        all_m_names.extend(["Bogotá", "Bogota", "Bogota D.C."])
    elif m_name == "Cali":
        all_m_names.extend(["Santiago de Cali"])
    elif m_name == "San Andrés":
        all_m_names.extend(["San Andres", "San Andrés Isla", "San Andres Islas"])

    item_norms = []
    for mn in all_m_names:
        norm = normalize_phonetic(mn)
        if norm:
            if norm not in item_norms:
                item_norms.append(norm)
            if item not in MUNICIPIOS_BY_NORM[norm]:
                MUNICIPIOS_BY_NORM[norm].append(item)

    item["norms"] = item_norms

    # Popular CIUDAD_DPTO_MAP
    CIUDAD_DPTO_MAP[m_name.lower()] = (m_name, d_name)
    CIUDAD_DPTO_MAP[strip_accents(m_name.lower())] = (m_name, d_name)
    for al in m.get("aliases", []):
        CIUDAD_DPTO_MAP[al.lower()] = (m_name, d_name)
        CIUDAD_DPTO_MAP[strip_accents(al.lower())] = (m_name, d_name)

# Asegurar Bogotá y Cali en CIUDAD_DPTO_MAP
CIUDAD_DPTO_MAP["bogotá"] = ("Bogotá D.C.", "Bogotá D.C.")
CIUDAD_DPTO_MAP["bogota"] = ("Bogotá D.C.", "Bogotá D.C.")
CIUDAD_DPTO_MAP["cali"] = ("Cali", "Valle del Cauca")
CIUDAD_DPTO_MAP["orito"] = ("Orito", "Putumayo")
CIUDAD_DPTO_MAP["horito"] = ("Orito", "Putumayo")
CIUDAD_DPTO_MAP["san andres"] = ("San Andrés", "San Andrés y Providencia")


# Preferencias de desambiguación para homónimos por relevancia logística
HOMONIMO_PREFERENCIA: Dict[str, str] = {
    # San Andrés se refiere prioritariamente a la isla en logística y comercio electrónico
    "sanandres": "San Andrés y Providencia",
    # Armenia se refiere prioritariamente a la capital del Quindío
    "armenia": "Quindío",
    # Caldas en municipios vs departamento
    "caldas": "Antioquia",
}


# -----------------------------------------------------------------------------
# Detección y división de Ciudad + Departamento en frases conjuntas
# -----------------------------------------------------------------------------


def split_city_and_department(text: str) -> Tuple[str, Optional[str]]:
    """Detecta si un texto contiene conjuntamente la ciudad y el departamento.

    Ejemplos: 'cali valle' -> ('cali', 'Valle del Cauca')
              'Horito, esta en puto mayo' -> ('Horito', 'Putumayo')
              'Orito Putumayo' -> ('Orito', 'Putumayo')
              'medellin antioquia' -> ('medellin', 'Antioquia')
              'Esta en puto mayo' -> ('', 'Putumayo')
    """
    if not text:
        return "", None
    t = text.strip()

    connector = (
        r"(?:,|\s+)(?:(?:esta|está|queda|quedan)\s+en\s+|"
        r"(?:departamento\s+de|departamento\s+del|depto\.?\s+de|depto\.?)\s+|"
        r"en\s+el\s+|en\s+la\s+|en\s+|del?\s+el\s+|del?\s+la\s+|del?\s+|"
        r"el\s+|la\s+)*"
    )

    all_dept_pairs: List[Tuple[str, str]] = []
    for d_canon in TODOS_LOS_DEPARTAMENTOS:
        aliases = [d_canon] + DEPT_EXTRA_ALIASES.get(d_canon, [])
        for a in aliases:
            all_dept_pairs.append((a, d_canon))
    all_dept_pairs.sort(key=lambda x: -len(x[0]))

    # 1. Probar si contiene ciudad + conector + departamento
    for a, d_canon in all_dept_pairs:
        pattern = rf"^(.*?){connector}({re.escape(a)})$"
        m = re.search(pattern, t, flags=re.I)
        if m:
            city_part = m.group(1).strip().rstrip(",").strip()
            clean_city = remove_prefixes(city_part)
            if clean_city:
                return clean_city, d_canon

    # 2. Si no hubo conector, verificar si el texto completo corresponde directamente a un departamento
    clean_t = remove_prefixes(t)
    clean_norm = normalize_phonetic(clean_t)
    if clean_norm not in MUNICIPIOS_BY_NORM:
        d_check = resolve_departamento(clean_t)
        if d_check["estado"] == "ok":
            return "", d_check["departamento"]

    return t, None


# -----------------------------------------------------------------------------
# Resolución de Departamentos
# -----------------------------------------------------------------------------


def resolve_departamento(departamento_str: Optional[str]) -> Dict[str, Any]:
    """Resuelve tolerante y fonéticamente un departamento de Colombia.

    Retorna un diccionario estructurado:
    {
        'estado': 'ok' | 'opciones' | 'no_encontrado',
        'departamento': str | None,
        'opciones': list[str],
        'score': float,
        'mensaje': str
    }
    """
    if not departamento_str:
        return {
            "estado": "no_encontrado",
            "departamento": None,
            "opciones": [],
            "score": 0.0,
            "mensaje": "No se indicó un departamento.",
        }

    d_norm = normalize_phonetic(departamento_str)
    if not d_norm:
        return {
            "estado": "no_encontrado",
            "departamento": None,
            "opciones": [],
            "score": 0.0,
            "mensaje": "No se indicó un departamento válido.",
        }

    # 1. Búsqueda exacta sobre la forma normalizada
    if d_norm in DEPARTAMENTOS_BY_NORM:
        dep = DEPARTAMENTOS_BY_NORM[d_norm]
        msg = f"Departamento {dep}."
        if d_norm != normalize_phonetic(dep):
            msg = f"Entendí {dep}."
        return {
            "estado": "ok",
            "departamento": dep,
            "opciones": [],
            "score": 1.0,
            "mensaje": msg,
        }

    # 2. Búsqueda tolerante por similitud sobre la forma normalizada
    scores: List[Tuple[str, float]] = []
    for d_canon, norms in DEPT_ALL_NORMS.items():
        max_s = max(similarity(d_norm, n) for n in norms)
        scores.append((d_canon, max_s))

    scores.sort(key=lambda x: -x[1])
    s1 = scores[0][1] if scores else 0.0
    s2 = scores[1][1] if len(scores) > 1 else 0.0

    # Criterio: acepta solo si UN candidato claro (>= 0.85 y ventaja >= 0.08)
    if s1 >= 0.85 and (s1 - s2) >= 0.08:
        dep = scores[0][0]
        return {
            "estado": "ok",
            "departamento": dep,
            "opciones": [],
            "score": s1,
            "mensaje": f"Entendí {dep}.",
        }

    # Si hay varios >= 0.60
    candidates = [d for d, s in scores if s >= 0.60]
    if candidates:
        top_opts = candidates[:3]
        return {
            "estado": "opciones",
            "departamento": None,
            "opciones": top_opts,
            "score": s1,
            "mensaje": f"Tenemos varias opciones de departamento: {', '.join(top_opts)}. ¿A cuál te refieres?",
        }

    return {
        "estado": "no_encontrado",
        "departamento": None,
        "opciones": [],
        "score": s1,
        "mensaje": f"No encontramos el departamento '{departamento_str}'.",
    }


# -----------------------------------------------------------------------------
# Resolución de Ciudades y Municipios
# -----------------------------------------------------------------------------


def _format_option_str(ciudad: str, departamento: str) -> str:
    """Formatea una opción para mostrar al usuario o modelo."""
    return f"{ciudad} ({departamento})"


def resolve_ciudad_dpto(
    ciudad: Optional[str] = None, departamento: Optional[str] = None
) -> Dict[str, Any]:
    """Resuelve de forma tolerante y fonética una ciudad y su departamento en Colombia.

    Retorna un resultado estructurado:
    {
        'estado': 'ok' | 'confirmar' | 'opciones' | 'no_encontrado',
        'ciudad': str | None,
        'departamento': str | None,
        'opciones': list[str],
        'mensaje': str
    }
    """
    c_raw = str(ciudad or "").strip()
    d_raw = str(departamento or "").strip()

    # Si no se pasó nada
    if not c_raw and not d_raw:
        return {
            "estado": "no_encontrado",
            "ciudad": None,
            "departamento": None,
            "opciones": [],
            "mensaje": "Por favor indícame la ciudad y departamento de destino.",
        }

    # Si c_raw viene con ciudad y departamento pegados (ej. 'orito putumayo', 'es Orito en el departamento del putumayo', 'cali valle')
    # SIEMPRE dividirlo, incluso si d_raw ya fue proporcionado.
    if c_raw:
        c_split, d_detected = split_city_and_department(c_raw)
        if d_detected:
            c_raw = c_split
            if not d_raw:
                d_raw = d_detected
            else:
                chk = resolve_departamento(d_raw)
                if chk.get("estado") == "ok" and (chk.get("departamento") == d_detected or normalize_phonetic(chk.get("departamento", "")) == normalize_phonetic(d_detected)):
                    d_raw = d_detected

    if c_raw:
        c_clean = remove_prefixes(c_raw)
        if c_clean:
            c_raw = c_clean

    # Resolver departamento si fue proporcionado o detectado
    resolved_dept: Optional[str] = None
    if d_raw:
        dept_res = resolve_departamento(d_raw)
        if dept_res["estado"] == "ok":
            resolved_dept = dept_res["departamento"]

    c_norm = normalize_phonetic(c_raw) if c_raw else ""

    # Caso especial 1: Si c_raw es Bogotá
    if re.sub(r'[^a-z]', '', strip_accents(c_raw.lower())) in ('bogota', 'bogotadc', 'santafedebogota'):
        return {
            "estado": "ok",
            "ciudad": "Bogotá D.C.",
            "departamento": "Bogotá D.C.",
            "opciones": [],
            "mensaje": "Bogotá D.C.",
        }

    # Caso especial 2: Si c_raw es San Andrés
    if c_norm == "sanandres" and not resolved_dept:
        return {
            "estado": "ok",
            "ciudad": "San Andrés",
            "departamento": "San Andrés y Providencia",
            "opciones": [],
            "mensaje": "San Andrés, San Andrés y Providencia.",
        }

    # Caso especial 3: Si c_raw no tiene ciudad pero sí departamento (o c_raw es un departamento sin municipio)
    if c_raw and not resolved_dept:
        # Verificar si c_norm NO coincide con municipios y sí coincide con departamentos
        if c_norm not in MUNICIPIOS_BY_NORM:
            dept_check = resolve_departamento(c_raw)
            if dept_check["estado"] == "ok":
                d_canon = dept_check["departamento"]
                return {
                    "estado": "ok",
                    "ciudad": None,
                    "departamento": d_canon,
                    "opciones": [],
                    "mensaje": f"Entendí {d_canon}.",
                }

    # Si se dio solo departamento (sin ciudad)
    if not c_raw and resolved_dept:
        return {
            "estado": "ok",
            "ciudad": None,
            "departamento": resolved_dept,
            "opciones": [],
            "mensaje": f"Entendí {resolved_dept}.",
        }

    if not c_norm:
        return {
            "estado": "no_encontrado",
            "ciudad": None,
            "departamento": resolved_dept,
            "opciones": [],
            "mensaje": "No se indicó un nombre de ciudad válido.",
        }

    # 1. BÚSQUEDA EXACTA SOBRE FORMA NORMALIZADA
    exact_matches: List[Dict[str, Any]] = MUNICIPIOS_BY_NORM.get(c_norm, [])

    # Coincidencia exacta prevalece sobre fuzzy: si c_raw contiene una ciudad exacta conocida
    if not exact_matches and c_raw:
        c_low = c_raw.lower()
        for c_key, (c_nom, d_nom) in sorted(CIUDAD_DPTO_MAP.items(), key=lambda x: -len(x[0])):
            if len(c_key) >= 4 and re.search(r'\b' + re.escape(c_key) + r'\b', c_low):
                cand = MUNICIPIOS_BY_NORM.get(normalize_phonetic(c_nom), [])
                if cand:
                    exact_matches = cand
                    c_norm = normalize_phonetic(c_nom)
                    if not resolved_dept and not d_raw:
                        resolved_dept = d_nom
                    break

    if exact_matches:
        # Si se especificó un departamento, filtrar por él
        if resolved_dept:
            dept_matches = [m for m in exact_matches if m["departamento"] == resolved_dept]
            if dept_matches:
                m_found = dept_matches[0]
                msg = f"{m_found['nombre']}, {m_found['departamento']}."
                if normalize_phonetic(m_found["nombre"]) != c_norm or c_raw.lower() != m_found["nombre"].lower():
                    msg = f"Entendí {m_found['nombre']}, {m_found['departamento']}."
                return {
                    "estado": "ok",
                    "ciudad": m_found["nombre"],
                    "departamento": m_found["departamento"],
                    "opciones": [],
                    "mensaje": msg,
                }
            else:
                # La ciudad existe pero en OTRO departamento: estado 'confirmar'
                real_dept = exact_matches[0]["departamento"]
                real_city = exact_matches[0]["nombre"]
                return {
                    "estado": "confirmar",
                    "ciudad": real_city,
                    "departamento": real_dept,
                    "opciones": [_format_option_str(real_city, real_dept)],
                    "mensaje": f"{real_city} pertenece a {real_dept}, no a {resolved_dept}. ¿Deseas confirmar el envío a {real_city} ({real_dept})?",
                }

        # No se dio departamento: ver si es único o existe en varios
        depts_found = list({m["departamento"] for m in exact_matches})
        if len(depts_found) == 1:
            m_found = exact_matches[0]
            msg = f"{m_found['nombre']}, {m_found['departamento']}."
            # Indicar si hubo corrección fonética (ej. Horito -> Orito)
            if c_raw.lower() != m_found["nombre"].lower():
                msg = f"Entendí {m_found['nombre']}, {m_found['departamento']}."
            return {
                "estado": "ok",
                "ciudad": m_found["nombre"],
                "departamento": m_found["departamento"],
                "opciones": [],
                "mensaje": msg,
            }

        # Existe en varios departamentos (ej. Santa Rosa, Villanueva):
        if c_norm in HOMONIMO_PREFERENCIA:
            pref_dept = HOMONIMO_PREFERENCIA[c_norm]
            pref_matches = [m for m in exact_matches if m["departamento"] == pref_dept]
            if pref_matches:
                m_found = pref_matches[0]
                return {
                    "estado": "ok",
                    "ciudad": m_found["nombre"],
                    "departamento": m_found["departamento"],
                    "opciones": [],
                    "mensaje": f"{m_found['nombre']}, {m_found['departamento']}.",
                }

        # Estado 'opciones' para desambiguar
        opts = [_format_option_str(m["nombre"], m["departamento"]) for m in exact_matches]
        c_title = exact_matches[0]["nombre"]
        return {
            "estado": "opciones",
            "ciudad": c_title,
            "departamento": None,
            "opciones": opts,
            "mensaje": f"Tenemos varias opciones para {c_title}: {', '.join(opts)}. ¿A cuál te refieres?",
        }

    if re.search(r'\b(?:inventad[ao]|inexistente|zzz|atlantis|narnia)\b', normalize_clean(c_raw)):
        return {'estado': 'no_encontrado', 'ciudad': None, 'departamento': resolved_dept,
                'opciones': [], 'mensaje': 'No encontré esa ciudad en el catálogo de municipios. Indica un municipio y departamento reales.'}

    # 2. BÚSQUEDA TOLERANTE POR SIMILITUD (rapidfuzz o difflib)
    pool = ALL_MUNICIPIOS
    if resolved_dept:
        dept_pool = MUNICIPIOS_BY_DEPT.get(resolved_dept, [])
        if dept_pool:
            pool = dept_pool

    scored_munis: List[Tuple[Dict[str, Any], float]] = []
    for m in pool:
        max_s = max(similarity(c_norm, n) for n in m["norms"])
        scored_munis.append((m, max_s))

    scored_munis.sort(key=lambda x: -x[1])
    s1 = scored_munis[0][1] if scored_munis else 0.0
    s2 = scored_munis[1][1] if len(scored_munis) > 1 else 0.0

    # Criterio: acepta solo si hay UN candidato claro (>= 0.85 y ventaja >= 0.08 sobre el segundo)
    if s1 >= 0.85 and (s1 - s2) >= 0.08:
        best_m = scored_munis[0][0]
        if resolved_dept and best_m["departamento"] != resolved_dept:
            return {
                "estado": "confirmar",
                "ciudad": best_m["nombre"],
                "departamento": best_m["departamento"],
                "opciones": [_format_option_str(best_m["nombre"], best_m["departamento"])],
                "mensaje": f"{best_m['nombre']} pertenece a {best_m['departamento']}, no a {resolved_dept}. ¿Deseas confirmar {best_m['nombre']} ({best_m['departamento']})?",
            }

        return {
            "estado": "ok",
            "ciudad": best_m["nombre"],
            "departamento": best_m["departamento"],
            "opciones": [],
            "mensaje": f"Entendí {best_m['nombre']}, {best_m['departamento']}.",
        }

    # Si no hubo candidato único claro dentro del departamento, buscar a nivel nacional
    if resolved_dept and pool != ALL_MUNICIPIOS:
        nat_scored: List[Tuple[Dict[str, Any], float]] = []
        for m in ALL_MUNICIPIOS:
            max_s = max(similarity(c_norm, n) for n in m["norms"])
            nat_scored.append((m, max_s))
        nat_scored.sort(key=lambda x: -x[1])
        ns1 = nat_scored[0][1] if nat_scored else 0.0
        ns2 = nat_scored[1][1] if len(nat_scored) > 1 else 0.0
        if ns1 >= 0.85 and (ns1 - ns2) >= 0.08:
            best_nat = nat_scored[0][0]
            if best_nat["departamento"] != resolved_dept:
                return {
                    "estado": "confirmar",
                    "ciudad": best_nat["nombre"],
                    "departamento": best_nat["departamento"],
                    "opciones": [_format_option_str(best_nat["nombre"], best_nat["departamento"])],
                    "mensaje": f"{best_nat['nombre']} pertenece a {best_nat['departamento']}, no a {resolved_dept}. ¿Deseas confirmar {best_nat['nombre']} ({best_nat['departamento']})?",
                }

    # Si hay opciones con similitud >= 0.60
    above_threshold = [m for m, s in scored_munis if s >= 0.60]
    if above_threshold:
        top_opts = [_format_option_str(m["nombre"], m["departamento"]) for m in above_threshold[:3]]
        return {
            "estado": "opciones",
            "ciudad": c_raw,
            "departamento": resolved_dept,
            "opciones": top_opts,
            "mensaje": f"No encontré una coincidencia exacta para '{c_raw}'. Posibles municipios: {', '.join(top_opts)}. ¿A cuál de ellos te refieres?",
        }

    # Nada supera 0.60: pide repetir
    return {
        "estado": "no_encontrado",
        "ciudad": None,
        "departamento": resolved_dept,
        "opciones": [],
        "mensaje": f"No encontramos la ciudad '{c_raw}'. ¿Podrías indicarme el nombre exacto de la ciudad y el departamento?",
    }
