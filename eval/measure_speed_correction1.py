import httpx,json,time
qs=['cuantos pedidos van este mes','cotiza envio de 2 mieles a Pasto','crea un pedido para Laura Gomez en Medellin 2 tes verdes contra entrega','que productos se estan agotando','como funcionan las devoluciones']
rows=[]
with httpx.Client(timeout=240) as c:
 print(c.get('http://127.0.0.1:8765/api/health').text,flush=True)
 cid=None
 for i,q in enumerate(qs+['quiero crear un pedido para Laura Gomez en Medellin','agrega 2 tes verdes','contra entrega','la mas barata']):
  body={'text':q}
  if i>5: body['conversationId']=cid
  d=c.post('http://127.0.0.1:8765/api/chat',json=body).json()
  if i>=5: cid=d['conversationId']
  rows.append({'q':q,**d});print(json.dumps(rows[-1],ensure_ascii=True),flush=True)
open('eval/speed_correction1.json','w',encoding='utf8').write(json.dumps(rows,ensure_ascii=False,indent=2))
