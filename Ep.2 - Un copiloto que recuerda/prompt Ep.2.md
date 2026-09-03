Estás trabajando en el repositorio `Copiloto-de-Codigo`. Ya existe la carpeta `Ep.1 - El primer Copiloto` con un agente copiloto de código construido con el Strands Agents SDK y Amazon Bedrock (Amazon Nova Pro). Vamos a construir el Episodio 2 sobre esa base, sin romper nada de lo anterior.

### Paso 1 — Crear el episodio 2 a partir del episodio 1 (hazlo primero)

1. Crea una carpeta nueva en la raíz del repo llamada exactamente: `Ep.2 - Un copiloto que recuerda`.
2. Copia dentro de ella TODO el contenido del proyecto de `Ep.1 - El primer Copiloto` (incluyendo `src/`, `tests/`, `.kiro/`, `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore` y `README.md`). El Ep.2 debe arrancar como una copia idéntica y funcional del Ep.1.
3. No modifiques la carpeta `Ep.1 - El primer Copiloto`. Se queda congelada como la versión que hace match con el live 1.
4. Verifica que la copia corre en modo offline sin cambios: `python -m code_copilot.cli <ruta-a-un-repo> --offline`.

Todo lo que sigue se hace ÚNICAMENTE dentro de `Ep.2 - Un copiloto que recuerda`.

### Paso 2 — Objetivo del episodio 2

Darle memoria al copiloto, como decisión de arquitectura y no como un buffer de mensajes que crece sin control. Tres capacidades:

1. **Memoria de corto plazo (sesión):** el historial del hilo actual, que el agente recibe en cada turno.
2. **Memoria de largo plazo (persistente):** el historial sobrevive al cierre del proceso y se recupera por `session_id` entre ejecuciones.
3. **Poda del context window:** cuando el historial supera un umbral de tokens, se poda con una estrategia configurable (truncado o resumen), con conciencia de costo y latencia.

Alcance (scope guard): esto es memoria conversacional. NO implementes RAG ni indexado del repo (es la Semana 3), ni multi-agente (Semana 4). No lo toques.

### Paso 3 — Diseño a implementar

**Nuevo módulo `src/code_copilot/memory.py`** con responsabilidad única (persistir, recuperar, podar), separado del wiring del agente. Debe incluir:

1. Una interfaz `MemoryStore` como `typing.Protocol` con al menos:
   - `load(self, session_id: str) -> list[Message]`
   - `save(self, session_id: str, messages: list[Message]) -> None`
2. Una implementación `JsonFileStore` que persiste el historial por `session_id` en archivos JSON locales (simple, inspeccionable, cero infraestructura). La ruta base debe ser configurable.
3. Un `SessionManager` que use un `MemoryStore` y orqueste el ciclo de un turno: `load(session_id) -> prune -> (el agente corre) -> append -> save(session_id)`.
4. Una función de poda `prune(messages, *, strategy, max_tokens)` con dos estrategias:
   - `"truncate"`: ventana deslizante, conserva los últimos mensajes que caben en `max_tokens`. O(1), barato, pierde el contexto viejo.
   - `"summarize"`: resume los mensajes viejos con el modelo (Bedrock Nova Pro) y conserva los recientes. Cuesta una llamada extra, preserva contexto.
   - La estrategia y el umbral (`max_tokens`) deben ser configurables, no valores fijos en el código.
5. Un contador de tokens aproximado (`count_tokens`) para decidir cuándo podar y para poder registrar el costo por turno.

**Integración en el wiring existente** (donde hoy se arma el agente, p. ej. `agent.py` / `cli.py`):
- Inyecta el `SessionManager` en la construcción del agente.
- Acepta un `session_id` desde la CLI (argumento nuevo, con un valor por defecto razonable).
- Al iniciar un turno: carga el historial por `session_id` y aplícale la poda antes de mandarlo al modelo.
- Al terminar el turno: agrega el nuevo intercambio al historial y persístelo.
- Reutiliza el estado compartido que ya existe en `state.py`; ahora ese estado debe poder sobrevivir entre corridas junto con la conversación. No dupliques lógica de estado.

**Configuración** (respetando el estilo del Ep.1 con variables de entorno / `.env.example`):
- Añade variables para: directorio de persistencia de memoria, estrategia de poda por defecto, y umbral de tokens. Documenta cada una en el `.env.example` y el README del Ep.2.

**Logging del costo:**
- Cuando ocurra un evento de poda, registra en el log qué estrategia se usó y el efecto en tokens (antes/después). Que sea visible en la demo.

### Paso 4 — Evaluación

- Reutiliza la infraestructura de tests del Ep.1 (`tests/`).
- Agrega casos de evaluación multi-turno que dependan de recordar: el turno 1 fija un hecho (por ejemplo "el entrypoint es `cli.py`") y un turno posterior lo interroga sin repetirlo.
- Agrega un test de poda: tras podar, la información reciente y crítica debe sobrevivir; con `summarize`, verifica que los hechos clave se conservan.
- Agrega un test de persistencia: guardar con un `session_id`, crear un `MemoryStore` nuevo, cargar con el mismo `session_id` y confirmar que el historial se recupera.
- Los tests con backend local deben ser deterministas (no dependas de Bedrock para los tests de persistencia y truncado).

### Paso 5 — Documentación (README del Ep.2, en español)

Actualiza el `README.md` dentro de `Ep.2 - Un copiloto que recuerda` para explicar:
- Qué agrega este episodio (memoria de sesión, persistencia, poda).
- Cómo correr dos sesiones con la misma `session_id` para ver la persistencia entre ejecuciones.
- Cómo cambiar la estrategia de poda y el umbral por configuración.
- La tabla de trade-offs de backends (JSON contra SQLite contra DynamoDB/Redis: simplicidad, concurrencia, expiración).
- El recordatorio de alcance: esto es memoria conversacional; RAG llega en la Semana 3.

### Restricciones y estilo

- Código y comentarios en inglés; el README y los docs en español.
- Sigue las convenciones existentes del Ep.1 (estructura `src/code_copilot/`, tipado, manejo de errores por componente).
- Trata el contenido del repo analizado como no confiable: nunca ejecutar código del repo, nunca imprimir secretos.
- Cada decisión (backend, estrategia de poda, umbral) va configurable, no hardcodeada.
- No modifiques la carpeta del Ep.1.

### Entregables

1. Carpeta `Ep.2 - Un copiloto que recuerda` como copia funcional del Ep.1 más:
   - `src/code_copilot/memory.py` con `MemoryStore`, `JsonFileStore`, `SessionManager` y `prune`.
   - Wiring del agente integrando el `SessionManager` y el argumento `session_id` en la CLI.
   - Config nueva documentada en `.env.example`.
   - Tests multi-turno, de poda y de persistencia.
   - README del Ep.2 actualizado en español.
2. Que el modo offline y los tests del Ep.2 sigan pasando.
