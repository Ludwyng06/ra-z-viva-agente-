import importlib.util
import io
import os
import re
import threading
import uuid
import wave
from pathlib import Path
from .config import VOICE_PATH, AUDIO_DIR

_voice = None
_failed = False
_lock = threading.Lock()
_synthesis_lock = threading.Lock()
_cleanup_counter = 0


def available():
    return bool(not _failed and VOICE_PATH.is_file() and VOICE_PATH.with_suffix('.onnx.json').is_file()
                and importlib.util.find_spec('piper'))


def get_voice():
    global _voice
    with _lock:
        if _voice is None:
            from piper import PiperVoice
            _voice = PiperVoice.load(str(VOICE_PATH))
    return _voice


def warmup():
    """Precalentar Piper cargando el modelo en memoria y sintetizando un token mínimo."""
    if not available():
        return
    try:
        voice = get_voice()
        with io.BytesIO() as buf:
            with wave.open(buf, 'wb') as wav:
                voice.synthesize_wav('ok', wav)
    except Exception:
        pass


def clean_text(text):
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[*_`#>|]', '', text)
    text = re.sub(r'\b(?:COP|\$)\s*([\d.]+)', lambda m: m[1].replace('.','') + ' pesos colombianos', text)
    text = re.sub(r'(?<=\d)\.(?=\d{3}(?:\D|$))', '', text)
    text = re.sub(r'https?://\S+', '', text)
    # Omitir sección de fuentes para audio; es metadata visual del chat
    text = re.sub(r'(?i)\n*fuentes\s*:.*$', '', text, flags=re.S)
    return re.sub(r'\s+', ' ', text).strip()[:6000]


def _prune_audio_cache():
    global _cleanup_counter
    _cleanup_counter += 1
    if _cleanup_counter % 25 != 0:
        return
    try:
        entries = []
        with os.scandir(AUDIO_DIR) as it:
            for entry in it:
                if entry.name.endswith('.wav') and entry.is_file():
                    try:
                        entries.append((entry.stat().st_mtime, entry.path))
                    except OSError:
                        pass
        if len(entries) > 100:
            entries.sort(key=lambda x: x[0], reverse=True)
            for _, fpath in entries[99:]:
                try:
                    os.unlink(fpath)
                except OSError:
                    pass
    except Exception:
        pass


def synthesize(text):
    global _failed
    if not available():
        return None
    cleaned = clean_text(text)
    if not cleaned:
        return None

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    # Identificador determinista RFC 4122 para reutilizar audios idénticos (0 ms)
    ident = str(uuid.uuid5(uuid.NAMESPACE_DNS, cleaned))
    target_path = AUDIO_DIR / f'{ident}.wav'

    # Verificación rápida sin lock si ya existe
    if target_path.is_file() and target_path.stat().st_size > 44:
        try:
            os.utime(target_path, None)
        except OSError:
            pass
        return f'/api/audio/{ident}.wav'

    temp_path = None
    try:
        with _synthesis_lock:
            if target_path.is_file() and target_path.stat().st_size > 44:
                return f'/api/audio/{ident}.wav'

            _prune_audio_cache()

            temp_path = AUDIO_DIR / f'{ident}.tmp_{uuid.uuid4().hex[:6]}'
            with wave.open(str(temp_path), 'wb') as wav:
                get_voice().synthesize_wav(cleaned, wav)

            if temp_path.is_file() and temp_path.stat().st_size > 44:
                temp_path.replace(target_path)
            else:
                if temp_path and temp_path.is_file():
                    temp_path.unlink(missing_ok=True)
                return None

        return f'/api/audio/{ident}.wav'
    except Exception:
        _failed = True
        if temp_path and temp_path.is_file():
            temp_path.unlink(missing_ok=True)
        return None
