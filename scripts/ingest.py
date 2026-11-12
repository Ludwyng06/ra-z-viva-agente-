"""Ingesta idempotente de artículos TS y decisiones de negocio a pgvector."""
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import ROOT
from app.db import agent_db
from app.ollama import embed, vector_literal

# Un escáner de literales evita cortar cadenas con comillas escapadas o comentarios.
TOKEN = re.compile(r'''//[^\n]*|/\*[\s\S]*?\*/|'(?:\\[\s\S]|[^'\\])*'|"(?:\\[\s\S]|[^"\\])*"|`(?:\\[\s\S]|[^`\\])*`''')
ESCAPE = re.compile(r'\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|([\s\S]))')


def decode_literal(token):
    def replace(match):
        if match[1] or match[2]:
            return chr(int(match[1] or match[2],16))
        return {'n':'\n','r':'\r','t':'\t','\n':''}.get(match[3],match[3])
    return ESCAPE.sub(replace,token[1:-1])


def parse_article(path):
    path = Path(path).resolve()
    text = path.read_text(encoding='utf-8-sig')
    scanned = list(TOKEN.finditer(text))
    tokens = [m for m in scanned if not m[0].startswith('/')]
    # Conservar posiciones, pero impedir que claves dentro de comentarios coincidan.
    characters = list(text)
    for token in scanned:
        if token[0].startswith('/'):
            characters[token.start():token.end()] = ' ' * len(token[0])
    structure = ''.join(characters)

    def key_pattern(name):
        return r'''(?:\b(?:''' + name + r''')|'(?:''' + name + r''')'|"(?:''' + name + r''')")\s*:\s*'''

    def field(name):
        for token in tokens:
            if re.search(key_pattern(name) + '$', structure[:token.start()]):
                return decode_literal(token[0])
        return None
    title, slug = field('title'), field('slug')
    blocks = re.search(key_pattern('blocks') + r'\[', structure)
    if not title or not slug or not blocks:
        return None
    start = blocks.end()
    values = [title,field('summary') or '']
    excluded = {'p','h2','h3','list','steps','table','callout','image','tip','warning','info','video','link'}
    for token in tokens:
        if token.start() < start:
            continue
        # Las claves JSON también son literales: no indexar "text", "items", etc.
        if re.match(r'\s*:', structure[token.end():]):
            continue
        value = decode_literal(token[0]).strip()
        before = structure[:token.start()]
        if re.search(key_pattern('type|image|imageAlt|src|href|variant|tone|url') + '$', before):
            continue
        if value and value not in excluded and not value.startswith(('/','http:','https:')):
            values.append(value)
    section = field('section')
    return {'title':title,'slug':f'{section}/{slug}' if section else slug,
            'content':'\n'.join(values),'source':str(path.relative_to(ROOT.parent))}


def split_chunks(text,size=800,overlap=150):
    text = re.sub(r'[ \t]+',' ',text).strip()
    offset = 0
    while offset < len(text):
        end = min(offset+size,len(text))
        if end<len(text):
            boundary = max(text.rfind('\n',offset+size//2,end),text.rfind(' ',offset+size//2,end))
            if boundary>offset:
                end=boundary
        yield text[offset:end].strip()
        if end==len(text):
            break
        offset = max(offset+1,end-overlap)


def ingest(document):
    # Si Ollama falla se conserva íntegra la versión previa del documento.
    fragments = list(split_chunks(document['content']))
    vectors = [vector_literal(embed(fragment)) for fragment in fragments]
    with agent_db() as connection:
        row = connection.execute('''INSERT INTO documents(source,slug,title,url)
            VALUES (%s,%s,%s,%s) ON CONFLICT(slug) DO UPDATE SET source=excluded.source,
            title=excluded.title,url=excluded.url,updated_at=now() RETURNING id''',
            (document['source'],document['slug'],document['title'],'/ayuda/'+document['slug'])).fetchone()
        connection.execute('DELETE FROM chunks WHERE document_id=%s',(row['id'],))
        for index,(fragment,vector) in enumerate(zip(fragments,vectors)):
            connection.execute('''INSERT INTO chunks(document_id,ord,content,embedding)
                VALUES (%s,%s,%s,%s::vector)''',(row['id'],index,fragment,vector))
    print(f'{document["slug"]}: {len(fragments)} fragmentos',flush=True)


def ingest_demo():
    demo_dir = ROOT / 'knowledge_demo'
    md_files = sorted(demo_dir.glob('*.md'))
    if not md_files:
        print(f'No se encontraron archivos .md en {demo_dir}')
        return
    count = 0
    for path in md_files:
        text = path.read_text(encoding='utf-8-sig')
        match_title = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        title = match_title.group(1).strip() if match_title else path.stem.replace('_', ' ').title()
        slug = f'demo/{path.stem}'
        source = f'demo/{path.name}'
        document = {
            'source': source,
            'slug': slug,
            'title': title,
            'content': text,
        }
        ingest(document)
        count += 1
    print(f'Ingesta demo finalizada: {count} documentos de Raíz Viva Naturales cargados con prefijo source="demo/".')


if __name__ == '__main__':
    if '--demo' in sys.argv:
        print('Iniciando ingesta en modo demo (Raíz Viva Naturales)...')
        ingest_demo()
    else:
        paths = sorted((ROOT.parent/'Frontend/src/features/help-center/content').rglob('*.ts'))
        article_paths = [path for path in paths if path.name != 'index.ts']
        skipped = [(path, 'módulo índice, no es un artículo') for path in paths if path.name == 'index.ts']
        count = 0
        for path in article_paths:
            document = parse_article(path)
            if document:
                ingest(document)
                count += 1
            else:
                skipped.append((path, 'faltan title, slug o blocks con una lista'))
        context = ROOT.parent/'docs/orquestacion/CONTEXTO.md'
        if context.exists():
            ingest({'source':str(context.relative_to(ROOT.parent)),'slug':'negocio/contexto',
                    'title':'Decisiones de negocio de Atlas','content':context.read_text(encoding='utf-8-sig')})
        print(f'Ingesta finalizada: {count} de {len(article_paths)} artículos encontrados cargados. Atlas no se modificó.')
        print(f'Archivos TypeScript encontrados: {len(paths)}; omitidos: {len(skipped)}.')
        for path, reason in skipped:
            print(f'  Omitido: {path.relative_to(ROOT.parent)} ({reason})')
        if not skipped:
            print('No se omitieron archivos.')
