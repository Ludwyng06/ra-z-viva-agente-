import httpx,json,time
qs=['cuantos pedidos van este mes','cotiza envio de 2 mieles a Pasto','crea un pedido para Laura Gomez en Medellin 2 tes verdes contra entrega','que productos se estan agotando','como funcionan las devoluciones']
r=[]
with httpx.Client(timeout=240) as c:
 for q in qs:
  t=time.perf_counter(); d=c.post('http://127.0.0.1:8765/api/chat',json={'text':q}).json(); r.append({'q':q,'wallMs':round((time.perf_counter()-t)*1000),**d}); print(json.dumps(r[-1],ensure_ascii=False),flush=True)
open('eval/speed_before.json','w',encoding='utf8').write(json.dumps(r,ensure_ascii=False,indent=2))
