# La vida me pidió poner una pausa, yo decidí construir

**Live coding para llevar agentes de IA a producción con Strands, una semana a la vez.**

Una lesión en el pie me impidió viajar a las comunidades este H2 2026, así que en lugar de pausar, muevo la comunidad a mi canal de YouTube: **8 semanas de live coding construyendo juntos, en público, un agente de IA real y llevándolo a producción con Strands.**

Aprendemos juntos, con el pie en alto. 🦶💻

📺 Canal: **[Soy Hazel Sáenz](https://www.youtube.com/@hazelsaenzG)** · 🗓️ Todos los miércoles a las 6:00 p.m. · #PausaYConstruye

---

## El proyecto: un Copiloto de Código

En vez de 8 demos sueltas, construimos **un solo agente progresivo** y cada semana le sumamos una capa de producción: un **Copiloto de Código**, un agente que entiende un repositorio y ayuda a los devs a explicar código, revisar cambios, encontrar problemas y documentar.

Cada semana el agente crece: empezamos con un primer copiloto con tools, y terminamos desplegándolo como servicio gestionado en producción.

## Roadmap de 8 semanas

| # | Fecha | Episodio | Qué construimos | Nivel | Video |
|---|-------|----------|-----------------|-------|-------|
| 1 | Ago 26 | **El primer copiloto (con el pie en alto)** | Agente que responde preguntas sobre código + tools con `@tool` (leer archivo, listar repo) + set básico de evaluación. Setup del repo y del proyecto. | 200→300 | [▶️ Ver live](https://www.youtube.com/live/sOdSN-Of5gE) |
| 2 | Sep 2 | **Un copiloto que recuerda** | Memoria del contexto del repo y de la conversación: session managers, persistencia entre sesiones, poda de context window. | 300 | 🔴 Próximamente |
| 3 | Sep 9 | **Que entienda todo el repo** | Ingerir el codebase como base de conocimiento (RAG) y exponer el copiloto como servicio con FastAPI (streaming, errores, timeouts). | 300 | 🔴 Próximamente |
| 4 | Sep 16 | **De uno a muchos: equipo de agentes** | Dividir en agentes especializados (review / seguridad / documentación); agents-as-tools vs. graph vs. swarm; estado compartido y manejo de fallos. | 300 | 🔴 Próximamente |
| 5 | Sep 23 | **Un copiloto con frenos** | Guardrails de input/output: no filtrar secretos ni credenciales, detección de PII, mantenerse en el scope del repo; Amazon Bedrock Guardrails. | 300 | 🔴 Próximamente |
| 6 | Sep 30 | **¿Qué está haciendo mi copiloto?** | Instrumentar el agent loop con OpenTelemetry; métricas de tokens/latencia/éxito; dashboards y alertas en CloudWatch. | 300 | 🔴 Próximamente |
| 7 | Oct 7 | **¿Revisa bien de verdad?** | Benchmarks de las respuestas y reviews del copiloto, LLM-as-judge, regression testing e integración en CI/CD. | 300 | 🔴 Próximamente |
| 8 | Oct 14 | **A producción (season finale)** | Deploy del copiloto como servicio gestionado en Amazon Bedrock AgentCore: auto-scaling, versionado, human-in-the-loop. | 300 | 🔴 Próximamente |

### Episodio bonus (opcional)

| Bonus | Episodio | Qué construimos | Video |
|-------|----------|-----------------|-------|
| ⭐ | **El copiloto como servidor MCP** | Exponer las tools del copiloto (leer repo, buscar, revisar) como un servidor MCP propio, para que cualquier cliente MCP (incluido un IDE) las consuma. | 🔴 Próximamente |

## Estructura del repo

Una carpeta por episodio. El agente crece semana a semana; cada carpeta es autocontenida con su propio código, README y guía de setup.

```
Copiloto-de-Codigo/
├── README.md                    # esta página: historia + roadmap + links
├── Ep.1 - El primer Copiloto/   # Semana 1 — primer agente con tools + evaluación
└── ...                          # las siguientes semanas se agregan aquí
```

## Formato de cada sesión (~75 min)

1. **5 min** — recap de la semana anterior + "cómo va el pie" (check-in personal, building in public)
2. **55 min** — live coding de la demo del día
3. **10 min** — Q&A en vivo
4. **5 min** — cierre + link al repo + qué sigue la próxima semana

- **Idioma:** español; código y comentarios en inglés.
- **Stack:** Strands Agents SDK + Amazon Bedrock, Python 3.11+.

## Episodios publicados

### Episodio 1 — El primer copiloto (con el pie en alto)

El arranque de la serie: montamos el proyecto y construimos un Copiloto de Código con Strands Agents y Amazon Bedrock que ingiere un repositorio, lo analiza (estructura, seguridad, dependencias, secretos, calidad), lo explica y recomienda mejoras. Corre en la terminal con salida coloreada.

- 📺 **Video:** https://www.youtube.com/live/sOdSN-Of5gE
- 📂 **Código:** [`Ep.1 - El primer Copiloto/`](./Ep.1%20-%20El%20primer%20Copiloto/)

---

_Serie en curso. Este repo se actualiza cada miércoles con el episodio de la semana._
