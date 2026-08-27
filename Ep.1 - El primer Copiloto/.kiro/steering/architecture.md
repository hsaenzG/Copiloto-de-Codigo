# Arquitectura: Local-First + Serverless en la Nube

Esta guía define la arquitectura objetivo del proyecto. Aplícala al diseñar, implementar y desplegar funcionalidades. El principio rector es **local-first**: la aplicación funciona de forma completa y rápida en el dispositivo del usuario, y la nube actúa como capa de sincronización, respaldo y cómputo bajo demanda mediante **servicios serverless**.

## Principios rectores

1. **Local-first**: el estado y la lógica principal viven en el cliente. La app debe ser usable sin conexión y sentirse instantánea.
2. **La nube es complemento, no dependencia dura**: la red es una optimización (sync, respaldo, cómputo compartido), no un requisito para la operación básica.
3. **Serverless por defecto**: sin servidores que administrar. Se paga por uso, escala automáticamente y reduce la carga operativa.
4. **Sin estado en el cómputo**: las funciones son stateless; el estado persiste en almacenes gestionados.
5. **Costo consciente**: preferir arquitecturas que escalen a cero cuando no hay uso.

## Capa local (cliente)

- **Fuente de verdad local**: mantener una base de datos o almacén local como fuente primaria de lectura/escritura.
  - Web: IndexedDB (idealmente vía una capa como Dexie), o SQLite en WASM (ej. sql.js / wa-sqlite / SQLite WASM con OPFS).
  - Escritorio/móvil: SQLite local.
- **Optimistic UI**: aplicar los cambios localmente de inmediato y luego sincronizarlos en segundo plano.
- **Cola de sincronización**: registrar mutaciones locales pendientes (outbox) para reenviarlas cuando haya conexión.
- **Offline-first**: la app arranca y opera sin red. La conectividad se detecta y se usa oportunistamente.
- **Resolución de conflictos**: definir una estrategia explícita (last-write-wins con timestamps/versionado, o CRDTs si el dominio lo requiere). Documentar la elegida por entidad.
- **Cifrado en reposo local** cuando se manejen datos sensibles.

## Capa de sincronización

- Sincronización basada en un log de cambios/deltas, no en reemplazos completos de estado.
- Idempotencia: cada mutación lleva un ID único para evitar duplicados en reintentos.
- Versionado por registro (por ejemplo `updatedAt` + versión) para detectar y resolver conflictos.
- Sincronización incremental y por lotes para minimizar llamadas de red y costo.

## Capa serverless (nube — AWS)

Componentes objetivo (todos dentro de la región del proyecto):

- **API**: Amazon API Gateway (HTTP API) o Lambda Function URLs para exponer endpoints.
- **Cómputo**: AWS Lambda para lógica bajo demanda (sync, validación, procesos en background). Funciones pequeñas, con una sola responsabilidad.
- **Persistencia gestionada**: Amazon DynamoDB como almacén principal serverless (escala a cero, pago por uso). Diseñar el modelo a partir de los patrones de acceso.
- **Almacenamiento de objetos**: Amazon S3 para archivos, blobs y respaldos.
- **Autenticación**: Amazon Cognito (user pools) para sign-in/sign-up y emisión de tokens.
- **Eventos/asíncrono**: Amazon EventBridge o Amazon SQS para desacoplar y procesar trabajo en background.
- **Infraestructura como código**: definir todo con AWS CDK, SAM o CloudFormation. Nada de recursos creados a mano en la consola.

### Restricciones de despliegue (nueva experiencia de AWS)

Este proyecto usa la nueva experiencia de AWS. Al desplegar:

- Crear TODOS los recursos regionales (Lambda, API Gateway, DynamoDB, S3, Cognito) en la **región del proyecto** (confirmar en AWS Settings o en `~/.aws/config`).
- NO crear recursos regionales en otras regiones.
- NO usar Lambda@Edge.
- NO usar CloudFormation StackSets (sin despliegues multi-cuenta o multi-región).
- NO usar acciones cross-region: sin replicación cross-region de DynamoDB/S3/RDS, sin llaves KMS multi-región.
- NO usar enrutamiento cross-region de Route 53 (geolocation, latency-based, failover).
- CloudFront es global y sus acciones sí se permiten en `us-east-1`; puede apuntar a una Function URL o API Gateway de la región del proyecto. Pero Lambda y API Gateway deben residir en la región del proyecto, no en `us-east-1`.
- Antes de usar un servicio, verificar disponibilidad con `aws freetier get-account-plan-state` y las listas de servicios soportados del plan (Free o Paid).

## Patrones de diseño

- **Separar dominio de infraestructura**: la lógica de negocio no debe depender directamente de SDKs de AWS ni de APIs del navegador. Usar interfaces/puertos (inversión de dependencias) y adaptadores para local y nube.
- **Repositorios intercambiables**: definir una interfaz de repositorio que tenga una implementación local (IndexedDB/SQLite) y una remota (DynamoDB/S3), seleccionable por configuración.
- **Funciones puras para la lógica de negocio**: mantener las funciones core sin efectos, testeables sin red ni disco.
- **Stateless en la nube**: cada invocación de Lambda no asume estado en memoria entre llamadas.
- **Contratos de API versionados y explícitos**: definir tipos/esquemas compartidos entre cliente y backend.

## Seguridad

- Autenticar todas las rutas de la API (autorizadores de Cognito/JWT en API Gateway).
- Aplicar mínimo privilegio en los roles IAM de las funciones (solo los permisos de los recursos que usan).
- Nunca exponer credenciales en el cliente; el acceso a AWS pasa por el backend o por tokens temporales de identidad.
- Validar toda entrada en el backend, aunque el cliente ya haya validado.

## Observabilidad

- Registrar logs estructurados en las funciones (CloudWatch Logs).
- Instrumentar métricas y trazas cuando el flujo lo justifique.
- Incluir IDs de correlación para seguir una operación desde el cliente hasta la nube.

## Verificación de despliegue

- Desplegar mediante IaC y validar el stack antes de aplicar cambios (por ejemplo `cdk diff` / change sets).
- Preferir arquitecturas que escalen a cero para minimizar costo cuando no hay uso.
- Al terminar una prueba en la nube, preguntar si se limpian los recursos creados para reducir costo.
