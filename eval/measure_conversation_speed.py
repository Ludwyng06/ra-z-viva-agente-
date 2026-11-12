"""Real local model and databases; no order is confirmed. Run from copiloto."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import chat
if '--api' in sys.argv:
    import httpx
    client = httpx.Client(timeout=120)
    print(client.get('http://127.0.0.1:8765/api/health').text, flush=True)
    def chat(text, cid=None):
        response = client.post('http://127.0.0.1:8765/api/chat', json={'text': text, 'conversationId': cid})
        response.raise_for_status()
        return response.json()
qs = ['quiero crear un pedido para Laura Gomez', 'la ciudad es Horito', 'esta en puto mayo', 'agrega 2 tes berdes', 'contra entrega', 'la mas barata']
rows=[]
cid=None
for q in qs:
    d=chat(q,cid)
    cid=d['conversationId']
    rows.append({'q':q,**d})
    print(json.dumps(rows[-1],ensure_ascii=True),flush=True)
(Path(__file__).parent / ('conversation_'+sys.argv[1]+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
