# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# MailPilot — Contexto del proyecto

## Qué es esto

MailPilot es un proyecto de portfolio personal (no una startup, no un producto real) para
aprender arquitectura backend, sistemas de datos, IA aplicada y seguridad, construyendo
una capa de gestión inteligente sobre Gmail.

**Problema que resuelve**: sobrecarga de decidir qué hacer con cada correo entrante.
La usuaria necesita su bandeja ordenada por categorías y quiere poder limpiar ruido
(promociones, spam) sin revisar correo por correo.

## Principio fundamental — NO NEGOCIABLE

> La IA propone. El usuario decide. MailPilot ejecuta únicamente las acciones autorizadas.

- El LLM nunca ejecuta acciones directamente sobre Gmail.
- Toda acción destructiva o de modificación pasa por: propuesta → validación (Policy Engine) →
  aprobación humana explícita → ejecución → audit log.
- El contenido de los correos NUNCA sale de la máquina local hacia proveedores de IA externos.
  Todo el procesamiento de IA es local (Ollama).

## Cómo debe comportarse Claude en este proyecto

Este NO es un proyecto de "constrúyeme esto y ya". Es un proyecto de aprendizaje activo.
Rol esperado: mentor técnico + profesor + pair programmer + arquitecto + code reviewer.

Reglas de trabajo:
- Explicar decisiones de arquitectura antes de implementarlas, no después.
- Cuestionar decisiones cuando sean incorrectas o innecesariamente complejas. No dar la razón
  automáticamente.
- Señalar sobreingeniería activamente. No introducir tecnología nueva sin justificar:
  problema → solución → alternativa simple → coste → beneficio de aprendizaje.
- Antes de código importante: explicar qué problema resuelve, dónde encaja, decisiones clave,
  dar el código, explicarlo por bloques, indicar qué partes debe poder modificar la usuaria,
  y proponer una pregunta o ejercicio de comprobación.
- No avanzar de fase hasta que la actual esté comprendida y funcional.
- Preferir explicaciones y opciones razonadas sobre preguntas socráticas encadenadas — la
  usuaria prefiere que se le den las ideas con su razonamiento para elegir, no que se le
  interrogue paso a paso.
- Nivel de la usuaria: 2º de Ingeniería de Sistemas de Datos. Conoce Python, APIs, Docker,
  n8n, PostgreSQL y SQLAlchemy a nivel funcional (no avanzado). No necesita explicaciones
  introductorias de estos conceptos base. Quiere profundizar en: arquitectura backend, IA
  aplicada/LLMs, seguridad, OAuth, async, testing, CI/CD, observabilidad.

## Decisiones de arquitectura ya tomadas (no reabrir sin motivo nuevo)

- **Tenancy**: single-tenant. Ampliable a multiusuario en el futuro si surge necesidad real,
  no antes.
- **Coste**: proyecto 100% gratuito. Sin APIs de IA de pago. Gmail API + Ollama local +
  stack open source.
- **Hardware de desarrollo**: MacBook con chip Apple M5, 24GB RAM. Soporta modelos locales
  vía Ollama tipo 7-8B cuantizados sin problema; medir antes de asumir viabilidad de modelos
  mayores.
- **OAuth**: modo "Testing" en Google Cloud Console (tokens caducan cada 7 días, asumido
  como aceptable). Scope inicial: `gmail.readonly`. Escalar a `gmail.modify` solo cuando se
  implementen acciones reales (Fase de ejecución), como decisión explícita documentada en ADR.
- **Acción destructiva permitida**: SOLO mover a papelera (`trash`, reversible 30 días en
  Gmail). Borrado permanente (`delete`) está **fuera de alcance del proyecto**, no solo del
  MVP. No implementar nunca, ni siquiera detrás de confirmación.
- **Prompt injection**: mitigación es arquitectónica, no basada en detección de frases. El
  LLM solo devuelve structured output validado contra un schema/enum cerrado (categoría,
  confianza en [0,1], acción de un enum fijo). Cualquier salida que no valide se descarta o
  se marca `review_needed`, nunca se ejecuta ni se muestra tal cual.

## Modelo de datos (MVP, sujeto a revisión en Fase 3 formal)

- **Email**: `gmail_message_id` (único, clave de idempotencia), `gmail_thread_id`, `subject`,
  `sender`, `snippet`, `received_at`, `raw_labels` (JSON), `created_at`.
- **Categorías** (`category` en Classification y ActionProposal): enum cerrado de siete valores
  — `personal`, `trabajo`, `compras`, `banco`, `avisos`, `promociones`, `otros`. Definiciones,
  reglas de desempate y justificación en `docs/decisions/001-categorias-de-clasificacion.md`.
  No confundir con las etiquetas que la usuaria ya tiene en Gmail: son mundos separados y
  `raw_labels` es solo una copia informativa.
- **Classification**: FK a Email, `category`, `confidence`, `reasoning`, `model_used`,
  `created_at`. Relación 1-a-muchos con Email a propósito (histórico de reclasificaciones,
  útil para evaluación de modelos en fase futura).
- **ActionProposal**: FK a Email, `proposed_action` (enum: categorize, move_to_trash),
  `category`, `reason`, `confidence`, `status` (enum: pending, approved, rejected, modified,
  executed, failed), `created_at`, `decided_at`. (Nota: se fusionó ActionProposal +
  UserDecision del diseño original en una sola tabla por simplicidad de MVP — revisar si
  hace falta separar cuando se necesite historial de cambios de decisión.)
- **AuditLog**: FK nullable a ActionProposal, `event_type`, `detail` (JSON), `created_at`.

Entidades descartadas del MVP explícitamente: `Thread` como tabla propia (el `thread_id` de
Gmail basta como campo simple por ahora), `Label` como tabla relacional (basta JSON de
labels de Gmail hasta que haya necesidad real de labels custom).

Idempotencia: `gmail_message_id` con constraint UNIQUE + patrón upsert. Si un email ya tiene
una `ActionProposal` no-pending (ya decidida por la usuaria), reprocesar el email NO debe
regenerar una propuesta nueva.

## Estado actual del proyecto

- [x] Fase 0 — Diseño y especificación (problema, requisitos, threat model inicial, modelo
      de datos conceptual)
- [x] Fase 1 — Gmail API + OAuth 2.0. Autenticación funcionando con scope `gmail.readonly`.
      `src/mailpilot/auth.py` gestiona el flujo. Token guardado localmente en
      `credentials/token.json` (gitignored). Verificado: `scripts/test_auth.py` lista los
      últimos 5 correos correctamente.
- [x] Fase 2 — Ingestión de datos (Gmail → parsing → PostgreSQL). `src/mailpilot/gmail.py`
      lista IDs con paginación y descarga cada correo con `format='metadata'` (sin cuerpo).
      `src/mailpilot/repository.py` hace el upsert idempotente. Verificado:
      `scripts/ingest.py` dos veces seguidas deja 20 correos, no 40.
- [x] Fase 3 — Modelo de datos formal con SQLAlchemy + Alembic. Cuatro tablas en
      `src/mailpilot/models.py`, migración inicial aplicada. PostgreSQL 17 en Docker.
- [x] Fase 4 — Backend con FastAPI. `src/mailpilot/api.py`, **solo lectura**: `/health`,
      `/emails` (paginado) y `/emails/{id}`. Esquemas Pydantic en `schemas.py`, separados
      a propósito de los modelos de SQLAlchemy. Sesión inyectada con `Depends`.
- [x] Fase 5 — IA local con Ollama. `src/mailpilot/classifier.py` con structured output
      validado contra el enum cerrado. Ollama **nativo** en macOS (usa la GPU del M5) en el
      puerto **11435**: el 11434 lo ocupa el contenedor de otro proyecto, y Ollama en Docker
      sobre Mac solo usa CPU. Modelo `qwen3:8b` (Q4_K_M, 5.2 GB).
      Primera medición sobre 20 correos reales: 0 fallos de validación, 11 s de media por
      correo, ~17/20 aciertos. **La confianza no está calibrada**: 0.95 en 18 de 20, así
      que no sirve como umbral para decidir nada automáticamente.
- [ ] Fase 6 — Evaluación de modelos. **Siguiente paso.** Hace falta un conjunto etiquetado
      a mano para medir en serio, y comparar al menos otro modelo.
- [ ] Fase 7 — Sistema de propuestas
- [ ] Fase 8 — Human-in-the-loop (frontend)
- [ ] Fase 9 — Gmail Actions (categorizar, mover a papelera)
- [ ] Fase 10 en adelante — seguridad avanzada, Docker completo, async si hace falta,
      testing, CI/CD, observabilidad, portfolio final. Ver roadmap completo en
      `docs/` (pendiente de crear).

## Comandos

Entorno: `venv/` en la raíz, Python 3.14. No hay `pyproject.toml` ni paquete instalable —
el código se importa vía manipulación de `sys.path` (ver más abajo).

```bash
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # y rellenar con credenciales reales

# Base de datos
docker compose up -d          # levantar PostgreSQL
docker compose ps             # ver estado (debe poner "healthy")
docker compose down           # parar, conservando los datos
docker compose down -v        # parar Y BORRAR los datos del volumen

# Migraciones
alembic upgrade head                                  # aplicar migraciones
alembic revision --autogenerate -m "descripción"      # generar tras cambiar models.py
alembic downgrade -1                                  # deshacer la última
alembic current                                       # en qué versión está la BD

# Scripts de prueba manual
python scripts/test_auth.py   # verificar OAuth (abre el navegador la primera vez)
python scripts/list_emails.py # leer correos e imprimirlos, sin tocar la BD
python scripts/ingest.py      # ingestión completa: Gmail → PostgreSQL
python scripts/classify.py    # clasificar con Ollama los correos sin categoría

# Ollama (nativo, no el de Docker)
OLLAMA_HOST=127.0.0.1:11435 /Applications/Ollama.app/Contents/Resources/ollama serve
OLLAMA_HOST=127.0.0.1:11435 /Applications/Ollama.app/Contents/Resources/ollama list

# Consultar la base de datos
docker compose exec db psql -U mailpilot -d mailpilot

# API
uvicorn mailpilot.api:app --reload --app-dir src
#   http://localhost:8000/docs   documentación interactiva, generada sola
#   http://localhost:8000/emails
```

**El puerto en el host es el 5433, no el 5432**: la máquina de desarrollo tiene un
PostgreSQL nativo ocupando el 5432. Dentro del contenedor sigue siendo el 5432. Al
conectar con TablePlus/DBeaver hay que usar 5433, o se acaba en la base de datos
equivocada y parece que la ingestión no guarda nada.

```bash
# Tests
pytest                                    # todo
pytest -m "not db"                        # solo los que no necesitan PostgreSQL
pytest tests/test_gmail.py                # un archivo
pytest tests/test_gmail.py::test_bandeja_vacia   # un test suelto
pytest -k "idempot"                       # los que casen con un patrón
pytest -v                                 # con el nombre de cada test
```

Los tests de base de datos usan una base aparte, `mailpilot_test`, que
`tests/conftest.py` crea sola si no existe. Cada test corre en una transacción que se
revierte al terminar, así que no dejan rastro y el orden no importa. Nunca tocan
`mailpilot`, la base con los correos reales.

Se usa PostgreSQL de verdad y no SQLite a propósito: el esquema depende de ENUM nativos
y JSONB, que SQLite no tiene. Con SQLite los tests pasarían y producción fallaría.

No hay linter ni formateador configurados todavía.

## Notas de implementación relevantes

- **`src/` no es un paquete instalado.** `scripts/test_auth.py` hace
  `sys.path.insert(0, .../src)` antes de importar `mailpilot`. Cualquier script nuevo en
  `scripts/` necesita el mismo prefacio, o se resuelve de raíz creando un `pyproject.toml`
  con instalación editable (`pip install -e .`) — decisión pendiente, documentar como ADR
  si se hace.
- **`auth.py` es la única frontera con `credentials/`.** Resuelve las rutas desde
  `Path(__file__).parents[2]`, es decir depende de vivir en `src/mailpilot/`. Si se mueve el
  módulo hay que ajustar ese cálculo. Ningún otro módulo debe leer `credentials/`: para
  obtener un cliente de Gmail, llamar a `get_credentials()` y construir el service encima.
- **Renovación de token**: `get_credentials()` refresca automáticamente si hay
  `refresh_token`; si no, relanza el flujo de navegador. Con OAuth en modo "Testing" el
  refresh token caduca a los 7 días, así que reautenticar periódicamente es esperado, no un bug.

## Stack

Confirmado: Python, Gmail API, OAuth 2.0, FastAPI, PostgreSQL, SQLAlchemy, Alembic, Ollama,
Docker, Docker Compose, pytest, Git.

Candidatos futuros (NO introducir sin justificar necesidad real primero): Redis, Celery/RQ,
React, Airflow, GitHub Actions, Prometheus/Grafana, n8n. Cada uno requiere el análisis
problema → solución → alternativa → coste → beneficio antes de añadirse.

## Estructura del repo

```
mailpilot/
├── src/mailpilot/       # código de la aplicación
│   ├── auth.py          # credenciales OAuth (único módulo que toca credentials/)
│   ├── gmail.py         # lectura de la Gmail API → EmailData
│   ├── db.py            # engine y sesiones (único que lee DATABASE_URL)
│   ├── models.py        # las cuatro tablas + los enums cerrados (cómo se guardan)
│   ├── schemas.py       # esquemas Pydantic de la API (cómo se exponen)
│   ├── repository.py    # guardado idempotente (upsert)
│   └── api.py           # FastAPI, solo lectura
├── tests/                # pytest. fakes.py = dobles de la Gmail API
├── migrations/           # Alembic: env.py + versions/
├── scripts/              # scripts de prueba manual, no parte del producto
├── docker-compose.yml    # PostgreSQL 17, publicado en el puerto 5433 del host
├── alembic.ini           # sqlalchemy.url vacío a propósito, la pone env.py desde .env
├── .env                  # NUNCA versionar — credenciales de la base de datos
├── .env.example          # plantilla sin secretos, esta sí se versiona
├── credentials/           # NUNCA versionar (gitignored y verificado) — client_secret.json, token.json
├── docs/decisions/        # ADRs. 001 = categorías de clasificación
│                          # (architecture.md y threat-model.md, pendientes)
├── venv/                  # entorno virtual local (gitignored)
├── .gitignore
├── requirements.txt
└── CLAUDE.md              # este archivo
```

## Convenciones de commits / ADRs

Commits — reglas estrictas:
- **En inglés**, siempre (el resto de la documentación del proyecto va en español; los
  commits no).
- **Conventional Commits**: `tipo(ámbito): descripción en imperativo y minúscula`.
  Tipos habituales: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
- **NUNCA añadir trailers de coautoría de Claude ni de ninguna herramienta de IA.** El
  historial es de la usuaria.

Cuando se tome una decisión arquitectónica significativa, documentarla en
`docs/decisions/NNN-titulo.md` como ADR breve (contexto, decisión, alternativas
consideradas, consecuencias).
