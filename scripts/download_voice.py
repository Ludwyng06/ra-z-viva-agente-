"""Respaldo opcional; conserva la voz ya descargada."""
import sys
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import VOICE_PATH

BASE = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high/es_MX-claude-high.onnx'

if __name__ == '__main__':
    VOICE_PATH.parent.mkdir(parents=True,exist_ok=True)
    with httpx.Client(follow_redirects=True,timeout=180) as client:
        for suffix in ('','.json'):
            target = Path(str(VOICE_PATH)+suffix)
            if target.exists():
                print(f'Ya existe: {target.name}')
                continue
            temporary = Path(str(target)+'.part')
            try:
                with client.stream('GET',BASE+suffix) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as output:
                        for block in response.iter_bytes():
                            output.write(block)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            print(f'Descargada: {target.name}')
