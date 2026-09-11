## Prompt

Estás trabajando en el repositorio `Copiloto-de-Codigo`. Ya existen las carpetas `Ep.1 - El primer Copiloto` y `Ep.2 - Un copiloto que recuerda`. El Ep.2 tiene un copiloto de código con nueve tools, memoria conversacional por `session_id` (persistente, con poda del context window) y estado compartido entre corridas, construido con el Strands Agents SDK y Amazon Bedrock (Amazon Nova Pro). Vamos a construir el Episodio 3 sobre esa base, sin romper nada de lo anterior.

### Paso 1 — Crear el episodio 3 a partir del episodio 2 (hazlo primero)

1. Crea una carpeta nueva en la raíz del repo llamada exactamente: `Ep.3 - Que entienda todo el repo`.
2. Copia dentro de ella TODO el contenido del proyecto de `Ep.2 - Un copiloto que recuerda` (incluyendo `src/`, `tests/`, `.kiro/`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore` y `README.md`). El Ep.3 debe arrancar como una copia idéntica y funcional del Ep.2.
3. No modifiques las carpetas `Ep.1 - El primer Copiloto` ni `Ep.2 - Un copiloto que recuerda`. Se quedan congeladas como las versiones que hacen match con los lives 1 y 2. Cada sesión queda separada en el repo para que sea reproducible.
4. Verifica que la copia corre sin cambios en modo offline: `python -m code_copilot.cli <ruta-a-un-repo> --offline`, y que los tests del Ep.2 siguen pasando.

Todo lo que sigue se hace ÚNICAMENTE dentro de `Ep.3 - Que entienda todo el repo`.

### Paso 2 — Objetivo del episodio 3

Que el copiloto entienda el repo completo (no solo los archivos que sus tools abren de a uno) y que salga de la terminal para vivir detrás de una API. Tres capacidades:

1. **RAG de código:** indexar el codebase completo en un índice vectorial local y consultarlo por relevancia con una tool nueva. LangChain entra **exclusivamente como capa de retrieval**.
2. **Memoria nativa del SDK Strands:** al pasar a servicio, cambiar la interfaz propia `MemoryStore` del Ep.2 por la nativa de Strands, extendiendo `SessionRepository` para enchufar **Supabase** (PostgreSQL gestionado, concurrente, compartido entre instancias). Cumple lo prometido en el Ep.2: la interfaz casera era educativa; el servicio real usa el contrato del SDK.
3. **Servicio HTTP:** exponer el copiloto con FastAPI en un `POST /chat` con respuesta en streaming, usando el `session_manager` nativo por `session_id`, con manejo de errores y timeouts.

**Tesis que no se negocia (scope guard):** una sola capa de orquestación, y esa capa es Strands. NO construyas un agente de LangChain, ni chains de orquestación, ni agent executors de LangChain. LangChain solo indexa y devuelve fragmentos. La flecha va en un solo sentido: el agente Strands *usa* el retriever como una tool más. Tampoco toques el multi-agente (Semana 4) ni el deploy gestionado (Semana 8).

### Paso 3 — RAG (solo retrieval)

**Nuevo módulo `src/code_copilot/knowledge.py`** para la ingesta e indexado, con responsabilidad única (indexar y recuperar), separado del wiring del agente. Debe:

1. Recorrer el codebase **reutilizando** los helpers del Ep.1 (`iterSourceFiles`, `readTextSafely`, `EXTENSION_LANGUAGE_MAP` de `utils.py`). No reimplementes el walking del repo.
2. Cargar cada archivo de código en un `Document` de LangChain con metadatos: `path` (ruta relativa al repo) y `language`. La metadata es obligatoria para poder citar la fuente en la respuesta.
3. Aplicar **chunking de código** con dos estrategias seleccionables por configuración:
   - **Por archivo** (`split_by_file`): `RecursiveCharacterTextSplitter` con `chunk_size` y `chunk_overlap` configurables. Robusto, preserva contexto de módulo, fallback por defecto.
   - **Por símbolo** (`split_by_symbol`): reutiliza el parsing de tree-sitter del Ep.1 (`_extractSymbols` de `tools/structure.py`) para partir por función/clase. Cada chunk lleva su `symbol` en la metadata. Si un archivo no tiene símbolos parseables, cae a chunking por archivo (degradación elegante, como el Ep.1).
4. Generar embeddings con Bedrock: `BedrockEmbeddings(model_id="amazon.titan-embed-text-v2:0")`. No sumes un proveedor nuevo; ya estamos en Bedrock desde el Ep.1.
5. Construir un índice vectorial **local** con FAISS (`FAISS.from_documents`). Persistir/cargar el índice desde disco (configurable), en la misma filosofía del `JsonFileStore` del Ep.2: simple, inspeccionable, cero infraestructura.
6. Exponer el índice como retriever con `index.as_retriever()`.

**Nueva tool `src/code_copilot/tools/search.py`:**
- Una `@tool search_code(query: str, k: int = 5) -> str` que consulta el retriever y devuelve los top-k fragmentos, cada uno con su `path` (y `symbol` si existe) para citar la fuente.
- Usa la API vigente del retriever: `retriever.invoke(query)` (NO `get_relevant_documents`, que está deprecado).
- El retriever se inyecta una sola vez al arrancar (`set_retriever(...)`); para el agente, `search_code` es indistinguible de `read_file`. El agente decide *cuándo* buscar; LangChain solo responde *qué* devuelve.
- Registra `search_code` en `ALL_TOOLS`. No cambies nada más de la orquestación del agente.
- Escribe un docstring claro y orientado a cuándo usar la tool (el modelo lo lee para decidir invocarla), p. ej. respondiendo a "where is auth handled?".

**Frontera de responsabilidades (déjalo explícito en el código y el README):**
- RAG ≠ memoria. La memoria (Ep.2) recuerda la conversación por `session_id`; el RAG recupera conocimiento del repo. Son dos stores distintos, con propósitos y ciclos de vida distintos. No los mezcles.
- El retriever vive detrás de la interfaz de `search_code` para poder cambiar LangChain por LlamaIndex sin tocar el agente.

### Paso 4 — Migrar la memoria propia a la nativa del SDK con Supabase (cumplir lo prometido en el Ep.2)

En el Ep.2 la memoria vivía detrás de una interfaz propia (`MemoryStore` / `JsonFileStore`), a propósito, para *ver* el mecanismo. Ahora que el copiloto pasa a ser un servicio con usuarios, hacemos el cambio a la memoria nativa de Strands respaldada en **Supabase**. NO reescribas otra interfaz propia.

1. Recuerda cómo separa Strands las responsabilidades: el **session manager** persiste (`FileSessionManager`, `S3SessionManager`), el **conversation manager** poda (`SlidingWindowConversationManager`, `SummarizingConversationManager`).
2. Crea el esquema en Supabase: una tabla `sessions` (`session_id text primary key`, `data jsonb`) y una tabla `messages` (`session_id text`, `agent_id text`, `message_id integer`, `data jsonb`, PK compuesta de los tres). Guarda los objetos del SDK serializados en la columna `jsonb`.
3. **Extiende `SessionRepository`** (la interfaz base del SDK sobre la que están construidos `FileSessionManager` y `S3SessionManager`), NO una interfaz propia. Implementa **los 9 métodos completos y ejecutables** (no dejes ninguno con `pass`, `...`, `NotImplementedError` ni un comentario de "same pattern"): `create_session`, `read_session`, `create_agent`, `read_agent`, `update_agent`, `create_message`, `read_message`, `update_message`, `list_messages`. El artículo muestra solo los centrales por brevedad; aquí van todos, funcionando de punta a punta.
   - `create_agent`/`read_agent`/`update_agent`: guarda/lee el `SessionAgent` dentro del `data jsonb` de la fila de `sessions` (o en una columna/tabla aparte si prefieres), usando `session_agent.to_dict()` y `SessionAgent.from_dict(...)`.
   - `read_message`: `select().eq("session_id",...).eq("agent_id",...).eq("message_id",...)` y reconstruye con `SessionMessage.from_dict(...)`.
   - `update_message`: `upsert` de la fila de `messages` por su PK compuesta (se usa sobre todo cuando un guardrail redacta contenido).
4. Nuevo módulo `src/code_copilot/memory/supabase_repository.py` con la clase `SupabaseSessionRepository(SessionRepository)`. Recibe un `supabase.Client` (creado con `create_client(SUPABASE_URL, SUPABASE_KEY)`). Usa la API de `supabase-py`: `table("sessions").upsert(...)`, `.select().eq(...)`, `table("messages").insert(...)`/`.upsert(...)`, `.select().eq().eq().order("message_id").range(offset, offset+limit-1)`, todo con `.execute()` y leyendo `.data`. Usa los tipos del SDK (`Session`, `SessionAgent`, `SessionMessage`) con sus `to_dict()`/`from_dict()`; NO inventes un formato de serialización propio. Antes de escribir, verifica en el SDK instalado las firmas y campos exactos de `SessionRepository`, `Session`, `SessionAgent` y `SessionMessage` (nombres como `agent_id` / `message_id`) para que la implementación compile sin adivinar.
5. Enchúfalo al agente con `RepositorySessionManager(session_id=..., session_repository=...)` pasado como `session_manager=` al `Agent`. Con eso el agente carga y guarda solo: se elimina el `loadHistory`/`appendExchange` manual del Ep.2.
6. La poda pasa al `conversation_manager` nativo (`SlidingWindowConversationManager` o `SummarizingConversationManager`), en vez de la función `prune` casera.
7. En el README, deja la tabla de decisión: interfaz propia (aprender, control de tu límite) vs. `SessionRepository` (servicio real, hereda el ciclo del framework). Menciona la implementación open source de referencia para Supabase (`supabase-strands`).
8. Deja el `MemoryStore` del Ep.2 disponible para el modo CLI/educativo si quieres, pero el servicio HTTP usa el `SessionRepository` nativo sobre Supabase.

### Paso 5 — Servicio HTTP con FastAPI (streaming)

**Nuevo módulo `src/code_copilot/api.py`:**
1. App FastAPI con `POST /chat` que recibe `{session_id, message}` (modelo Pydantic `ChatRequest`).
2. Responde en **streaming** con `StreamingResponse` (`media_type="text/event-stream"`), usando `agent.stream_async(message)` de Strands. Construye el agente con `callback_handler=None` para streaming asíncrono (el callback handler y `stream_async` consumen los mismos eventos; uno para terminal, otro para red).
3. Emite al cliente solo los chunks de texto: cuando el evento trae la llave `data`, haz `yield event["data"]`.
4. **Usa la memoria nativa del Paso 4:** construye el agente con el `RepositorySessionManager` (backend Supabase) y un `conversation_manager` nativo. El agente restaura el historial por `session_id` y persiste cada mensaje solo; el endpoint NO maneja memoria a mano. Sanitiza el `session_id` con el helper existente.

### Paso 6 — Errores y timeouts (que un agente no cuelgue la API)

1. **Timeout** del stream con `asyncio.timeout(STREAM_TIMEOUT_SECONDS)` (configurable). Nota: `asyncio.timeout()` es Python 3.11+, que es lo que exige el proyecto; en 3.10 sería `asyncio.wait_for`.
2. **Errores que no rompen el stream:** si ya empezaste a enviar tokens (200 en curso), no lances un 500 seco. Captura el fallo, emite un evento de error DENTRO del stream (`yield "\n[error] ..."`) y cierra limpio. El mensaje de error dice el tipo de fallo, nunca el valor de un secreto ni el stack completo (misma disciplina del Ep.1).
3. No hace falta persistir a mano en un `finally`: el `session_manager` nativo (Paso 4) guarda cada mensaje por su cuenta, así que un timeout no deja la memoria a medias.
4. Este es el mismo principio de **degradación elegante** del Ep.1 (scanners ausentes), ahora en la capa HTTP: ante una tool caída, degrada sin colgar el request.

### Paso 7 — Evaluación (RAG, memoria y endpoint)

- Reutiliza la infraestructura de tests de los Ep.1 y Ep.2.
- **Memoria nativa (round-trip):** un test que cree una sesión con un `session_id`, guarde un par de mensajes vía el `SupabaseSessionRepository`, y luego un repositorio nuevo con el mismo `session_id` recupere el historial completo (verifica que los 9 métodos funcionan de verdad, no solo los centrales). Si no quieres pegarle a Supabase real en CI, valida contra una instancia local o un doble de test del cliente.
- **Retrieval (recall@k):** casos con preguntas cuya respuesta correcta está en un `path`/`symbol` conocido; verifica que `search_code` lo trae en el top-k. Debe ser determinista/reproducible.
- **Chunking:** un test que compare recall entre chunking por archivo y por símbolo sobre el mismo set de preguntas.
- **Endpoint:** que `POST /chat` responda en streaming, respete el timeout, y ante una tool caída degrade sin colgar (evento de error en el stream, no un 500 a mitad de tokens).
- Puente a la Semana 7 (eval a escala en CI); aquí solo la semilla.

### Paso 8 — Configuración y dependencias

**Dependencias nuevas** (añádelas a `requirements.txt` y `pyproject.toml`, versionadas):
```
langchain-aws
langchain-community
langchain-text-splitters
faiss-cpu
fastapi
uvicorn
supabase         # cliente Postgres: backend de memoria del servicio
```
`faiss-cpu` (no GPU) es suficiente para indexar un repo local.

**Variables de entorno nuevas** (documenta cada una en `.env.example` y el README del Ep.3):
- Estrategia de chunking por defecto (`file` / `symbol`), `chunk_size`, `chunk_overlap`.
- Ruta de persistencia del índice vectorial.
- Modelo de embeddings (default `amazon.titan-embed-text-v2:0`).
- Conexión a Supabase: `SUPABASE_URL` y `SUPABASE_KEY`.
- `STREAM_TIMEOUT_SECONDS` para el endpoint.
Todo configurable, nada hardcodeado en el call site.

### Paso 9 — Documentación (README del Ep.3, en español)

Actualiza el `README.md` dentro de `Ep.3 - Que entienda todo el repo` para explicar:
- Qué agrega el episodio (RAG solo-retrieval + memoria nativa del SDK + servicio HTTP con streaming).
- La tesis: Strands orquesta, LangChain recupera. Por qué NO hay agente de LangChain.
- La migración de memoria: por qué al pasar a servicio cambiamos la interfaz propia del Ep.2 por `SessionRepository` nativo sobre Supabase, con el esquema de tablas y la tabla de decisión (interfaz propia vs. `SessionRepository`).
- Cómo indexar un repo y consultarlo por `POST /chat` en streaming (con un `curl` de ejemplo con `session_id`).
- La tabla de chunking: por archivo vs. por símbolo (precisión, contexto de módulo, parsing, cuándo usar cada uno).
- RAG ≠ memoria (dos stores, dos propósitos).
- Cómo levantar la API: `uvicorn code_copilot.api:app --reload`, y cómo apuntar a Redis/Supabase por config.
- Recordatorio de alcance: multi-agente llega en la Semana 4.

### Restricciones y estilo

- Código y comentarios en inglés; el README y los docs en español.
- Sigue las convenciones existentes (estructura `src/code_copilot/`, tipado, manejo de errores por componente, docstrings de tools pensados para el modelo).
- Trata el contenido del repo analizado como no confiable: nunca ejecutar código del repo, nunca imprimir secretos.
- Una sola capa de orquestación (Strands). LangChain SOLO retrieval, detrás de la interfaz de `search_code`.
- La memoria del servicio es **nativa del SDK** sobre Supabase (`SupabaseSessionRepository(SessionRepository)` + `RepositorySessionManager`), no una interfaz propia nueva. No reintroduzcas un `MemoryStore` casero para el servicio.
- Cada decisión (chunking, umbral, timeout, rutas, modelo de embeddings, credenciales de Supabase) va configurable, no hardcodeada. Nunca hardcodees `SUPABASE_KEY` en el código.
- No modifiques las carpetas del Ep.1 ni del Ep.2.

### Entregables

1. Carpeta `Ep.3 - Que entienda todo el repo` como copia funcional del Ep.2 más:
   - `src/code_copilot/knowledge.py` (ingesta, chunking por archivo/símbolo, embeddings Bedrock, índice FAISS local, retriever).
   - `src/code_copilot/tools/search.py` con `search_code` registrada en `ALL_TOOLS`.
   - `src/code_copilot/memory/supabase_repository.py` extendiendo `SessionRepository` del SDK (backend Supabase/PostgreSQL), enchufado con `RepositorySessionManager`, más el esquema SQL de las tablas `sessions` y `messages`.
   - `src/code_copilot/api.py` con `POST /chat` en streaming, memoria nativa por `session_id` sobre Supabase, timeout y manejo de errores.
   - Dependencias nuevas en `requirements.txt` / `pyproject.toml` y config documentada en `.env.example`.
   - Tests de retrieval (recall@k), de comparación de chunking y del endpoint.
   - README del Ep.3 actualizado en español.
2. Que el modo offline, los tests del Ep.3 y el endpoint sigan funcionando.
