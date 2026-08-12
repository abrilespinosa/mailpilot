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
- [ ] Fase 2 — Ingestión de datos (Gmail → parsing → PostgreSQL). **Siguiente paso.** Se
      levantará PostgreSQL vía Docker Compose.
- [ ] Fase 3 — Modelo de datos formal con SQLAlchemy + Alembic
- [ ] Fase 4 — Backend con FastAPI
- [ ] Fase 5 — IA local con Ollama
- [ ] Fase 6 — Evaluación de modelos
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

# Verificar OAuth + listar los últimos 5 correos (abre el navegador la primera vez)
python scripts/test_auth.py
```

Aún no hay tests automatizados, linter ni formateador configurados. `pytest` está en el
stack previsto pero no instalado. Cuando se añadan (Fase 10), documentar aquí el comando
para ejecutar un test individual.

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
│   └── auth.py          # gestión de credenciales OAuth (único módulo que toca credentials/)
├── scripts/              # scripts de prueba manual, no parte del producto
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
