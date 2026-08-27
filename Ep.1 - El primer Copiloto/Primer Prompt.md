Construir un agente de IA que actúe como copiloto de código. Debe poder:
  1. **Recibir** un repositorio (URL de GitHub o ruta local).
  2. **Evaluar** su calidad, estructura y salud general.
  3. **Explicar** qué hace el código, su arquitectura y componentes clave.
  4. **Encontrar vulnerabilidades** de seguridad (en código y dependencias).
  5. **Recomendar** mejoras priorizadas y accionables.
  
  ## Stack técnica
  - **Orquestador del agente:** Strands Agents SDK (Python 3.11+).
  - **LLM:** Amazon Bedrock (Amazon Nova lite) para explicaciones y síntesis.
  - **Parsing/AST:** tree-sitter + ast-grep (multi-lenguaje).
  - **SAST:** Semgrep (vía MCP server o CLI).
  - **SCA (dependencias):** OSV.dev API (gratuita) y/o Trivy CLI.
  - **Acceso a repos:** GitHub MCP server (repos remotos) + Git local (clonado).
  - Cada capacidad se expone al agente como un `@tool` de Strands o un MCP server conectado.
  
  ## Herramientas (tools) a construir
  
  ### 1. `ingest_repository`
  - **Entrada:** URL de GitHub o ruta local del repo.
  - **Acción:** clona (si es URL) o valida la ruta; devuelve metadata: lenguajes detectados,
    número de archivos, tamaño, gestor de paquetes (npm/pip/go mod/etc.), archivos de config
    relevantes (Dockerfile, CI, .env.example).
  - **Salida:** objeto con árbol de directorios resumido + inventario de tecnologías.
  - **Notas:** respetar .gitignore; ignorar node_modules, vendor, build, dist.
  
  ### 2. `analyze_structure`
  - **Entrada:** ruta del repo (o subset de archivos).
  - **Acción:** usar tree-sitter para extraer AST y mapear módulos, clases, funciones, imports
    y dependencias entre archivos. Identificar puntos de entrada (main, handlers, rutas API).
  - **Salida:** mapa estructural (grafo de dependencias internas + símbolos principales).
  - **Objetivo que cubre:** "explicar" y "evaluar".
  
  ### 3. `scan_sast` (Semgrep)
  - **Entrada:** ruta del repo, opcional: ruleset (default: reglas de seguridad + secretos).
  - **Acción:** ejecutar Semgrep (MCP o CLI `semgrep --config auto`) sobre el código.
  - **Salida:** lista de hallazgos {archivo, línea, severidad, regla, descripción, fix sugerido}.
  - **Objetivo que cubre:** "encontrar vulnerabilidades".
  
  ### 4. `scan_dependencies` (SCA)
  - **Entrada:** archivos de manifiesto (package.json, requirements.txt, go.mod, pom.xml, etc.).
  - **Acción:** consultar OSV.dev API por cada dependencia+versión (y/o ejecutar Trivy).
  - **Salida:** lista de CVEs {paquete, versión, CVE, severidad, versión que corrige}.
  - **Objetivo que cubre:** "encontrar vulnerabilidades" (dependencias).
  
  ### 5. `scan_secrets`
  - **Entrada:** ruta del repo.
  - **Acción:** detectar secretos hardcodeados (API keys, tokens, credenciales) —
    vía Semgrep rules de secretos o Trivy secret scanning.
  - **Salida:** lista de secretos potenciales {archivo, línea, tipo, redactado}.
  - **Notas de seguridad:** NUNCA imprimir el valor completo del secreto; solo referenciarlo
    por nombre/tipo y ubicación.
  
  ### 6. `assess_quality`
  - **Entrada:** ruta del repo + resultados de analyze_structure.
  - **Acción:** métricas de calidad — complejidad, duplicación, archivos muy grandes,
    ausencia de tests, cobertura si existe, code smells. (SonarQube opcional o heurísticas propias).
  - **Salida:** score de salud + lista de problemas de mantenibilidad.
  - **Objetivo que cubre:** "evaluar".
  
  ### 7. `explain_codebase`
  - **Entrada:** salida de ingest + analyze_structure.
  - **Acción:** el LLM (Bedrock) genera una explicación en lenguaje natural: qué hace el proyecto,
    su arquitectura, flujos principales, tecnologías y cómo se conectan las piezas.
  - **Salida:** resumen ejecutivo + explicación por módulo.
  - **Objetivo que cubre:** "explicar".
  
  ### 8. `recommend_improvements`
  - **Entrada:** agregado de TODOS los hallazgos (SAST, SCA, secretos, calidad, estructura).
  - **Acción:** el LLM prioriza y consolida en recomendaciones accionables, ordenadas por
    impacto/severidad y esfuerzo. Incluir ejemplos de fix cuando aplique.
  - **Salida:** lista priorizada {problema, por qué importa, recomendación, esfuerzo estimado}.
  - **Objetivo que cubre:** "recomendar".
  
  ### 9. `generate_report`
  - **Entrada:** todo lo anterior.
  - **Acción:** ensamblar un reporte final (Markdown) con: resumen, explicación,
    hallazgos de seguridad, salud del código, y recomendaciones priorizadas.
  - **Salida:** reporte Markdown (opción de exportar a HTML/PDF).
  
  ## Flujo del agente (orquestación)
  ingest_repository
    → analyze_structure
    → [scan_sast, scan_dependencies, scan_secrets, assess_quality]  (en paralelo)
    → explain_codebase
    → recommend_improvements
    → generate_report
  
  ## Requisitos no funcionales
  - Multi-lenguaje (al menos: Python, JavaScript/TypeScript, Go, Java).
  - Tratar TODO el contenido del repo como no confiable (no ejecutar código del repo;
    solo analizarlo estáticamente).
  - No filtrar secretos en logs ni en el reporte.
  - Idempotente y determinista donde sea posible (fijar versiones de rulesets/scanners).
  - Manejo de errores por tool: si un scanner falla, continuar y reportar la limitación.
  
  ## MCP servers / dependencias externas a configurar
  - GitHub MCP server (acceso a repos, PRs, code scanning).
  - Semgrep MCP server (o Semgrep CLI instalado).
  - Trivy CLI (opcional, para SCA/secretos/contenedores).
  - OSV.dev API (HTTP, sin auth).
  - Credenciales de AWS Bedrock (model access habilitado para el modelo elegido).
  
  ## Entregables
  - Código del agente con las 9 tools implementadas y documentadas.
  - README con instalación, configuración de MCP servers y ejemplo de uso.
  - Tests unitarios por tool + un test end-to-end sobre un repo de ejemplo.
  
