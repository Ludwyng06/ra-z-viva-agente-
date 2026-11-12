from contextlib import asynccontextmanager
import logging
import time
import uuid
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, Field
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool
from . import agent, stt, tts
from .config import ROOT, AUDIO_DIR, OLLAMA_URL, LLM_MODEL, EMBED_MODEL, COPILOT_MODE
from .db import agent_db, atlas_db, demo_db
from .tools_demo import confirmar_accion_demo, resumen_hoy, ensure_order_contact_schema, public_order_payload
from .reports import router as reports_router

logger = logging.getLogger(__name__)

title_name = 'Agente de Raíz Viva Naturales' if COPILOT_MODE == 'demo' else 'Copiloto de Atlas'
desc_name = 'Asistente por voz y texto para tu tienda en línea.' if COPILOT_MODE == 'demo' else 'Atlas solo lectura y acciones simuladas.'

@asynccontextmanager
async def lifespan(app):
    from .ollama import warmup
    if COPILOT_MODE == 'demo':
        await run_in_threadpool(ensure_order_contact_schema)
    try:
        await run_in_threadpool(warmup)
    except httpx.HTTPError:
        logger.warning('No se pudo precalentar Ollama; se reintentar? en la consulta.')
    yield
    from .db import close_connections
    close_connections()


app = FastAPI(title=title_name, description=desc_name, lifespan=lifespan)
app.include_router(reports_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:8765', 'http://127.0.0.1:8765'],
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Content-Type', 'Authorization'],
)


class ChatRequest(BaseModel):
    conversationId: str | None = Field(default=None, validation_alias=AliasChoices("conversationId", "conversation_id"))
    text: str = Field(min_length=1, max_length=6000)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


@app.get('/api/resumen-hoy')
def get_today_summary():
    if COPILOT_MODE != 'demo':
        raise HTTPException(404, 'Resumen disponible en modo tienda demo.')
    try:
        return resumen_hoy()
    except Exception:
        logger.exception('No se pudo consultar el resumen de hoy')
        raise HTTPException(503, 'No se pudo consultar el resumen de hoy.')


@app.get('/api/config')
def get_config():
    if COPILOT_MODE == 'demo':
        return {
            'mode': 'demo',
            'appName': 'Agente de Raíz Viva Naturales',
            'storeName': 'Raíz Viva Naturales',
            'examples': [
                '¿Cuántos pedidos van este mes?',
                '¿Qué producto se vende más?',
                'Cotiza un envío de 2 mieles a Pasto',
                'Crea un pedido para Laura Gómez en Medellín: 2 tés verdes, contra entrega',
                '¿Qué novedades tengo abiertas?',
                '¿Qué productos se están agotando?',
                '¿Cómo funcionan las devoluciones?',
            ],
        }
    return {
        'mode': 'atlas',
        'appName': 'Copiloto de Atlas',
        'storeName': 'Atlas',
        'examples': [
            '¿Cómo va el día de hoy?',
            '¿Qué pedidos están confirmados?',
            '¿Hay novedades abiertas?',
            '¿Qué productos tienen poco stock?',
            '¿Cómo activo el modo Automático de Atlas?',
        ],
    }


@app.get('/api/health')
def health():
    result = {k: False for k in ('ok', 'llm', 'embeddings', 'stt', 'tts', 'atlasDb', 'agentDb', 'demoDb')}
    try:
        response = httpx.get(f'{OLLAMA_URL}/api/tags', timeout=5)
        response.raise_for_status()
        names = {m['name'] for m in response.json().get('models', [])}
        result['llm'] = LLM_MODEL in names or LLM_MODEL + ':latest' in names
        result['embeddings'] = EMBED_MODEL in names or EMBED_MODEL + ':latest' in names
    except Exception:
        pass

    # En modo demo, 'atlasDb' representa el indicador 'BD Tienda' y verifica el esquema tienda
    if COPILOT_MODE == 'demo':
        try:
            with demo_db() as connection:
                connection.execute('SELECT 1 FROM tienda.pedidos LIMIT 1')
                result['atlasDb'] = True
                result['demoDb'] = True
        except Exception:
            pass
    else:
        try:
            with atlas_db() as connection:
                connection.execute('SELECT 1')
                result['atlasDb'] = True
        except Exception:
            pass

    try:
        with agent_db() as connection:
            connection.execute('SELECT id FROM conversations LIMIT 1')
            result['agentDb'] = True
    except Exception:
        pass

    result['stt'], result['tts'] = stt.available(), tts.available()
    result['ok'] = all(result[k] for k in ('llm', 'embeddings', 'atlasDb', 'agentDb'))
    return result


def generate_title(text: str) -> str:
    cleaned = ' '.join(text.strip().split())
    words = cleaned.split()
    if not words:
        return 'Nueva conversación'
    title = ' '.join(words[:8])
    if len(title) > 100:
        title = title[:100].rstrip()
    return title


def process_chat(text, conversation_id):
    if not text.strip():
        raise HTTPException(422, 'Escribe una pregunta o graba un audio.')
    try:
        response = agent.chat(text.strip(), conversation_id)
    except ValueError:
        raise HTTPException(422, 'Identificador o argumento no válido.')
    except LookupError:
        raise HTTPException(404, 'Conversación no encontrada.')
    except Exception:
        logger.exception('No se pudo procesar la conversación')
        raise HTTPException(503, 'No pude procesar la consulta. Revisa la base del agente y los servicios locales.')

    cid = response.get('conversationId')
    if cid:
        try:
            with agent_db() as connection:
                row = connection.execute('SELECT title FROM conversations WHERE id=%s', (cid,)).fetchone()
                if row and not row.get('title'):
                    auto_title = generate_title(text)
                    connection.execute(
                        'UPDATE conversations SET title=%s, updated_at=now() WHERE id=%s',
                        (auto_title, cid)
                    )
                    response['conversationTitle'] = auto_title
                elif row:
                    connection.execute('UPDATE conversations SET updated_at=now() WHERE id=%s', (cid,))
                    response['conversationTitle'] = row.get('title')
        except Exception:
            logger.warning('No se pudo actualizar título/updated_at de la conversación %s', cid)

    return response


@app.post('/api/chat')
def chat(body: ChatRequest):
    return process_chat(body.text, body.conversationId)


@app.post('/api/voice')
async def voice(audio: UploadFile = File(...), conversationId: str | None = Form(None)):
    start = time.perf_counter()
    try:
        data = await audio.read(20 * 1024 * 1024 + 1)
    finally:
        await audio.close()
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, 'El audio debe contener datos y pesar menos de 20 MB.')
    try:
        transcript = await run_in_threadpool(stt.transcribe, data)
    except Exception:
        raise HTTPException(422, 'No pude transcribir el audio. Revisa ffmpeg y Whisper o intenta de nuevo.')
    stt_ms = round((time.perf_counter() - start) * 1000)
    response = await run_in_threadpool(process_chat, transcript, conversationId)
    tick = time.perf_counter()
    audio_url = await run_in_threadpool(tts.synthesize, response['answer'])
    response.update(transcript=agent.safe_answer(transcript), audioUrl=audio_url)
    response['timings'].update(
        sttMs=stt_ms,
        ttsMs=round((time.perf_counter() - tick) * 1000),
        totalMs=round((time.perf_counter() - start) * 1000),
    )
    return response


@app.post('/api/tts')
def text_to_speech(body: TTSRequest):
    return {'audioUrl': tts.synthesize(agent.safe_answer(body.text))}


@app.get('/api/audio/{ident}.wav')
def audio_file(ident: uuid.UUID):
    path = AUDIO_DIR / f'{ident}.wav'
    if not path.is_file():
        raise HTTPException(404, 'Audio no encontrado o vencido.')
    return FileResponse(path, media_type='audio/wav')


def resolve_atlas_action(ident, status):
    with agent_db() as connection:
        row = connection.execute('SELECT * FROM pending_actions WHERE id=%s FOR UPDATE', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Acción no encontrada.')
        if row['status'] not in ('pending', status):
            raise HTTPException(409, 'Esta acción ya se resolvió de otra forma.')
        connection.execute('''UPDATE pending_actions SET status=%s,resolved_at=coalesce(resolved_at,now())
            WHERE id=%s''', (status, ident))
        message = ('Acción confirmada de forma simulada. No se modificó Atlas.' if status == 'confirmed_simulated'
                   else 'Propuesta cancelada. No se modificó Atlas.')
        if row['status'] == 'pending':
            connection.execute("INSERT INTO messages(conversation_id,role,content) VALUES (%s,'assistant',%s)",
                               (row['conversation_id'], message))
    return {'id': str(ident), 'status': status, 'message': message}


def resolve_demo_action(ident, status):
    if status == 'confirmed':
        res = confirmar_accion_demo(ident)
        if not res.get('ok'):
            raise HTTPException(400, res.get('error', 'No se pudo confirmar la acción.'))
        message = res['message']
        with agent_db() as conn:
            row = conn.execute('SELECT conversation_id FROM pending_actions WHERE id = %s', (ident,)).fetchone()
            if row:
                conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (%s, 'assistant', %s)",
                             (row['conversation_id'], message))
        return {'id': str(ident), 'status': 'confirmed', 'message': message}
    else:
        with agent_db() as conn:
            row = conn.execute('SELECT * FROM pending_actions WHERE id = %s FOR UPDATE', (ident,)).fetchone()
            if not row:
                raise HTTPException(404, 'Acción no encontrada.')
            if row['status'] != 'pending':
                raise HTTPException(400, 'Esta acción ya se resolvió.')
            conn.execute("UPDATE pending_actions SET status = 'cancelled', resolved_at = coalesce(resolved_at, now()) WHERE id = %s", (ident,))
            order_ref = row['order_ref']
            message = ("Propuesta de compra cancelada. No se guardó ninguna orden de compra." if row['kind'] == 'orden_compra'
                       else f"Propuesta sobre el pedido {order_ref} cancelada. No se modificó Raíz Viva Naturales.")
            conn.execute("INSERT INTO messages(conversation_id, role, content) VALUES (%s, 'assistant', %s)",
                         (row['conversation_id'], message))
        return {'id': str(ident), 'status': 'cancelled', 'message': message}


@app.post('/api/actions/{ident}/confirm')
def confirm_action(ident: uuid.UUID):
    if COPILOT_MODE == 'demo':
        return resolve_demo_action(ident, 'confirmed')
    return resolve_atlas_action(ident, 'confirmed_simulated')


@app.post('/api/actions/{ident}/cancel')
def cancel_action(ident: uuid.UUID):
    if COPILOT_MODE == 'demo':
        return resolve_demo_action(ident, 'cancelled')
    return resolve_atlas_action(ident, 'cancelled')


@app.get('/api/conversations')
def list_conversations():
    with agent_db() as connection:
        rows = connection.execute('''
            SELECT c.id,
                   COALESCE(c.title, 'Nueva conversación') AS title,
                   c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.deleted_at IS NULL
            GROUP BY c.id, c.title, c.updated_at
            ORDER BY c.updated_at DESC
            LIMIT 100
        ''').fetchall()
    return [
        {
            'id': str(r['id']),
            'title': r['title'],
            'updatedAt': r['updated_at'].isoformat() if r.get('updated_at') else None,
            'messageCount': int(r['message_count'] or 0),
        }
        for r in rows
    ]


@app.get('/api/conversations/{ident}')
def conversation(ident: uuid.UUID):
    with agent_db() as connection:
        conv = connection.execute(
            'SELECT id, title, updated_at FROM conversations WHERE id=%s AND deleted_at IS NULL',
            (ident,)
        ).fetchone()
        if not conv:
            raise HTTPException(404, 'Conversación no encontrada.')
        messages = connection.execute('''
            SELECT id, role, content, created_at
            FROM messages
            WHERE conversation_id=%s
            ORDER BY id
        ''', (ident,)).fetchall()
        calls = connection.execute('''
            SELECT message_id, name, args, ok, ms, result_summary AS summary
            FROM tool_calls
            WHERE conversation_id=%s
            ORDER BY id
        ''', (ident,)).fetchall()
        formatted_messages = [
            {
                'id': m['id'],
                'role': m['role'],
                'content': m['content'],
                'createdAt': m['created_at'].isoformat() if hasattr(m['created_at'], 'isoformat') else str(m['created_at']),
            }
            for m in messages
        ]
        pending_row = connection.execute('''
            SELECT id, kind, order_ref, status, created_at, payload
            FROM pending_actions
            WHERE conversation_id=%s AND status='pending'
            ORDER BY created_at DESC LIMIT 1
        ''', (ident,)).fetchone()
        pending_data = None
        if pending_row:
            pending_data = {
                'id': str(pending_row['id']),
                'kind': pending_row['kind'],
                'orderRef': pending_row['order_ref'],
                'orderNumber': pending_row['order_ref'],
                'status': pending_row['status'],
                'description': (pending_row['payload'] or {}).get('resumen') or f"Propuesta sobre el pedido {pending_row['order_ref']}",
                'payload': public_order_payload(pending_row['payload']),
            }
    return {
        'conversationId': str(ident),
        'title': conv['title'] or 'Nueva conversación',
        'updatedAt': conv['updated_at'].isoformat() if conv.get('updated_at') else None,
        'messages': formatted_messages,
        'toolCalls': calls,
        'pendingAction': pending_data,
    }


@app.patch('/api/conversations/{ident}')
def rename_conversation(ident: uuid.UUID, body: RenameConversationRequest):
    new_title = body.title.strip()
    if not new_title:
        raise HTTPException(422, 'El título no puede estar vacío.')
    with agent_db() as connection:
        row = connection.execute('''
            UPDATE conversations
            SET title = %s, updated_at = now()
            WHERE id = %s AND deleted_at IS NULL
            RETURNING id, title, updated_at
        ''', (new_title, ident)).fetchone()
        if not row:
            raise HTTPException(404, 'Conversación no encontrada o eliminada.')
    return {
        'id': str(row['id']),
        'title': row['title'],
        'updatedAt': row['updated_at'].isoformat() if row.get('updated_at') else None,
    }


@app.delete('/api/conversations/{ident}')
def delete_conversation(ident: uuid.UUID):
    with agent_db() as connection:
        row = connection.execute('''
            UPDATE conversations
            SET deleted_at = now()
            WHERE id = %s AND deleted_at IS NULL
            RETURNING id
        ''', (ident,)).fetchone()
        if not row:
            raise HTTPException(404, 'Conversación no encontrada o ya eliminada.')
    return {'id': str(row['id']), 'ok': True}


class RevalidatingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers['Cache-Control'] = 'no-cache'
        return response


app.mount('/', RevalidatingStaticFiles(directory=str(ROOT / 'web'), html=True), name='web')
