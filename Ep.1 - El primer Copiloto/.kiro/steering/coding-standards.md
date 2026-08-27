# Estándares de Programación: Clean Code, SOLID y Convenciones

Estas reglas aplican a todo el código que escribas o modifiques en este workspace, sin importar el lenguaje.

## Convención de nombres

- Usa **camelCase** en TODAS las declaraciones de variables (locales, parámetros, propiedades y campos).
  - Correcto: `userName`, `totalAmount`, `isActive`, `retryCount`
  - Incorrecto: `user_name`, `TotalAmount`, `is_active`, `RETRYCOUNT`
- Excepciones razonables por convención del lenguaje:
  - Constantes verdaderamente inmutables a nivel de módulo pueden ir en `UPPER_SNAKE_CASE` (ej. `MAX_RETRIES`).
  - Nombres de clases, tipos e interfaces van en `PascalCase` (ej. `OrderService`, `UserRepository`).
  - Nombres de funciones y métodos también en `camelCase` (ej. `calculateTotal`, `fetchUser`).
- Cuando un lenguaje imponga una convención distinta y fuertemente idiomática para variables (por ejemplo `snake_case` en Python), prioriza esta regla de camelCase solo si no rompe herramientas o linters del proyecto. Si el proyecto ya tiene un linter/configuración establecida, respeta primero la configuración existente y avísame del conflicto.

## Nombres significativos

- Los nombres deben revelar la intención: qué representa el valor o qué hace la función.
- Evita abreviaturas ambiguas y nombres de una sola letra (salvo índices de bucles cortos como `i`, `j`).
- Usa nombres pronunciables y buscables.
- Los booleanos deben leerse como predicados: `isValid`, `hasPermission`, `canRetry`.

## Clean Code

- **Funciones pequeñas**: cada función hace una sola cosa y la hace bien. Si una función supera ~20-30 líneas o mezcla niveles de abstracción, divídela.
- **Un solo nivel de abstracción por función**: no mezcles lógica de alto nivel con detalles de bajo nivel.
- **Pocos parámetros**: idealmente 0-3. Si necesitas más, agrupa en un objeto/estructura.
- **Evita efectos secundarios ocultos**: una función no debe modificar estado global inesperado.
- **Sin números mágicos ni cadenas mágicas**: extráelos a constantes con nombre.
- **Evita el código duplicado (DRY)**: extrae lógica repetida a funciones o módulos reutilizables.
- **Comentarios**: prefiere código autoexplicativo. Comenta el *por qué*, no el *qué*. Elimina código comentado muerto.
- **Manejo de errores explícito**: no silencies excepciones. Usa validación de entradas y mensajes de error claros.
- **Formato consistente**: respeta el estilo y el formateo existentes del proyecto (indentación, comillas, punto y coma).
- **Guard clauses**: usa retornos tempranos para reducir anidamiento profundo de `if`.

## Principios SOLID

- **S — Single Responsibility**: cada clase o módulo tiene una única razón para cambiar. Separa responsabilidades distintas.
- **O — Open/Closed**: el código debe estar abierto a extensión pero cerrado a modificación. Prefiere extender mediante composición, interfaces o inyección antes que editar código estable.
- **L — Liskov Substitution**: las subclases deben poder sustituir a su clase base sin romper el comportamiento esperado.
- **I — Interface Segregation**: prefiere interfaces pequeñas y específicas en lugar de una interfaz grande que obligue a implementar métodos innecesarios.
- **D — Dependency Inversion**: depende de abstracciones (interfaces), no de implementaciones concretas. Inyecta dependencias en lugar de instanciarlas dentro de la clase.

## Estructura y mantenibilidad

- Organiza el código por responsabilidad/dominio, no por tipo técnico cuando sea posible.
- Mantén baja la complejidad ciclomática: si un método tiene demasiadas ramas, refactorízalo.
- Escribe código testeable: separa la lógica pura de los efectos (I/O, red, base de datos).
- Prefiere inmutabilidad cuando sea práctico; evita mutar objetos compartidos.

## Verificación

- Tras cada cambio, ejecuta el build/compilación y los linters del proyecto antes de dar por terminada la tarea.
- Si añades una funcionalidad o corriges un bug, considera pruebas relevantes.
