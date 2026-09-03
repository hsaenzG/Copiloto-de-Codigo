# Code Copilot Agent — Episodio 2: Un copiloto que recuerda

Un copiloto de código con IA que ingiere un repositorio (URL de GitHub o ruta
local), evalúa su calidad, explica qué hace, encuentra vulnerabilidades de
seguridad y recomienda mejoras priorizadas y accionables.

Construido con el **Strands Agents SDK** y **Amazon Bedrock (Amazon Nova Pro)**.
Corre en la terminal con **salida a color**: las respuestas en lenguaje natural
del agente aparecen en un color y cada herramienta en el suyo.

> **Este episodio** parte de una copia funcional del Ep.1 y le agrega **memoria
> conversacional**: memoria de sesión, persistencia entre ejecuciones y poda del
> context window. Todo lo demás del Ep.1 se conserva sin cambios.

## Qué agrega el Episodio 2

La novedad es darle **memoria** al copiloto como decisión de arquitectura, no
como un buffer de mensajes que crece sin control. Tres capacidades:

1. **Memoria de corto plazo (sesión):** el historial del hilo actual, que el
   agente recibe (podado a presupuesto) en cada turno.
2. **Memoria de largo plazo (persistente):** el historial sobrevive al cierre
   del proceso y se recupera por `session_id` entre ejecuciones.
3. **Poda del context window:** cuando el historial supera un umbral de tokens,
   se poda con una estrategia configurable (`truncate` o `summarize`), con
   conciencia de costo y latencia (se registra el efecto en tokens).

Todo esto vive en el módulo nuevo `src/code_copilot/memory.py`, con una
responsabilidad única (persistir, recuperar, podar), separado del wiring del
agente.

### Diseño de la capa de memoria

El punto clave es una **fachada mínima e intercambiable**: el resto de la app
solo depende de dos métodos, así se puede cambiar el backend (JSON hoy; SQLite,
DynamoDB o Redis mañana) sin tocar nada más.

```
MemoryStore (typing.Protocol)      <- la costura estable: load() / save()
  └─ JsonFileStore                 <- única implementación de este episodio
                                       (usa la serialización SessionMessage de Strands)

SessionManager                     <- orquesta el turno:
                                       load(session_id) -> prune -> (corre el agente)
                                       -> append -> save(session_id)

prune(messages, *, strategy, max_tokens, summarizer, ...)
  ├─ "truncate"   ventana deslizante, O(n), barata, pierde contexto viejo
  └─ "summarize"  resume lo viejo con Nova Pro, preserva contexto (1 llamada extra)

count_tokens(...)                  <- contador aproximado (~4 chars/token) para
                                       decidir cuándo podar y registrar el costo
```

Nos apoyamos en las **primitivas nativas de Strands** para no reinventar lo
frágil: `JsonFileStore` serializa con `strands.types.session.SessionMessage`
(mismo formato que usa el `FileSessionManager` del SDK), y la estrategia
`summarize` sigue el mismo enfoque que el `SummarizingConversationManager` de
Strands, invocando Nova Pro vía `model.runModelPrompt`.

El estado de análisis compartido (`state.py`) también se persiste junto con la
conversación, así sobrevive entre corridas sin duplicar la lógica de estado.

## Capacidades (heredadas del Ep.1)

El agente expone nueve herramientas:

| Herramienta | Propósito |
| --- | --- |
| `ingest_repository` | Clona (URL) o valida (local) un repo; devuelve lenguajes, archivos, tamaño, gestores de paquetes, archivos de config y árbol de directorios. |
| `analyze_structure` | Mapea módulos, símbolos, imports y puntos de entrada (tree-sitter, con fallback a regex). |
| `scan_sast` | Análisis estático de seguridad con Semgrep (con heurísticas propias como fallback). |
| `scan_dependencies` | Análisis de composición de software (SCA) con la API gratuita de OSV.dev. |
| `scan_secrets` | Detecta secretos hardcodeados; los valores siempre se redactan. |
| `assess_quality` | Heurísticas de mantenibilidad + un health score de 0 a 100. |
| `explain_codebase` | Explicación de arquitectura y flujos con el LLM. |
| `recommend_improvements` | Recomendaciones priorizadas y accionables con el LLM. |
| `generate_report` | Ensambla el reporte final en Markdown. |

Flujo de orquestación:

```
ingest_repository
  -> analyze_structure
  -> [scan_sast, scan_dependencies, scan_secrets, assess_quality]
  -> explain_codebase
  -> recommend_improvements
  -> generate_report
```

## Requisitos

- Python 3.11+
- Credenciales de AWS con acceso a Amazon Bedrock (para el modo agente)
- Escáneres externos opcionales para cobertura completa:
  - [Semgrep](https://semgrep.dev/docs/getting-started/) para SAST. Instálalo
    con el extra: `pip install -e ".[scanners]"` (o `pip install semgrep`).
    `scan_sast` lo detecta automáticamente; sin él, corren las heurísticas.
  - [Trivy](https://aquasecurity.github.io/trivy/) (opcional, SCA/secretos)

## Instalación

Este episodio usa un **entorno virtual propio** (uno por episodio):

```bash
# Desde la carpeta del Ep.2
python3 -m venv .venv
source .venv/bin/activate

pip install -e .
# Para desarrollo (tests):
pip install -e ".[dev]"
```

## Configuración

Copia `.env.example` y ajústalo, o exporta las variables directamente.

### Variables del Ep.1 (Bedrock)

```bash
export AWS_REGION=us-east-2                 # región del proyecto
export AWS_PROFILE=my-Free-tier             # perfil creado con `aws login`
export BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0
```

### Variables nuevas del Ep.2 (memoria)

| Variable | Default | Descripción |
| --- | --- | --- |
| `MEMORY_BACKEND` | `json` | Backend de almacenamiento del historial. Hoy solo `json`; `sqlite`/`dynamodb`/`redis` son puntos de extensión. |
| `MEMORY_DIR` | `~/.code_copilot/sessions` | Directorio donde se persiste el historial (una subcarpeta por `session_id`). |
| `MEMORY_STRATEGY` | `truncate` | Estrategia de poda: `truncate` (ventana deslizante) o `summarize` (resumen con el modelo). |
| `MEMORY_MAX_TOKENS` | `4000` | Umbral aproximado de tokens que dispara la poda. |
| `MEMORY_PRESERVE_RECENT` | `2` | Mínimo de mensajes recientes que siempre se conservan intactos al podar. |
| `MEMORY_SESSION_ID` | `default` | `session_id` por defecto cuando no se pasa `--session-id`. |

Habilita el acceso al modelo Amazon Nova Pro en la consola de Bedrock antes de
correr en modo agente.

## Uso

> **Importante (macOS / entorno por episodio):** primero **activa el venv** de
> este episodio. El comando `code-copilot` y el paquete `code_copilot` solo
> existen dentro de ese entorno. En macOS con Homebrew el intérprete es
> `python3` (no existe `python`).
>
> ```bash
> # Desde la carpeta del Ep.2
> source .venv/bin/activate      # <- imprescindible antes de cada comando
> ```
>
> Si no quieres activar el venv, invoca su intérprete directamente con
> `./.venv/bin/python -m code_copilot.cli ...` (ver más abajo).

Modo agente (con LLM, requiere Bedrock):

```bash
# El session_id por defecto es "default"
code-copilot https://github.com/octocat/Hello-World

# Fija un session_id para poder retomar la conversación después
code-copilot /ruta/al/repo --session-id proyecto-x --report reports/x.md
```

Modo offline (pipeline estático determinista, sin AWS):

```bash
code-copilot /ruta/al/repo --offline --report reports/x.md
```

También puedes correrlo **sin activar** el venv, llamando a su intérprete de
forma explícita (nota `python3` / la ruta del venv, no `python`):

```bash
# con el venv activado:
python3 -m code_copilot.cli /ruta/al/repo --offline --report reports/x.md

# o sin activarlo, apuntando al intérprete del venv:
./.venv/bin/python -m code_copilot.cli /ruta/al/repo --offline --report reports/x.md
```

### Ver la persistencia entre ejecuciones

Corre **dos veces** con el **mismo `session_id`**. La segunda corrida recupera
el historial de la primera (memoria de largo plazo):

```bash
# Primera ejecución: se crea y persiste el historial de la sesión "demo"
code-copilot /ruta/al/repo --session-id demo

# Segunda ejecución (proceso nuevo): recupera el historial de "demo"
code-copilot /ruta/al/repo --session-id demo
# -> imprime "Recovered N message(s) from prior session."
```

El historial y el estado compartido quedan en
`<MEMORY_DIR>/session_demo/` (`messages.json` y `state.json`).

### Cambiar la estrategia de poda y el umbral

Todo es por configuración, nada hardcodeado:

```bash
# Poda por truncado (barata) con un presupuesto más chico
MEMORY_STRATEGY=truncate MEMORY_MAX_TOKENS=2000 \
  code-copilot /ruta/al/repo --session-id demo

# Poda por resumen (preserva contexto, cuesta una llamada extra a Nova Pro)
MEMORY_STRATEGY=summarize MEMORY_MAX_TOKENS=3000 \
  code-copilot /ruta/al/repo --session-id demo
```

### Ver el costo de la poda

Cuando ocurre un evento de poda se registra en el log la estrategia usada y el
efecto en tokens (antes/después), visible en la terminal:

```
INFO code_copilot.memory: memory.prune strategy=truncate tokens_before=5200 tokens_after=3800 saved=1400 messages_before=18 messages_after=13
```

## Trade-offs de backends de memoria

Todos implementan la misma interfaz `MemoryStore` (`load`/`save`); se eligen por
`MEMORY_BACKEND` sin tocar el resto del código. En este episodio solo se
implementa `json`; el resto son puntos de extensión de una sola clase.

| Backend | Simplicidad | Concurrencia | Expiración (TTL) | Cuándo usarlo |
| --- | --- | --- | --- | --- |
| **JSON** (este ep.) | Alta, cero infra, inspeccionable | Baja (un proceso; bloqueos de archivo) | Manual | Local, demos, un solo usuario |
| **SQLite** | Media, un archivo, transaccional | Media (un escritor a la vez) | Manual / job | Local con consultas o varias sesiones |
| **DynamoDB** | Baja (infra AWS) | Alta (serverless, escalable) | Nativa (atributo TTL) | Producción en AWS, multiusuario |
| **Redis** | Media | Alta (baja latencia) | Nativa (`EXPIRE`) | Caché de sesiones caliente, tiempo real |

Para agregar uno: implementa `MemoryStore` (dos métodos) y regístralo en
`memory.buildStore`.

## Alcance (scope guard)

Esto es **memoria conversacional**: recordar el hilo del chat entre turnos y
entre ejecuciones. **No** es RAG ni indexado del repositorio (eso llega en la
**Semana 3**), ni memoria multi-agente (**Semana 4**). Este episodio no toca
esos temas a propósito, para mantener la decisión de arquitectura enfocada.

## Salida a color

- Respuestas del agente: azul brillante, con el prefijo `agent>`.
- Cada herramienta: un color propio y estable, mostrado como una etiqueta entre
  corchetes tipo `[scan_sast] running...`.
- El color se desactiva solo cuando la salida no es una TTY o cuando `NO_COLOR`
  está definido.

## Servidores MCP (integraciones opcionales)

Son opcionales; las herramientas degradan con elegancia sin ellos.

- **GitHub MCP server** — acceso más rico a repos/PRs/code-scanning. Configúralo
  en tu herramienta de IA y define `GITHUB_TOKEN`.
- **Semgrep** — instala el CLI (`pip install semgrep`) o corre el MCP server;
  `scan_sast` usa el CLI automáticamente cuando está presente.
- **Trivy CLI** — escáner alternativo opcional para SCA/secretos/contenedores.
- **OSV.dev API** — la usa `scan_dependencies` sobre HTTPS; sin autenticación.

## Modelo de seguridad

- El contenido del repositorio se trata como **no confiable**. El agente
  **nunca ejecuta** código del repo; solo lee y analiza estáticamente.
- Los valores de secretos nunca se imprimen ni se registran. Los hallazgos
  referencian solo el tipo, la ubicación y una vista previa redactada.
- La capa de memoria tampoco registra el contenido de los mensajes: solo roles
  y conteos de tokens.
- Si un escáner falta o falla, el pipeline continúa y reporta la limitación en
  vez de abortar.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

## Estructura del proyecto

```
src/code_copilot/
  cli.py          # punto de entrada de terminal (argparse) + wiring de memoria
  agent.py        # wiring del agente Strands (recibe historial podado)
  memory.py       # NUEVO Ep.2: MemoryStore, JsonFileStore, SessionManager, prune
  config.py       # NUEVO Ep.2: configuración de memoria por variables de entorno
  state.py        # estado de análisis compartido + persistencia entre corridas
  callback.py     # callback de streaming a color
  colors.py       # helpers de color ANSI
  model.py        # factory del modelo Bedrock Nova Pro
  pipeline.py     # pipeline offline determinista
  utils.py        # helpers compartidos (walking, detección, redacción)
  tools/          # las nueve implementaciones @tool
tests/            # tests unitarios por herramienta + pipeline e2e + test_memory.py
```
