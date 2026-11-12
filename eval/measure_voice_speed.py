import httpx,json,time
from pathlib import Path
from app.tts import synthesize
from app.config import ROOT
qs=json.loads(Path('eval/speed_before.json').read_text(encoding='utf8'))
rows=[]
with httpx.Client(timeout=240) as c:
 for q in qs:
  url=synthesize(q['q']); data=(ROOT/'audio'/url.split('/')[-1]).read_bytes()
  d=c.post('http://127.0.0.1:8765/api/voice',files={'audio':('pregunta.wav',data,'audio/wav')}).json()
  rows.append({'q':q['q'],**d}); print(json.dumps({'q':q['q'],'timings':d.get('timings')},ensure_ascii=True),flush=True)
Path('eval/speed_voice_after.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
