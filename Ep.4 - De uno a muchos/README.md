# Code Copilot Agent — Episodio 4: De uno a muchos, equipo de agentes

Un copiloto de código con IA que ingiere un repositorio (URL de GitHub o ruta
local), evalúa su calidad, explica qué hace, encuentra vulnerabilidades de
seguridad y recomienda mejoras priorizadas. Construido con el **Strands Agents
SDK** y **Amazon Bedrock (Amazon Nova Pro)**.

> **Este episodio** parte de una copia funcional del Ep.3 y divide el agente de
> nueve tools en un **equipo de agentes especialistas** coordinados con un
> **Graph** de Strands. Nada del Ep.1, Ep.2 ni Ep.3 se rompe: el agente único,
> el modo offline y la API siguen igual.

📝 Este episodio no tiene live: se cuenta en un artículo. Los episodios con video están en el canal [Soy Hazel Sáenz](https://www.youtube.com/@hazelsaenzG).

## Qué agrega el Episodio 4

1. **Cuatro agentes con una sola responsabilidad cada uno** (`team.py`):

   | Nodo | Agente | Tools |
   | --- | --- | --- |
   | `ingest` | `ingestor` | `ingest_repository` |
   | `security` | `security_analyst` | `scan_sast`, `scan_dependencies`, `scan_secrets` |
   | `review` | `code_reviewer` | `analyze_structure`, `assess_quality` |
   | `docs` | `doc_writer` | `explain_codebase`, `recommend_improvements`, `generate_report` |

   Cada uno tiene un system prompt corto y solo ve sus tools.

2. **Coordinación con Graph y una etapa paralela:**

   ```
   ingest ──► security ──┐
          └─► review   ──┴─► docs
   ```

   `security` y `review` no dependen entre sí, así que corren en el mismo
   batch. `docs` corre una sola vez, cuando los dos terminan.

3. **Estado compartido en dos canales:**
   - Las **aristas del graph** llevan texto: cada nodo recibe la tarea original
     más la salida de los nodos de los que depende (el `repo_path` y los
     resúmenes de los especialistas).
   - Los **resultados grandes** (hallazgos, health score, árbol de archivos)
     siguen en el store en memoria (`state.py`) por `repo_path`, igual que en
     el Ep.1. El modelo nunca los copia de un agente a otro.

4. **Manejo de fallos:** en el SDK de Python un nodo que falla lanza una
   excepción (fail-fast) y el graph se detiene, cortando también a su hermano
   del mismo batch. `runTeam` atrapa la excepción, lee `graph.state` para saber
   qué nodos completaron y cuáles fallaron, y genera un **reporte parcial
   determinista** (sin LLM) con lo que sí quedó en el estado compartido.
   Además hay límites, que en Python se comportan distinto:
   `set_node_timeout(300)` corta al nodo a tiempo y **lanza** una excepción;
   `set_execution_timeout(900)` se revisa antes de cada batch y **devuelve**
   un resultado `FAILED` sin lanzar. `runTeam` trata los dos casos igual: marca
   la corrida como fallida y escribe el reporte parcial, que dice qué nodo
   falló o qué nodos no llegaron a correr. Los errores nunca se rutean a otro
   nodo: las aristas condicionales rutean sobre resultados que el nodo devuelve.

   Un detalle importante: una tool que falla **no** tumba el nodo. Las tools
   del Ep.1 ya devuelven un `limitation` (por ejemplo, Semgrep no instalado) y
   el agente sigue. Lo que tumba un nodo es una falla del agente completo:
   la llamada al modelo, permisos de Bedrock o un timeout.

### ¿Por qué Graph y no agents-as-tools o swarm?

| Patrón | Cuándo usarlo | ¿Aquí? |
| --- | --- | --- |
| Agents-as-tools | Un orquestador decide en cada request a qué especialista llamar. | No: el flujo no cambia entre requests. |
| Graph | El flujo se puede dibujar antes de ejecutar y hay pasos paralelos. | **Sí:** ingest, dos análisis en paralelo, reporte. |
| Swarm | El siguiente paso depende de lo que encontró el anterior. | No: ningún especialista necesita pasarle el control a otro. |

### Cómo correr el equipo

```bash
source .venv/bin/activate
code-copilot /ruta/al/repo --team --report reports/x.md
```

Si un especialista falla, el CLI imprime qué nodos completaron, cuál falló y
dónde quedó el reporte parcial, y sale con código `1`.

## Heredado del Episodio 3

1. **RAG de código (solo retrieval):** indexamos el codebase completo en un
   índice vectorial local (FAISS) y lo consultamos por relevancia con una tool
   nueva, `search_code`. **LangChain entra exclusivamente como capa de
   retrieval.**
2. **Memoria nativa del SDK Strands sobre Supabase:** al pasar a servicio,
   cambiamos la interfaz propia `MemoryStore` del Ep.2 por la nativa de Strands,
   extendiendo `SessionRepository` para enchufar **Supabase** (PostgreSQL
   gestionado, concurrente, compartido entre instancias).
3. **Servicio HTTP con streaming:** exponemos el copiloto con **FastAPI**. Un
   `POST /index` procesa un repo nuevo (ruta local o URL de GitHub) y un
   `POST /chat` responde en streaming, usando el `session_manager` nativo por
   `session_id`, con timeout y manejo de errores. Todo por HTTP, sin CLI.

## La tesis: Strands orquesta, LangChain recupera

Hay **una sola capa de orquestación, y es Strands.** No hay agente de LangChain,
ni chains de orquestación, ni agent executors de LangChain. LangChain solo
indexa y devuelve fragmentos. La flecha va en un solo sentido: el agente Strands
*usa* el retriever como una tool más (`search_code`). El agente decide *cuándo*
buscar; LangChain solo responde *qué* devuelve.

El retriever vive **detrás de la interfaz de `search_code`**, así que se puede
cambiar LangChain por LlamaIndex mañana sin tocar el agente.

```
Agente Strands (única orquestación)
  └─ tools: ingest, structure, sast, deps, secrets, quality,
            explain, recommend, report,
            search_code  ──►  retriever (LangChain)  ──►  índice FAISS local
                              (solo retrieval: indexar y devolver fragmentos)
```

## RAG ≠ memoria (dos stores, dos propósitos)

Son dos almacenes distintos, con ciclos de vida distintos. **No se mezclan.**

| | Memoria (Ep.2/Ep.3) | RAG (Ep.3) |
| --- | --- | --- |
| Qué guarda | La **conversación** por `session_id` | **Conocimiento del repo** (fragmentos de código) |
| Backend | `MemoryStore` (CLI) / `SessionRepository` sobre Supabase (servicio) | Índice vectorial FAISS local |
| Ciclo de vida | Por sesión / usuario | Por repositorio indexado |
| Módulo | `memory/` | `knowledge.py` + `tools/search.py` |

## RAG: indexar y consultar el repo

El módulo `src/code_copilot/knowledge.py` tiene una responsabilidad única
(indexar y recuperar), separado del wiring del agente. El flujo:

1. Recorre el codebase **reutilizando los helpers del Ep.1** (`iterSourceFiles`,
   `readTextSafely`, `EXTENSION_LANGUAGE_MAP`). No reimplementa el walking.
2. Carga cada archivo en un `Document` de LangChain con metadatos obligatorios:
   `path` (ruta relativa) y `language`, para poder **citar la fuente**.
3. Aplica **chunking de código** (ver tabla abajo).
4. Genera embeddings con Bedrock (`amazon.titan-embed-text-v2:0`). Seguimos en
   Bedrock, no sumamos un proveedor nuevo.
5. Construye un índice **local** con FAISS (`FAISS.from_documents`) y lo
   persiste/carga desde disco (configurable), en la filosofía del `JsonFileStore`
   del Ep.2: simple, inspeccionable, cero infraestructura.
6. Expone el índice como retriever con `index.as_retriever()`.

La tool `search_code(query, k=5)` consulta el retriever con la API vigente
(`retriever.invoke(query)`, **no** `get_relevant_documents`, que está deprecado)
y devuelve los top-k fragmentos, cada uno con su `path` (y `symbol` si existe).
El retriever se inyecta una sola vez al arrancar con `set_retriever(...)`.

### Tabla de chunking: por archivo vs. por símbolo

| Estrategia | Precisión | Contexto de módulo | Parsing | Cuándo usarla |
| --- | --- | --- | --- | --- |
| **Por archivo** (`file`) | Media | Alto (preserva el módulo) | Ninguno (robusto) | Default y fallback; codebases mixtos o sin gramática tree-sitter |
| **Por símbolo** (`symbol`) | Alta (función/clase) | Bajo (fragmenta el módulo) | tree-sitter (Ep.1) | Cuando quieres precisar la función/clase exacta y citarla por `symbol` |

`file` usa `RecursiveCharacterTextSplitter` con `chunk_size`/`chunk_overlap`
configurables. `symbol` reutiliza el parsing de tree-sitter del Ep.1 para partir
por función/clase; cada chunk lleva su `symbol` en la metadata. Si un archivo no
tiene símbolos parseables, **cae a chunking por archivo** (degradación elegante,
como el Ep.1).

## Migración de memoria: de la interfaz propia al `SessionRepository` nativo

En el Ep.2 la memoria vivía detrás de una interfaz propia (`MemoryStore` /
`JsonFileStore`), **a propósito**, para *ver* el mecanismo. Ahora que el copiloto
pasa a ser un servicio con usuarios, cumplimos lo prometido: el servicio real usa
el **contrato del SDK**.

Strands separa las dos responsabilidades de memoria:

- el **session manager** persiste (`FileSessionManager`, `S3SessionManager`, y
  aquí `RepositorySessionManager` + `SupabaseSessionRepository`);
- el **conversation manager** poda (`SlidingWindowConversationManager`,
  `SummarizingConversationManager`).

`src/code_copilot/memory/supabase_repository.py` define
`SupabaseSessionRepository(SessionRepository)` con los **nueve métodos completos
y ejecutables** (`create_session`, `read_session`, `create_agent`, `read_agent`,
`update_agent`, `create_message`, `read_message`, `update_message`,
`list_messages`). Recibe un `supabase.Client` (creado con
`create_client(SUPABASE_URL, SUPABASE_KEY)`) y serializa los objetos del SDK con
sus `to_dict()`/`from_dict()`; no inventamos un formato propio.

Se enchufa al agente con
`RepositorySessionManager(session_id=..., session_repository=...)` pasado como
`session_manager=` al `Agent`. Con eso el agente carga y guarda solo: se elimina
el `loadHistory`/`appendExchange` manual del Ep.2, y la poda pasa al
`conversation_manager` nativo. Implementación open source de referencia:
[`supabase-strands`](https://github.com/supabase-community).

### Esquema de tablas (Supabase / PostgreSQL)

El SQL completo está en `src/code_copilot/memory/schema.sql`:

```sql
create table sessions (
    session_id text primary key,
    data       jsonb not null           -- { "session": {...}, "agents": {...} }
);

create table messages (
    session_id text    not null,
    agent_id   text    not null,
    message_id integer not null,
    data       jsonb   not null,         -- SessionMessage serializado
    primary key (session_id, agent_id, message_id)
);
```

Los `SessionAgent` viven dentro de `sessions.data['agents']` (una fila por
sesión). Los objetos del SDK se guardan serializados en las columnas `jsonb`.

### Tabla de decisión: interfaz propia vs. `SessionRepository`

| | Interfaz propia (`MemoryStore`, Ep.2) | `SessionRepository` nativo (Ep.3) |
| --- | --- | --- |
| Propósito | Aprender, controlar tu propio límite | Servicio real |
| Ciclo de vida | Lo defines tú | Lo hereda del framework |
| Poda | Función `prune` casera | `conversation_manager` nativo |
| Persistencia | `load`/`save` manuales | El agente carga/guarda solo |
| Concurrencia | Baja (JSON, un proceso) | Alta (Supabase/PostgreSQL, multi-instancia) |
| Cuándo | CLI / educativo | HTTP con usuarios |

El `MemoryStore` del Ep.2 sigue disponible para el **modo CLI/educativo**; el
**servicio HTTP** usa el `SessionRepository` nativo sobre Supabase.

## Servicio HTTP con FastAPI (streaming)

`src/code_copilot/api.py` expone `POST /chat` que recibe `{session_id, message}`
(modelo Pydantic `ChatRequest`) y responde en **streaming** con
`StreamingResponse` (`media_type="text/event-stream"`), usando
`agent.stream_async(message)`. El agente se construye con `callback_handler=None`
(el callback de terminal y `stream_async` consumen los mismos eventos; uno para
la terminal, otro para la red). Solo se emiten los chunks de texto: cuando el
evento trae la llave `data`, se hace `yield event["data"]`.

La memoria es la **nativa** (Supabase): el agente restaura el historial por
`session_id` y persiste cada mensaje solo; el endpoint no maneja memoria a mano.
El `session_id` se sanitiza con el helper existente.

### Errores y timeouts (que un agente no cuelgue la API)

- **Timeout** del stream con `asyncio.timeout(STREAM_TIMEOUT_SECONDS)`
  (configurable). Nota: `asyncio.timeout()` es Python 3.11+, que es lo que exige
  el proyecto.
- **Errores que no rompen el stream:** si ya empezaron a fluir tokens (200 en
  curso), no se lanza un 500 seco. Se captura el fallo, se emite un evento de
  error **dentro** del stream (`\n[error] ...`) y se cierra limpio. El mensaje
  dice el **tipo** de fallo, nunca el valor de un secreto ni el stack completo
  (misma disciplina del Ep.1).
- No hace falta persistir a mano en un `finally`: el `session_manager` nativo
  guarda cada mensaje por su cuenta.

Es el mismo principio de **degradación elegante** del Ep.1 (scanners ausentes),
ahora en la capa HTTP.

## Endpoints de la API

Todo el flujo (procesar un repo y preguntar sobre el código) se hace por HTTP,
sin ejecutar nada en el CLI.

| Método / ruta | Qué hace |
| --- | --- |
| `GET /health` | Liveness probe. Devuelve `{"status":"ok"}`. |
| `POST /index` | Indexa un repo (**ruta local o URL de GitHub**) en el índice FAISS, lo persiste, deja el retriever listo para `search_code`, y devuelve un `analysis` de lo que se procesó (lenguajes, archivos, chunking, etc.). |
| `POST /analyze` | Corre la **revisión estática completa** del Ep.1 (structure, SAST, dependencias, secretos, calidad) y devuelve las métricas + un reporte Markdown. Más pesado que `/index`. |
| `POST /chat` | Chat en streaming sobre el repo indexado, con memoria nativa por `session_id`. |

> **`/index` vs. `/analyze`:** `/index` es ligero y sin LLM; construye el índice
> vectorial para poder *preguntar* por el código (RAG). `/analyze` es la
> auditoría de calidad/seguridad del Ep.1 (más costosa). Son independientes:
> puedes indexar sin analizar, o analizar sin indexar.

## Cómo levantar la API (flujo completo, sin CLI)

### 1. Configura el entorno

La API **carga `.env` al arrancar** (las variables ya presentes en el entorno
tienen prioridad). Copia `.env.example` a `.env` y complétalo, o exporta las
variables. Nunca hardcodees la key en el código:

```bash
# .env (o export)
AWS_REGION=us-east-2
AWS_PROFILE=my-Free-tier
SUPABASE_URL=https://<tu-proyecto>.supabase.co
SUPABASE_KEY=<tu-api-key>          # solo en el entorno, jamás en el código
RAG_INDEX_DIR=.code_copilot/index
RAG_CHUNK_STRATEGY=symbol          # o file
STREAM_TIMEOUT_SECONDS=60
```

> **Nota sobre la key de Supabase:** para un servicio backend que escribe en las
> tablas, usa la **`service_role` key** (no la `publishable`), sobre todo si las
> tablas tienen Row Level Security activo. Guárdala solo en `.env`.

### 2. Crea el esquema en Supabase (una sola vez)

`POST /chat` usa la memoria nativa sobre Supabase, así que las tablas deben
existir. Abre el **SQL Editor** de tu proyecto Supabase y ejecuta el contenido
de `src/code_copilot/memory/schema.sql` (tablas `sessions` y `messages`). Si no
lo haces, `/chat` responde `503` con
`Could not find the table 'public.sessions'`.

### 3. Levanta el servidor

```bash
# Desde la carpeta del Ep.3, con el venv activado (ver Instalación)
uvicorn code_copilot.api:app --reload
```

Al arrancar, si ya existe un índice persistido en `RAG_INDEX_DIR`, la API lo
carga y lo enchufa a `search_code` automáticamente (no hace falta reindexar tras
reiniciar).

### 4. Procesa un repositorio nuevo (`POST /index`)

`repo` puede ser una **ruta local** o una **URL de GitHub** (las URLs se clonan
en modo shallow, sin ejecutar código del repo):

```bash
curl -s -X POST http://127.0.0.1:8000/index \
  -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/tu-usuario/tu-repo"}'
```

La respuesta incluye un **`analysis`** con lo que se procesó del repo (sin LLM,
determinista): fuente, ruta local resuelta del clon, lenguajes y conteo de
archivos, tamaño total, gestores de paquetes, árbol de directorios, la
estrategia de chunking usada y las estadísticas de chunks:

```json
{
  "status": "indexed",
  "repo": "https://github.com/tu-usuario/tu-repo",
  "analysis": {
    "source": "https://github.com/tu-usuario/tu-repo",
    "resolved_path": ".copilot_workdir/repo_xxxx",
    "chunk_strategy": "file",
    "chunk_size": 1000,
    "chunk_overlap": 150,
    "embeddings_model": "amazon.titan-embed-text-v2:0",
    "index_dir": ".code_copilot/index",
    "file_count": 12,
    "total_size_bytes": 84210,
    "languages": {"JavaScript": 8, "Python": 4},
    "package_managers": {"package.json": "npm"},
    "directory_tree": "...",
    "chunk_count": 87,
    "chunks_with_symbol": 0,
    "top_chunked_files": [{"path": "app.js", "chunks": 87}]
  }
}
```

Errores del indexado: `400` si el repo no tiene archivos de código indexables;
`502` si el indexado falla (solo se reporta el tipo del fallo, nunca un stack).

### 4b. (Opcional) Revisión estática completa (`POST /analyze`)

Corre la auditoría del Ep.1 sobre el repo (independiente del índice de RAG):

```bash
curl -s -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/tu-usuario/tu-repo"}'
```

Devuelve un `summary` con las métricas clave (health score, hallazgos SAST,
vulnerabilidades de dependencias, secretos potenciales, ruta del reporte) más
las secciones `structure`, `sast`, `dependencies`, `secrets` y `quality`:

```json
{
  "status": "analyzed",
  "repo": "https://github.com/tu-usuario/tu-repo",
  "summary": {
    "health_score": 72,
    "sast_findings": 0,
    "dependency_vulnerabilities": 0,
    "potential_secrets": 0,
    "report_written_to": null
  }
}
```

Pasa `"report_path": "reports/x.md"` en el cuerpo para escribir el reporte
Markdown a disco. Errores: `400` si el repo no se puede clonar/encontrar; `502`
si el pipeline falla (solo el tipo del fallo, nunca un stack).

### 5. Pregunta sobre el código (`POST /chat`, streaming)

```bash
curl -N -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "proyecto-x", "message": "where is auth handled? cite the file"}'
```

`-N` desactiva el buffering de curl para ver el streaming token a token. Reusa el
mismo `session_id` entre llamadas para que el agente recupere el hilo (memoria
nativa por sesión sobre Supabase).

> El agente decide cuándo llamar a `search_code`. Si preguntas por `/chat` sin
> haber indexado un repo antes (ni tener un índice persistido en
> `RAG_INDEX_DIR`), la búsqueda responde que no hay índice configurado; primero
> llama a `POST /index`.

## Capacidades (heredadas del Ep.1/Ep.2)

El agente expone ahora **diez** herramientas: las nueve del Ep.1 más
`search_code` (RAG). Flujo de orquestación del análisis:

```
ingest_repository -> analyze_structure
  -> [scan_sast, scan_dependencies, scan_secrets, assess_quality]
  -> explain_codebase -> recommend_improvements -> generate_report
```

`search_code` es una tool bajo demanda: el agente la invoca cuando necesita
ubicar algo en el repo (p. ej. "where is auth handled?").

## Requisitos

- Python 3.11+ (el proyecto usa `asyncio.timeout`, que es 3.11+).
- Credenciales de AWS con acceso a Amazon Bedrock (modelo Nova Pro para el chat
  y Titan Embeddings para el RAG).
- Un proyecto de Supabase para la memoria del servicio (con el `schema.sql`
  aplicado).
- `faiss-cpu` (no GPU) es suficiente para indexar un repo local.

## Instalación

Este episodio usa un **entorno virtual propio** (uno por episodio):

```bash
# Desde la carpeta del Ep.4
python3 -m venv .venv
source .venv/bin/activate

pip install -e .
# Para desarrollo (tests):
pip install -e ".[dev]"
```

## Configuración

Copia `.env.example` y ajústalo. Variables del Ep.3 (todas configurables, nada
hardcodeado):

| Variable | Default | Descripción |
| --- | --- | --- |
| `RAG_CHUNK_STRATEGY` | `file` | Estrategia de chunking: `file` o `symbol`. |
| `RAG_CHUNK_SIZE` | `1000` | Tamaño de chunk (caracteres) para el splitter por archivo. |
| `RAG_CHUNK_OVERLAP` | `150` | Solapamiento entre chunks (caracteres). |
| `RAG_INDEX_DIR` | `~/.code_copilot/index` | Ruta de persistencia del índice FAISS. |
| `EMBEDDINGS_MODEL_ID` | `amazon.titan-embed-text-v2:0` | Modelo de embeddings de Bedrock. |
| `SUPABASE_URL` | — | URL del proyecto Supabase (memoria del servicio). |
| `SUPABASE_KEY` | — | API key de Supabase. **Nunca hardcodear.** |
| `CONVERSATION_MANAGER` | `sliding_window` | Poda nativa: `sliding_window` o `summarizing`. |
| `CONVERSATION_WINDOW_SIZE` | `40` | Tamaño de ventana para `sliding_window`. |
| `STREAM_TIMEOUT_SECONDS` | `60` | Timeout por request del endpoint de streaming. |

Las variables del Ep.1 (Bedrock: `AWS_REGION`, `AWS_PROFILE`,
`BEDROCK_MODEL_ID`) y del Ep.2 (memoria del CLI: `MEMORY_*`) siguen vigentes.

## Uso (CLI, heredado)

El CLI del Ep.1/Ep.2 sigue funcionando igual (memoria propia + modo offline):

```bash
source .venv/bin/activate

# Ep.4: equipo de agentes (Graph)
code-copilot /ruta/al/repo --team --report reports/x.md

# Modo agente único (con LLM, requiere Bedrock)
code-copilot /ruta/al/repo --session-id proyecto-x --report reports/x.md

# Modo offline (pipeline estático determinista, sin AWS)
code-copilot /ruta/al/repo --offline --report reports/x.md
```

## Modelo de seguridad

- El contenido del repositorio se trata como **no confiable**: el agente nunca
  ejecuta código del repo; solo lo lee y analiza (también al indexarlo para RAG).
- Los valores de secretos nunca se imprimen ni se registran.
- El endpoint nunca filtra el valor de un secreto ni el stack completo en un
  error del stream: solo el tipo del fallo.
- `SUPABASE_KEY` va por configuración/entorno, jamás en el código.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

Los tests son **deterministas y offline**: usan un doble en memoria del cliente
de Supabase, embeddings de hashing reproducibles y un agente falso para el
endpoint. No pegan a Supabase, Bedrock ni la red.

- **Memoria nativa (round-trip):** `tests/test_memory_supabase.py` ejercita los
  nueve métodos y verifica que un repositorio nuevo recupere el historial por
  `session_id`.
- **Retrieval (recall@k) y chunking:** `tests/test_knowledge.py` verifica que
  `search_code` traiga el `path`/`symbol` correcto en el top-k y compara recall
  entre chunking por archivo y por símbolo.
- **Equipo de agentes (Ep.4):** `tests/test_team.py` corre el graph completo
  con un modelo guionado (`ScriptedModel` en `fakes.py`) que hace llamadas
  reales a las tools: verifica que cada agente solo vea sus tools, que
  `security` y `review` corran en paralelo antes de `docs`, y que un
  especialista caído, un timeout de nodo o el timeout global detengan el
  graph y dejen un reporte parcial.
- **Endpoints:** `tests/test_api.py` verifica `POST /index` (inyecta el
  retriever; `400` cuando no hay archivos indexables) y `POST /chat` (streaming,
  timeout y degradación ante una tool caída: evento de error en el stream, no un
  500 a mitad de tokens).

## Estructura del proyecto

```
src/code_copilot/
  team.py           # NUEVO Ep.4: especialistas + Graph + runTeam (fallos y reporte parcial)
  api.py            # NUEVO Ep.3: FastAPI POST /index + /analyze + /chat (streaming)
  knowledge.py      # NUEVO Ep.3: ingesta, chunking, embeddings Bedrock, índice FAISS, retriever
  agent.py          # + buildServiceAgent (memoria nativa, callback_handler=None)
  config.py         # + RagConfig / ServiceConfig (RAG, Supabase, timeout)
  memory/           # Ep.2 MemoryStore (__init__.py) + NUEVO Ep.3:
    __init__.py     #   MemoryStore/JsonFileStore/SessionManager/prune (CLI/educativo)
    supabase_repository.py  # SupabaseSessionRepository(SessionRepository), 9 métodos
    native.py       #   wiring: cliente Supabase + RepositorySessionManager + conv. manager
    schema.sql      #   tablas sessions y messages
  tools/
    search.py       # NUEVO Ep.3: search_code (RAG), registrada en ALL_TOOLS
    ...             # las nueve tools del Ep.1
  ...               # model.py, state.py, pipeline.py, utils.py, cli.py, callback.py, colors.py
tests/              # + test_team.py (Ep.4), test_knowledge.py, test_search_tool.py, test_memory_supabase.py, test_api.py, fakes.py
```

## Alcance (scope guard)

- Una sola capa de orquestación: **Strands**. LangChain **solo** retrieval,
  detrás de `search_code`. No hay agente/chains de LangChain.
- La memoria del servicio es **nativa del SDK** sobre Supabase, no una interfaz
  propia nueva.
- El **multi-agente** vive en el CLI (`--team`). La API del Ep.3 sigue usando
  el agente único; los **guardrails** llegan en la **Semana 5** y el **deploy
  gestionado** en la **Semana 8**.
