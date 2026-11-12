# Agente de Raíz Viva Naturales

Proyecto académico para la clase de Agentes de IA de UniPutumayo: un asistente local por voz y texto para administrar **Raíz Viva Naturales**, una tienda ficticia colombiana. Consulta productos, pedidos, inventario, novedades y políticas; prepara acciones que el usuario revisa y confirma. Los montos se presentan en COP, con punto de miles y sin decimales.

En `COPILOT_MODE=demo`, la aplicación funciona con datos ficticios en el esquema `tienda` y conocimiento de `knowledge_demo/`. Confirmar una acción **sí escribe en la base demo**; crear pedidos puede descontar inventario. La demostración no requiere una cuenta, base ni credenciales de Atlas. La integración histórica `COPILOT_MODE=atlas` es opcional y queda fuera de esta instalación.

## Tecnologías y versiones

Tabla reproducida de [docs/presentacion.html](docs/presentacion.html). Describe las versiones documentadas para la presentación; `requirements.txt` usa intervalos y una instalación nueva puede resolver otras versiones compatibles.

| Componente | Tecnología | Versión | Rol |
|---|---|---|---|
| Voz a texto (STT) | faster-whisper (Whisper small, sobre CTranslate2 4.8.5) | 1.2.1 | Transcribe la voz en español |
| Modelo de lenguaje | Qwen3 4B Instruct (qwen3:4b-instruct) en Ollama | Ollama 0.40.12 | Razona y decide qué herramienta usar |
| Embeddings | EmbeddingGemma (embeddinggemma, 768 dim) | vía Ollama 0.40.12 | Convierte manuales y preguntas en vectores |
| Base transaccional | PostgreSQL | 16.15 | Productos, pedidos, novedades, órdenes de compra, tareas |
| Base vectorial | pgvector | 0.8.7 | Búsqueda semántica en la base de conocimiento |
| Texto a voz (TTS) | Piper TTS (voz es_MX-claude-high, ONNX Runtime 1.31.1) | 1.8.0 | Lee las respuestas en voz alta |
| Backend | FastAPI + Uvicorn (Python 3.13.2) | 0.162.0 / 0.54.0 | API y servicio del agente |
| Acceso a datos | psycopg | 3.3.8 | Conexión a PostgreSQL |
| Interfaz | HTML5, CSS3 y JavaScript | — | Chat por voz y texto, reportes y PDF |

## Requisitos

- Python **3.13**, Git y Docker Desktop en ejecución (contenedores Linux).
- Ollama con `qwen3:4b-instruct` y `embeddinggemma`.
- PostgreSQL **16** con pgvector **0.8.7**, en un contenedor exclusivo de la demo, puerto local **5434**.
- FFmpeg en el `PATH`; Chrome o Edge con permiso para usar el micrófono desde `localhost`.
- Voz Piper descargable `es_MX-claude-high` (modelo `.onnx` y configuración `.onnx.json`).
- Internet para descargar dependencias y modelos. GPU NVIDIA compatible con CTranslate2/CUDA si se utiliza aceleración; la transcripción también dispone de CPU.

## Instalación paso a paso (PowerShell)

### 1. Abrir la carpeta y preparar Python

Clona el repositorio publicado por el docente y abre su carpeta raíz. Si el repositorio contiene una subcarpeta `copiloto/`, entra en ella. Los siguientes comandos se ejecutan desde la carpeta que contiene `requirements.txt`, sin depender de una ruta absoluta.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

La plantilla contiene valores ficticios. Para esta instalación, `AGENT_DATABASE_URL` y `DEMO_DATABASE_URL` apuntan a la misma base local `copiloto_demo` en `127.0.0.1:5434`, con usuario `demo_user` y contraseña de ejemplo `demo_password`. El esquema del agente y `tienda` conviven allí. Conserva `COPILOT_MODE=demo` y usa esta base únicamente para datos de clase.

### 2. Preparar Ollama

Abre Ollama. Si se usa su CLI sin servicio activo, ejecuta `ollama serve` en otra terminal. Después descarga ambos modelos:

```powershell
ollama pull qwen3:4b-instruct
ollama pull embeddinggemma
ollama list
```

### 3. Crear la base demo

```powershell
docker run --name raiz-viva-postgres -e POSTGRES_USER=demo_user -e POSTGRES_PASSWORD=demo_password -e POSTGRES_DB=copiloto_demo -p 127.0.0.1:5434:5432 -v raiz-viva-pgdata:/var/lib/postgresql/data -d pgvector/pgvector:0.8.7-pg16
docker exec raiz-viva-postgres pg_isready -U demo_user -d copiloto_demo
```

Espera a que `pg_isready` indique que acepta conexiones. Si el contenedor ya existe, usa `docker start raiz-viva-postgres`. El volumen conserva sus datos entre reinicios; las variables de creación no cambian las credenciales de un volumen previamente inicializado.

### 4. Descargar la voz e inicializar los datos

```powershell
$env:COPILOT_MODE = 'demo'
python scripts/download_voice.py
python scripts/init_db.py
python scripts/seed_demo.py
python scripts/init_db.py
python scripts/seed_hasta_hoy.py
python scripts/ingest_conocimiento_tienda.py
```

El orden es intencional:

1. `init_db.py` aplica `sql/001_schema.sql` (incluye la extensión vector) y `002_conversations_history.sql`.
2. `seed_demo.py` aplica `sql/010_tienda_demo.sql` y carga productos, clientes, pedidos y tarifas ficticios. **Reinicia las tablas demo que enumera mediante `TRUNCATE`**: úsalo para la carga inicial o para reiniciar voluntariamente la práctica.
3. La segunda ejecución de `init_db.py`, con `tienda.productos` ya creada, aplica `sql/011_compras_tareas.sql` y `012_contacto_pedidos.sql`.
4. `seed_hasta_hoy.py` completa pedidos hasta la fecha actual, sin borrar pedidos existentes; exige `127.0.0.1:5434`. Puede rechazar una jornada que ya tiene más de cinco pedidos por método de pago.
5. `ingest_conocimiento_tienda.py` genera documentos de catálogo/tarifas desde la base demo e ingesta todo `knowledge_demo/` mediante embeddings en pgvector. Escribe su informe en `tmp/`. Como alternativa para cargar solamente los Markdown existentes: `python scripts/ingest.py --demo`.

Las credenciales y el nombre de base deben coincidir con `.env`. No ejecutes los scripts contra una base de otro proyecto. La voz se obtiene desde el repositorio público `rhasspy/piper-voices` en Hugging Face; sus archivos descargados se excluyen de Git. Whisper descarga su modelo `small` la primera vez que lo necesita.

### 5. Arrancar el agente

```powershell
$env:COPILOT_MODE = 'demo'
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

En una terminal POSIX, el equivalente es `COPILOT_MODE=demo uvicorn app.main:app --host 127.0.0.1 --port 8765`.

Abre **http://localhost:8765**. La barra de estado muestra LLM, embeddings, STT, TTS, base de tienda y base del agente. La primera respuesta puede tardar mientras se cargan los modelos.

## Uso y pruebas de escenarios

Escribe una pregunta o usa el micrófono. Prueba: «¿Qué productos tienes?», «¿Qué productos tienen stock bajo?» y «Crea un pedido de una unidad de té verde». Completa los datos ficticios solicitados, revisa cantidades, flete y total, y confirma solo si deseas guardar el pedido demo. El panel de trazabilidad muestra herramientas, fuentes y tiempos de respuesta.

Con el servidor demo y sus servicios activos, desde otra terminal con el entorno virtual activado:

```powershell
$env:COPILOT_MODE = 'demo'
python eval/run_eval.py --casos eval/casos_demo.yaml
python scripts/escenarios_alucinacion.py
python scripts/escenarios_conversacion.py
```

Las pruebas de escenarios consultan la base local y envían conversaciones al servidor; generan historial y pueden preparar borradores. Revisa los resultados de cada ejecución: los informes no son una garantía previa de aprobación. La evaluación escribe `eval/reporte.md` y los escenarios escriben JSON en `tmp/`, excluidos de publicación. Los teléfonos de los escenarios son marcadores ficticios y no deben contactarse.

## Variables de entorno

`.env.example` enumera todas las variables que consume el proyecto: `COPILOT_MODE`, `AGENT_DATABASE_URL`, `DEMO_DATABASE_URL`, `OLLAMA_URL`, `LLM_MODEL`, `EMBED_MODEL`, `PIPER_VOICE` y, solo para la integración opcional, `ATLAS_DATABASE_URL`, `ATLAS_STORE_ID`, `ATLAS_EVAL_ORDER`. Las variables de proceso tienen prioridad sobre `.env`.

`CHROME_BIN` es opcional para `scripts/verify_layout.js`: expórtala en la terminal con la ruta o nombre de un navegador Chromium. Este script auxiliar requiere Node.js con `fetch` y `WebSocket` globales, guarda capturas en `tmp/` y no interviene en el arranque del agente.

## Estructura de carpetas

```text
.env.example       Configuración ficticia publicable
.gitignore         Exclusiones de secretos, modelos y resultados
requirements.txt   Dependencias Python
app/               API, agente, herramientas, protección de datos, STT y TTS
  data/            Catálogo geográfico colombiano
web/               Interfaz HTML, CSS, JavaScript y recursos gráficos
sql/               Esquemas y funciones de las bases del agente y tienda
scripts/           Inicialización, semillas, ingesta y escenarios
knowledge_demo/    Políticas y conocimiento de la tienda ficticia
eval/              Casos y ejecutor de evaluación
tests/             Pruebas de catálogo, conversación y borradores
docs/              Presentación, ficha técnica, guion y diagramas
voices/            Voz Piper descargada (no versionada)
audio/             Audio generado (no versionado)
tmp/               Informes y capturas locales (no versionados)
```

## Solución de problemas

| Síntoma | Qué comprobar |
|---|---|
| Base de datos en rojo | Contenedor activo, `pg_isready`, puerto 5434 y coincidencia de variables con el contenedor |
| Tablas o funciones inexistentes | Haber seguido el orden de inicialización, semilla y segunda inicialización |
| LLM o embeddings en rojo | Ollama activo en el puerto 11434 y ambos modelos descargados |
| Fallo al ingestar | Catálogo y tarifas cargados, modelo de embeddings disponible y extensión vector creada |
| TTS en rojo | Ejecutar la descarga de voz y comprobar que `PIPER_VOICE` apunta al `.onnx` |
| Micrófono sin permiso | Abrir desde `localhost`, permitir micrófono y comprobar FFmpeg |
| Error de aceleración STT | Revisar controladores y entorno CUDA de CTranslate2 o utilizar CPU |

Más material: [ficha técnica](docs/ficha-agente.md), [guion](docs/guion-demo.md), [diagramas](docs/diagramas.md) y [presentación](docs/presentacion.html).
