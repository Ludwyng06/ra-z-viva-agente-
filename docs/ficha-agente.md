# Ficha del Agente: Agente de Raíz Viva Naturales

**Proyecto Académico:** UniPutumayo – Agentes de IA (2026).  
**Categoría:** Agente de operación por voz para tiendas en línea (Raíz Viva Naturales).  
**Caso de Aplicación Práctica:** "Raíz Viva Naturales" (tienda ficticia de productos naturales, bienestar y cosmética artesanal con modelo de pago contra entrega en Colombia).

---

## 1. Ficha Resumen (Formato Académico del Docente)

- **Sector:** Comercio electrónico / MiPymes y logística de última milla.
- **Qué hace:** Es el copiloto inteligente del dueño o administrador de una tienda de comercio electrónico. A través de voz interactiva o texto en lenguaje natural:
  1. Reporta en tiempo real las métricas comerciales y logísticas del periodo (hoy, semana, mes, mes anterior): volumen de pedidos, ventas totales en pesos colombianos (COP), ticket promedio, tasa de entrega, desglose por estado (confirmados, nuevos, despachados, entregados, en novedad, devoluciones, cancelados), proporción de pedidos pagados por anticipado vs. recaudo contra entrega, y comparación con el periodo previo.
  2. Identifica los productos más vendidos (`top_productos`), ciudades con mayor demanda (`ventas_por_ciudad`) y el desempeño comparativo de transportadoras (`rendimiento_transportadoras`).
  3. Consulta pedidos específicos por su código (`buscar_pedido`), filtra pedidos por estado operativo (`pedidos_por_estado`) e informa alertas de novedades abiertas en ruta (`novedades_abiertas`) y productos con inventario crítico (`alertas_stock`).
  4. Explica con claridad los motivos por los cuales pedidos nuevos requieren revisión humana (`explicar_revision`, como dirección incompleta o municipio sin cobertura).
  5. Cotiza envíos entre diferentes transportadoras aliadas recomendando la más económica y la más rápida para cualquier municipio de Colombia (`cotizar_envio`).
  6. Propone la creación de nuevos pedidos con validación de stock, cálculo de flete y resumen financiero (`proponer_pedido`), así como confirmaciones o cancelaciones (`proponer_cambio_estado`). La acción queda en espera de confirmación humana en la interfaz; al confirmarse se ejecuta formalmente en la base de datos de la tienda.
  7. Responde inquietudes sobre políticas de despacho, pago contra entrega, devoluciones, novedades de entrega y cuidado de productos mediante búsqueda semántica vectorial (`buscar_en_documentos`).
- **Datos en PostgreSQL:** Esquema transaccional `tienda`: tablas `productos` (sku, nombre, categoría, precio, costo, stock, stock mínimo), `clientes` (nombre, ciudad, departamento, teléfono enmascarado), `transportadoras` (Interrapidísimo, Envía, Coordinadora, TCC, Servientrega, Veloces, 99 Minutos), `tarifas` (zonas, valor base y por kilo), `pedidos` (códigos RV-xxxx, cliente, transportadora, estados, método de pago, valores monetarios), `pedido_items`, `novedades` (reportes de entrega en carretera) y `acciones` (auditoría de operaciones ejecutadas por el copiloto).
- **Conocimiento en pgvector:** Colección de documentos de la tienda troceados en fragmentos de texto (chunks) e indexados mediante vectores de 768 dimensiones generados con el modelo local `embeddinggemma`, almacenados en PostgreSQL con índice HNSW optimizado para distancia coseno.
- **Uso de voz:** El usuario habla desde el navegador; el audio se captura mediante MediaRecorder API, se procesa en formato WAV mono a 16 kHz y se transcribe localmente con `faster-whisper` (`small`, español). El LLM razona y selecciona herramientas, y la respuesta final se sintetiza al instante con `piper-tts` (`es_MX-claude-high`), disponiendo de respaldo automático en la síntesis nativa del navegador. Las respuestas son concisas (máximo 3 frases), optimizadas para la comprensión auditiva y protegen la privacidad (nunca leen direcciones completas ni números telefónicos en voz alta).
- **Observación didáctica:** Ejemplifica la arquitectura híbrida recomendada en inteligencia artificial aplicada:
  - *Búsqueda exacta con SQL fijo y parametrizado:* Garantiza cero alucinaciones en cifras financieras, recuentos de inventario y estados de pedidos.
  - *Búsqueda semántica (RAG local):* Resuelve dudas interpretativas de políticas y atención al cliente.
  - *Human-in-the-Loop (confirmación humana):* Las acciones con impacto operativo y económico requieren validación explícita del usuario antes de alterar el estado de la base de datos.
  - *Trazabilidad de caja blanca:* Panel interactivo que expone la transcripción exacta, las herramientas invocadas con sus argumentos JSON, las fuentes documentales recuperadas y las latencias en milisegundos de cada etapa del pipeline.

---

## 2. Arquitectura del Sistema

```
                    [ Micrófono Web (Navegador) ]
                                  │
                                  ▼ Audio (WebM / OGG)
                [ Normalizador ffmpeg (WAV 16 kHz mono) ]
                                  │
                                  ▼
                [ STT Local: faster-whisper small (es) ]
                                  │
                                  ▼ Transcripción texto
                 [ FastAPI Orchestrator (Puerto 8765) ]
                                  │
                                  ▼
             [ LLM Local: Ollama qwen3:4b-instruct ]
                  (Bucle de Razonamiento y Tool Calling)
                     │                            │
      ┌──────────────┴──────────────┐             │
      ▼                             ▼             ▼
[ Herramientas SQL Fijo ]    [ RAG pgvector ]   [ Tarjeta de Acción ]
(Esquema tienda)             (Documentos demo)  (Confirmar / Cancelar)
- metricas_periodo           - políticas envío             │
- top_productos              - contra entrega              ▼
- ventas_por_ciudad          - devoluciones       [ Ejecución en BD ]
- cotizar_envio              - novedades          (Crea pedido / stock)
- proponer_pedido            - productos
- alertas_stock
      │                             │
      └──────────────┬──────────────┘
                     ▼
             Respuesta final concisa
                     │
                     ▼
             [ TTS Local: Piper ] ──► Audio sintetizado (WAV)
                     │
                     ▼
             [ Interfaz Web ] ──► Reproducción + Panel de Trazabilidad
```

---

## 3. Modelo de Datos Relacional y Vectorial

### Base de Datos Transaccional (Esquema `tienda`)

| Tabla | Propósito y Atributos Principales |
|---|---|
| `tienda.productos` | Catálogo de productos naturales: SKU, nombre, categoría, precio de venta en COP, costo unitario, existencia actual de stock y stock mínimo de alerta. |
| `tienda.clientes` | Directorio de clientes con nombre, ciudad, departamento y teléfono ficticio enmascarado (`300 000 0000`). |
| `tienda.transportadoras` | Empresas de logística aliadas (Interrapidísimo, Envía, Coordinadora, TCC, Servientrega, Veloces, 99 Minutos) con tiempos promedio de tránsito. |
| `tienda.tarifas` | Esquema tarifario por transportadora y departamento (tarifa base y recargo por kilogramo). |
| `tienda.pedidos` | Órdenes de compra: código `RV-xxxx`, cliente, destino, transportadora, estado (`nuevo`, `confirmado`, `despachado`, `entregado`, `novedad`, `devolucion`, `cancelado`), método de pago (`contra_entrega`, `pagado`), valores monetarios (subtotal, flete, total en COP), creador (`tienda` o `copiloto`) y `motivo_revision` en caso de alertas iniciales. |
| `tienda.pedido_items` | Detalle de líneas de producto por pedido con cantidad y precio unitario pactado. |
| `tienda.novedades` | Registro de incidencias logísticas en carretera con tipo de novedad, detalle, estado (`abierta`, `resuelta`) y fecha. |
| `tienda.acciones` | Bitácora de auditoría de operaciones ejecutadas por el copiloto con usuario, tipo de acción, parámetros y marca de tiempo. |

### Base del Agente (Persistencia de Conversación y Vectorial `pgvector`)

| Tabla | Propósito |
|---|---|
| `documents` | Metadatos de artículos de la tienda (fuente `demo/<archivo>`, slug, título, URL). |
| `chunks` | Fragmentos de texto con vector de incrustación `embedding vector(768)` e índice HNSW (distancia coseno). |
| `conversations` | Sesiones de interacción conversacional. |
| `messages` | Historial de mensajes (`user`, `assistant`, `tool`). |
| `tool_calls` | Auditoría de llamadas a herramientas: función, argumentos JSON, estado de éxito, duración en milisegundos y resumen. |
| `pending_actions` | Acciones propuestas pendientes de validación humana con estado (`pending`, `confirmed`, `cancelled`). |
| `eval_runs` / `eval_results` | Registro de corridas de evaluación sistemática con métricas y tasa de aprobación. |

---

## 4. Decisiones Técnicas y de Diseño

1. **Privacidad Total y Soberanía de Datos:** Toda la inferencia (STT, LLM, Embeddings y TTS) se ejecuta estrictamente en la infraestructura local (GPU NVIDIA RTX 4050 6 GB). Ninguna información comercial, nombre de cliente o audio es transmitido a APIs en la nube de pago.
2. **Consultas Fijas y Parametrizadas contra Inyección SQL:** El LLM nunca redacta código SQL arbitrario. Dispone de un catálogo cerrado de herramientas con contratos estrictos. Las consultas ejecutan sentencias SQL parametrizadas preparadas por los desarrolladores.
3. **Acciones Operativas Human-in-the-Loop:** Crear un pedido compromete inventario y recursos de flete. Por ende, el agente solo "propone" la operación. El usuario visualiza la tarjeta con el detalle de productos, flete y total en COP, decidiendo si pulsa "Confirmar" o "Cancelar".
4. **Respuestas Diseñadas para Audio:** El prompt de sistema instruye al modelo a responder en máximo 3 oraciones sencillas, con tono cordial colombiano, redondeando cifras monetarias y omitiendo datos de contacto para no saturar al usuario durante la escucha.

---

## 5. Consideraciones Éticas y Regulatorias

- **Guardia de Seguridad Visible (Guardrails Pre-Modelo):** Implementada en el módulo `app/guardrails.py`, opera de forma previa e independiente del modelo de lenguaje en dos niveles complementarios:
  1. *Filtro de Manipulación / Jailbreak:* Detección mediante expresiones regulares normalizadas de cualquier intento de saltar o ignorar instrucciones o reglas ("saltarte las instrucciones", "ignora tus reglas"), suplantaciones de rol ("actúa como", modo desarrollador/DAN, "ahora eres un pirata"), exfiltración de prompts o contraseñas ("muéstrame tu prompt de sistema"), solicitud de datos personales masivos o comandos destructivos (`drop table`, `delete from`). Respuesta fija inmediata y bloqueo total.
  2. *Filtrado Fuera de Dominio:* Detecta temas ajenos al comercio electrónico (deportes/fútbol/Champions League, política, clima, recetas, chistes, etc.) mediante patrones rápidos y micro-clasificación ultrarrápida con LLM local (`qwen3:4b-instruct`, `num_predict: 3`). Respuesta fija: *"Solo puedo ayudarte con la tienda: pedidos, ventas, envíos, productos y clientes."*
  3. *Trazabilidad de Seguridad:* Cada bloqueo genera un registro en `toolCalls` con nombre `guardia_seguridad` (tipo de regla y latencia en ms), se audita en la tabla `tool_calls` de PostgreSQL y se muestra destacado en la interfaz con icono de escudo (🛡️) y color ámbar.
- **Sin Promesas Médicas Engañosas:** Los productos de la tienda son suplementos e infusiones naturales. El agente tiene prohibido afirmar que "curan" o "tratan enfermedades", enfatizando siempre su rol de apoyo al bienestar general y recomendando consulta médica cuando corresponde.
- **Protección de Datos Personales (Ley 1581 de 2012 de Colombia):** Se enmascaran los números celulares y no se emiten direcciones residenciales completas por voz.
- **Resistencia a Inyección de Instrucciones (Prompt Injection):** El prompt clasifica los datos de clientes, productos y notas como literales inertes. Si un texto intenta emitir órdenes, la guardia y el modelo neutralizan el intento.
- **Trazabilidad y Auditoría:** Cada deducción puede ser verificada en el panel lateral de la interfaz web, donde el dueño puede inspeccionar qué función SQL se corrió y qué documentos sustentan la afirmación.

---

## 6. Metodología de Evaluación (14 Casos Automatizados)

La efectividad del copiloto se evalúa sistemáticamente mediante `python eval/run_eval.py --casos eval/casos_demo.yaml`, validando la invocación de herramientas, presencia de palabras clave en la respuesta y latencia:

| # | Caso de Prueba | Categoría | Herramienta Esperada | Validación Principal |
|---|---|---|---|---|
| 1 | Métricas del mes actual | Transaccional | `metricas_periodo` | Reporta pedidos totales, confirmados y ventas en COP |
| 2 | Métricas del día de hoy | Transaccional | `metricas_periodo` | Detalla órdenes del día y comparación |
| 3 | Comparación con mes anterior | Analítica | `metricas_periodo` | Porcentaje de variación o diferencia de pedidos |
| 4 | Producto más vendido | Comercial | `top_productos` | Identifica el producto líder del catálogo |
| 5 | Buscar pedido existente | Búsqueda exacta | `buscar_pedido` | Código RV, estado, cliente y transportadora |
| 6 | Buscar pedido inexistente | Manejo de bordes | `buscar_pedido` | Admite que no existe sin inventar datos |
| 7 | Pedidos por estado | Operativa | `pedidos_por_estado` | Listado filtrado (ej. pedidos nuevos o en novedad) |
| 8 | Novedades abiertas | Logística | `novedades_abiertas` | Envíos con problemas reportados por transportadoras |
| 9 | Alertas de stock crítico | Inventario | `alertas_stock` | Productos con existencia igual o menor al mínimo |
| 10 | Explicar motivo de revisión | Lógica de negocio | `explicar_revision` | Causas de retención de pedidos nuevos |
| 11 | Cotizar envío a una ciudad | Logística tarifaria | `cotizar_envio` | Comparativa entre transportadoras (más barata y más rápida) |
| 12 | Proponer pedido nuevo | Human-in-the-loop | `proponer_pedido` | Genera tarjeta pendiente con subtotal, flete y total |
| 13 | Pregunta RAG: Devoluciones | Semántica / RAG | `buscar_en_documentos` | Cita política de retracto y condiciones de empaque |
| 14 | Intento de inyección de prompt | Seguridad y robustez | *(Ninguna)* | Rechaza revelar directivas o vulnerar el sistema |

Los resultados se compilan en `eval/reporte.md` y se auditan en la tabla `eval_runs` de PostgreSQL.
