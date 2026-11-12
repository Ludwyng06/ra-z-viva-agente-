"""Configuración local sin dependencias adicionales."""
import os
from pathlib import Path
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit, urlunsplit

try:
    BOGOTA = ZoneInfo('America/Bogota')
except ZoneInfoNotFoundError:
    # Windows puede no traer tzdata. Colombia usa UTC-5 sin horario de verano.
    BOGOTA = timezone(timedelta(hours=-5), name='America/Bogota')

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines() if (ROOT / '.env').exists() else []:
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
def ipv4_loopback(url):
    # También evita la demora si un .env existente todavía usa localhost.
    parts = urlsplit(url)
    if parts.hostname == 'localhost':
        credentials, separator, host = parts.netloc.rpartition('@')
        netloc = (credentials + separator if separator else '') + '127.0.0.1' + host[len('localhost'):]
        return urlunsplit(parts._replace(netloc=netloc))
    return url


COPILOT_MODE = os.getenv('COPILOT_MODE', 'demo').lower()
ATLAS_DSN = ipv4_loopback(os.getenv('ATLAS_DATABASE_URL', 'postgresql://copiloto_ro:copiloto_ro_local@127.0.0.1:5433/atlas'))
AGENT_DSN = ipv4_loopback(os.getenv('AGENT_DATABASE_URL', 'postgresql://copiloto:copiloto_local@127.0.0.1:5434/copiloto'))
DEMO_DSN = ipv4_loopback(os.getenv('DEMO_DATABASE_URL', 'postgresql://copiloto:copiloto_local@127.0.0.1:5434/copiloto'))
OLLAMA_URL = ipv4_loopback(os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')).rstrip('/')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen3:4b-instruct')
EMBED_MODEL = os.getenv('EMBED_MODEL', 'embeddinggemma')
VOICE_PATH = Path(os.getenv('PIPER_VOICE', str(ROOT / 'voices/es_MX-claude-high.onnx')))
AUDIO_DIR = ROOT / 'audio'
