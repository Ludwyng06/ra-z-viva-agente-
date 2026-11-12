import ctypes.util
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import soundfile as sf

_model = None
_lock = threading.Lock()
_transcribe_lock = threading.Lock()


def available():
    return bool(shutil.which('ffmpeg') and importlib.util.find_spec('faster_whisper'))


def _cuda_usable():
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() <= 0:
            return False
        # ctranslate2 en Windows requiere cublas64_12.dll
        return bool(ctypes.util.find_library('cublas64_12') or ctypes.util.find_library('cublas64_11'))
    except Exception:
        return False


def get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            threads = min(8, os.cpu_count() or 4)
            if _cuda_usable():
                try:
                    m = WhisperModel('small', device='cuda', compute_type='float16')
                    import numpy as np
                    # Verificar que CUDA realmente ejecute inferencia sin error de DLL
                    list(m.transcribe(np.zeros(16000, dtype=np.float32), language='es', beam_size=1, vad_filter=False)[0])
                    _model = m
                except Exception:
                    _model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=threads)
            else:
                _model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=threads)
    return _model


def warmup():
    """Precalentar Whisper para evitar penalizaciones de arranque en frío en la primera consulta."""
    if not available():
        return
    try:
        model = get_model()
        import numpy as np
        dummy = np.zeros(16000, dtype=np.float32)
        with _transcribe_lock:
            list(model.transcribe(dummy, language='es', beam_size=1, vad_filter=False)[0])
    except Exception:
        pass


def _decode_audio(data):
    # 1. Intento directo en memoria si el audio ya es WAV 16 kHz mono
    try:
        samples, sr = sf.read(io.BytesIO(data), dtype='float32')
        if sr == 16000 and samples.size > 0:
            if samples.ndim == 2:
                samples = samples.mean(axis=1)
            if samples.ndim == 1:
                return samples
    except Exception:
        pass

    # 2. Conversión por pipe en memoria con ffmpeg (evita I/O de disco)
    try:
        proc = subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
             '-t', '120', '-ac', '1', '-ar', '16000', '-f', 'wav', 'pipe:1'],
            input=data, check=True, timeout=45, capture_output=True
        )
        samples, sr = sf.read(io.BytesIO(proc.stdout), dtype='float32')
        if sr == 16000 and samples.ndim == 1 and samples.size > 0:
            return samples
    except Exception:
        pass

    # 3. Fallback a directorio temporal en disco por robustez
    with tempfile.TemporaryDirectory(prefix='atlas-stt-') as folder:
        original, converted = Path(folder) / 'input.audio', Path(folder) / 'input.wav'
        original.write_bytes(data)
        subprocess.run(
            ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(original),
             '-t', '120', '-ac', '1', '-ar', '16000', str(converted)],
            check=True, timeout=45, capture_output=True
        )
        samples, sr = sf.read(str(converted), dtype='float32')
        if sr != 16000 or samples.ndim != 1 or samples.size == 0:
            raise ValueError('El audio convertido debe ser mono, de 16 kHz y contener muestras.')
        return samples


def transcribe(data):
    global _model
    samples = _decode_audio(data)
    if samples is None or samples.size == 0:
        raise ValueError('El audio convertido debe ser mono, de 16 kHz y contener muestras.')

    with _transcribe_lock:
        model = get_model()
        try:
            # beam_size=1 (greedy) con condition_on_previous_text=False es conservador y mucho más rápido
            segments, _ = model.transcribe(
                samples, language='es', beam_size=1, vad_filter=True, condition_on_previous_text=False
            )
            text = ' '.join(part.text.strip() for part in segments).strip()
        except Exception:
            if getattr(model, 'model', None) and getattr(model.model, 'device', None) == 'cpu':
                raise
            # Si falló en GPU, recrear en CPU
            from faster_whisper import WhisperModel
            threads = min(8, os.cpu_count() or 4)
            with _lock:
                _model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=threads)
            segments, _ = _model.transcribe(
                samples, language='es', beam_size=1, vad_filter=True, condition_on_previous_text=False
            )
            text = ' '.join(part.text.strip() for part in segments).strip()

        # Si greedy devolvió vacío por alguna razón, reintentar con beam_size=3
        if not text:
            segments, _ = model.transcribe(samples, language='es', beam_size=3, vad_filter=True)
            text = ' '.join(part.text.strip() for part in segments).strip()

    if not text:
        raise ValueError('No se entendió el audio; intenta hablar de nuevo.')
    return text
