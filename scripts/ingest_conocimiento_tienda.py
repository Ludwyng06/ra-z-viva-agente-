"""Genera fichas desde la tienda demo e ingiere Markdown en pgvector.

Idempotente sin migraciones: compara contenidos ordenados de chunks, y omite
documentos intactos. Cada documento se reemplaza atómicamente después de obtener
sus embeddings; un fallo de Ollama conserva la versión previamente publicada.
Ejecutar desde copiloto con PYTHONUTF8=1. No reinicia ningún servicio.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import COPILOT_MODE, DEMO_DSN, AGENT_DSN, EMBED_MODEL
from app.ollama import embed, vector_literal
from ingest import split_chunks


def money(value):
    return f'{int(value):,}'.replace(',', '.') + ' COP'


def require_local_demo():
    if COPILOT_MODE != 'demo':
        raise ValueError('Se requiere COPILOT_MODE=demo.')
    for dsn in (DEMO_DSN, AGENT_DSN):
        if conninfo_to_dict(dsn).get('host', '') not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Solo se permite PostgreSQL local; no se imprimen credenciales.')


# No se atribuyen ingredientes, origen, dosis o indicaciones terapéuticas a
# partir del nombre comercial. Las etiquetas del fabricante no están en SQL.
FAQ = {
    'RV-ACO-04': ('Cuidado personal; consultar la etiqueta para el uso externo admitido. '
                  'No recomendar consumo oral a partir del nombre aceite de coco.',
                  'Conservar cerrado y protegido de calor y luz; seguir la etiqueta.'),
    'RV-ALA-05': ('Aceite esencial para el uso que indique su etiqueta; no ingerir ni recomendar '
                  'diluciones, aplicación cutánea o uso en menores sin ficha del fabricante.',
                  'Mantener el frasco cerrado, lejos de luz, calor y alcance de niños.'),
    'RV-ARO-13': ('Producto de cuidado personal. Verificar etiqueta antes de sugerir aplicación '
                  'o dilución; el nombre no confirma que sea un extracto apto para ingerir.',
                  'Conservar cerrado y protegido de luz y calor según el envase.'),
    'RV-CHI-14': ('Alimento para preparaciones conforme a su etiqueta; no inventar porciones '
                  'ni prometer saciedad, reducción de peso o tratamiento.',
                  'Mantener el empaque bien cerrado, seco y protegido de humedad.'),
    'RV-COL-06': ('Suplemento en polvo; la dosis y preparación dependen de la etiqueta, '
                  'no están registradas en el demo. No prometer regeneración de articulaciones.',
                  'Conservar seco y cerrado; utilizar utensilios limpios y secos.'),
    'RV-CUR-07': ('Alimento en polvo para preparaciones según etiqueta; no indicar dosis medicinal '
                  'ni sugerir que reemplaza tratamientos.',
                  'Cerrar el empaque y protegerlo de luz, humedad y fuentes de calor.'),
    'RV-GRA-11': ('Alimento para consumo según etiqueta. El nombre informa frutos secos; '
                  'no afirmar sin azúcar, sin gluten ni una fórmula específica no registrada.',
                  'Conservar bien cerrada y seca, respetando vencimiento y etiqueta.'),
    'RV-JAB-08': ('Producto de limpieza para uso externo según etiqueta; no ingerir. '
                  'No atribuir cualidades hipoalergénicas ni ausencia de sulfatos sin ficha.',
                  'Dejar secar en una jabonera con drenaje y seguir indicaciones del fabricante.'),
    'RV-MAN-12': ('Alimento a base de maní según su nombre comercial; verificar ingredientes '
                  'y advertencias del envase antes de consumir. No asumir ausencia de azúcar o aditivos.',
                  'Cerrar después de usar; refrigeración y condiciones tras abrir según etiqueta.'),
    'RV-MIE-03': ('Alimento; seguir etiqueta. No ofrecer a menores de doce meses. '
                  'La cristalización por sí sola no certifica autenticidad ni pureza.',
                  'Conservar bien cerrada y protegida de contaminación y calor excesivo; seguir etiqueta.'),
    'RV-MOR-09': ('Suplemento en cápsulas; no inventar dosis ni declarar seguridad individual. '
                  'No afirmar composición de la cápsula ni beneficios médicos sin ficha.',
                  'Conservar el envase cerrado, seco y fuera del alcance de niños.'),
    'RV-TEM-02': ('Infusión; preparar conforme a la etiqueta. No prometer que cura insomnio, '
                  'ansiedad o trastornos digestivos.',
                  'Mantener las bolsitas secas, cerradas y protegidas de olores y humedad.'),
    'RV-TEV-01': ('Infusión; respetar las instrucciones del envase. No prometer adelgazamiento '
                  'ni tratamientos; composición y contenido de cafeína no están en SQL.',
                  'Conservar las bolsitas en su empaque cerrado, lejos de humedad y olores.'),
    'RV-VIN-10': ('Alimento para usos culinarios según etiqueta; no sugerir consumo puro '
                  'ni dosis terapéuticas. No prometer que cura enfermedades o reduce peso.',
                  'Mantener cerrado; seguir instrucciones de conservación tras abrir del fabricante.'),
}


def generate_documents(products, tariffs):
    directory = ROOT / 'knowledge_demo'
    directory.mkdir(parents=True, exist_ok=True)
    lines = ['# Catálogo verificado de Raíz Viva Naturales', '',
             'Datos leídos de tienda.productos al ejecutar la ingesta. Los precios son una referencia '
             'del demo, en COP sin decimales; consultar precio y stock actuales antes de confirmar.', '',
             '| SKU | Producto | Categoría | Precio de referencia |', '|---|---|---|---|']
    for p in products:
        lines.append(f"| {p['sku']} | {p['nombre']} | {p['categoria']} | {money(p['precio_venta'])} |")
        usage, storage = FAQ.get(p['sku'],
                                ('No hay ficha de uso; pedir la etiqueta al equipo humano.',
                                 'Conservar según las instrucciones del fabricante.'))
        text = (f"# FAQ: {p['nombre']} — Raíz Viva Naturales\n\n"
                f"SKU registrado: {p['sku']}. Categoría: {p['categoria']}. "
                f"Precio de referencia leído de tienda.productos: {money(p['precio_venta'])}. "
                'Consultar precio y stock actuales; aparecer en el catálogo no garantiza disponibilidad.\n\n'
                f'## ¿Cómo se usa?\n{usage}\n\n'
                f'## ¿Cómo se conserva?\n{storage}\n\n'
                '## ¿Qué precauciones o contraindicaciones tiene?\n'
                'El demo no contiene una ficha completa de ingredientes, alérgenos ni contraindicaciones. '
                'No usar si la etiqueta contraindica su uso o se conoce alergia a sus componentes. '
                'Consultar etiqueta y profesional de salud en embarazo, lactancia, menores o uso de medicamentos. '
                'Natural no garantiza seguridad; no sustituye un tratamiento ni permite promesas médicas.\n')
        if p['sku'] == 'RV-MIE-03':
            text += ('\nLa precaución sobre miel en menores de doce meses procede de '
                     '[CDC: Foods and Drinks to Avoid or Limit]'
                     '(https://www.cdc.gov/infant-toddler-nutrition/foods-and-drinks/foods-and-drinks-to-avoid-or-limit.html).\n')
        (directory / f"producto_{p['sku']}.md").write_text(text, encoding='utf-8')
    (directory / '09_catalogo_verificado.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    zones = ['# Cotizaciones demo por zona — Raíz Viva Naturales', '',
             'Referencia generada de tienda.tarifas y transportadoras activas. No es una cotización '
             'vinculante ni acredita cobertura física por municipio. Para el precio del pedido, '
             'consultar el cotizador con ciudad, departamento y productos.', '',
             'Los días promedio se almacenan por transportadora; no existen tiempos específicos '
             'por zona o municipio. La misma referencia de días puede aparecer en varias zonas. '
             'No convertir estos promedios en fecha garantizada ni añadir un plazo rural inventado.', '',
             'Cada referencia para un kilogramo suma valor base y valor por kilogramo. '
             'Un peso distinto cambia el flete. El grupo Nacional es una tarifa de respaldo, '
             'no una declaración de cobertura universal.', '']
    zone = None
    for t in tariffs:
        if zone != t['departamento']:
            zone = t['departamento']
            zones.extend([f'## Zona {zone}', ''])
        zones.append(f"Zona {zone}: {t['nombre']}; promedio {t['dias_promedio']} días; "
                     f"base {money(t['valor_base'])}; adicional por kilogramo {money(t['valor_por_kg'])}; "
                     f"referencia para un kilogramo {money(t['valor_base'] + t['valor_por_kg'])}.")
    (directory / '10_cotizaciones_por_zona.md').write_text('\n'.join(zones) + '\n', encoding='utf-8')


def ingest_one(connection, path):
    content = path.read_text(encoding='utf-8-sig')
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_match[1].strip() if title_match else path.stem
    slug, source = f'demo/{path.stem}', f'demo/{path.name}'
    fragments = list(split_chunks(content))
    previous = connection.execute('SELECT id,title,source,url FROM documents WHERE slug=%s', (slug,)).fetchone()
    if previous:
        old_chunks = connection.execute('SELECT content FROM chunks WHERE document_id=%s ORDER BY ord',
                                        (previous['id'],)).fetchall()
        if ([c['content'] for c in old_chunks] == fragments and previous['title'] == title
                and previous['source'] == source and previous['url'] == '/ayuda/' + slug):
            return {'slug': slug, 'status': 'sin cambios', 'chunks': len(fragments)}
    vectors = [vector_literal(embed(fragment)) for fragment in fragments]
    with connection.transaction():
        row = connection.execute('''INSERT INTO documents(source,slug,title,url)
            VALUES (%s,%s,%s,%s) ON CONFLICT(slug) DO UPDATE SET source=excluded.source,
            title=excluded.title,url=excluded.url,updated_at=now() RETURNING id''',
                                 (source, slug, title, '/ayuda/' + slug)).fetchone()
        connection.execute('DELETE FROM chunks WHERE document_id=%s', (row['id'],))
        for ordinal, (fragment, vector) in enumerate(zip(fragments, vectors)):
            connection.execute('INSERT INTO chunks(document_id,ord,content,embedding) '
                               'VALUES (%s,%s,%s,%s::vector)', (row['id'], ordinal, fragment, vector))
    return {'slug': slug, 'status': 'actualizado' if previous else 'creado', 'chunks': len(fragments)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='tmp/ingesta_conocimiento_tienda.json')
    args = parser.parse_args()
    require_local_demo()
    with psycopg.connect(DEMO_DSN, row_factory=dict_row, autocommit=True,
                         options='-c default_transaction_read_only=on', connect_timeout=5) as demo:
        with demo.transaction():
            demo.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            products = demo.execute('SELECT sku,nombre,categoria,precio_venta FROM tienda.productos ORDER BY sku').fetchall()
            tariffs = demo.execute('SELECT tar.departamento, t.nombre, t.dias_promedio, '
                                   'tar.valor_base,tar.valor_por_kg FROM tienda.tarifas tar '
                                   'JOIN tienda.transportadoras t ON t.id=tar.transportadora_id '
                                   'WHERE t.activo ORDER BY tar.departamento,t.nombre').fetchall()
    if not products or not tariffs:
        raise ValueError('Catálogo o tarifas demo vacíos; no se genera conocimiento inventado.')
    generate_documents(products, tariffs)
    report = {'date': datetime.now(timezone.utc).isoformat(), 'model': EMBED_MODEL,
              'products': len(products), 'tariffs': len(tariffs), 'documents': []}
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(AGENT_DSN, row_factory=dict_row, autocommit=True, connect_timeout=5) as connection:
        # Serializa estas ingestas sin crear tablas ni tocar documentos de Atlas.
        connection.execute("SELECT pg_advisory_lock(hashtext('raiz-viva-knowledge-ingestion'))")
        try:
            for document in sorted((ROOT / 'knowledge_demo').glob('*.md')):
                result = ingest_one(connection, document)
                report['documents'].append(result)
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f"{result['slug']}: {result['status']}, {result['chunks']} fragmentos", flush=True)
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext('raiz-viva-knowledge-ingestion'))")
    print(f"Ingesta completada: {len(report['documents'])} documentos; informe {path}")


if __name__ == '__main__':
    main()
