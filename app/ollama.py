from functools import lru_cache
import logging
import threading
import httpx
from .config import OLLAMA_URL, EMBED_MODEL

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    if _client is None or _client.is_closed:
        with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.Client(timeout=120)
    return _client


@lru_cache(maxsize=256)
def embed(text):
    client = _get_client()
    try:
        response = client.post(
            f'{OLLAMA_URL}/api/embed',
            json={'model': EMBED_MODEL, 'input': text, 'keep_alive': '30m'}
        )
        response.raise_for_status()
    except (httpx.TransportError, httpx.HTTPStatusError):
        # En caso de conexión caída o reseteada, reintentar con nuevo cliente
        with _client_lock:
            global _client
            try:
                if _client and not _client.is_closed:
                    _client.close()
            except Exception:
                pass
            _client = httpx.Client(timeout=120)
        response = _client.post(
            f'{OLLAMA_URL}/api/embed',
            json={'model': EMBED_MODEL, 'input': text, 'keep_alive': '30m'}
        )
        response.raise_for_status()

    vector = response.json()['embeddings'][0]
    if len(vector) != 768:
        raise ValueError(f'Se requieren 768 dimensiones; recibidas {len(vector)}')
    return vector


def vector_literal(vector):
    return '[' + ','.join(str(float(x)) for x in vector) + ']'


def warmup():
    """Cargar modelos sin generar texto; la primera consulta de voz o texto no paga la carga."""
    from .config import LLM_MODEL
    ollama_error = None

    # 1. Precalentar LLM de Ollama
    client = _get_client()
    try:
        response = client.post(f'{OLLAMA_URL}/api/chat', json={
            'model': LLM_MODEL, 'messages': [], 'keep_alive': '30m',
            'options': {'num_ctx': 8192}, 'stream': False, 'think': False})
        response.raise_for_status()
    except httpx.HTTPError as err:
        ollama_error = err

    # 2. Precalentar modelo de embeddings de Ollama (evita 2s de demora en la primera búsqueda RAG)
    try:
        response = client.post(f'{OLLAMA_URL}/api/embed', json={
            'model': EMBED_MODEL, 'input': 'calentamiento de embeddings', 'keep_alive': '30m'})
        response.raise_for_status()
    except Exception:
        pass

    # 3. Precalentar STT (Whisper) en segundo plano/arranque
    try:
        from .stt import warmup as stt_warmup
        stt_warmup()
    except Exception as err:
        logger.warning('No se pudo precalentar STT: %s', err)

    # 4. Precalentar TTS (Piper) en segundo plano/arranque
    try:
        from .tts import warmup as tts_warmup
        tts_warmup()
    except Exception as err:
        logger.warning('No se pudo precalentar TTS: %s', err)

    if ollama_error is not None:
        raise ollama_error
