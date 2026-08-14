# MailPilot

Capa de gestión inteligente sobre Gmail: clasifica el correo con un LLM que corre
**en local** y propone qué hacer con cada mensaje, sin ejecutar nada por su cuenta.

Proyecto personal de aprendizaje sobre arquitectura backend, sistemas de datos, IA
aplicada y seguridad.

---

## El principio, y no es negociable

> **La IA propone. La persona decide. MailPilot ejecuta solo lo autorizado.**

- El LLM **nunca** ejecuta acciones sobre Gmail.
- Toda acción pasa por: propuesta → validación → **aprobación humana explícita** →
  ejecución → registro de auditoría.
- El contenido de los correos **nunca sale de la máquina**. Todo el procesamiento de
  IA es local, con [Ollama](https://ollama.com). No se usa ninguna API de IA externa.
- La única acción destructiva contemplada es **mover a papelera**, reversible 30 días
  en Gmail. El borrado permanente está fuera del alcance del proyecto, no solo del MVP.

---

## Cómo funciona

```
   Gmail API          PostgreSQL           Ollama              FastAPI
  ┌─────────┐        ┌──────────┐       ┌──────────┐        ┌──────────┐
  │  leer   │───────►│ guardar  │──────►│clasificar│───────►│ proponer │
  │readonly │        │idempotente│      │  local   │        │          │
  └─────────┘        └──────────┘       └──────────┘        └────┬─────┘
                                                                 │
                                                          ┌──────▼──────┐
                                                          │  La persona │
                                                          │   decide    │
                                                          └─────────────┘
```

1. **Ingestión** — se leen los mensajes con `format=metadata`: asunto, remitente,
   extracto, fecha y etiquetas. **El cuerpo de los correos no se descarga**, porque el
   modelo de datos no lo necesita. Lo que no se descarga no se puede filtrar.
2. **Persistencia** — `INSERT ... ON CONFLICT` sobre el id de Gmail. Reingerir mil
   veces deja las mismas filas.
3. **Clasificación** — un modelo local asigna una de siete categorías, con salida
   restringida a un esquema cerrado.
4. **Propuesta** — cada clasificación genera una propuesta pendiente.
5. **Decisión** — se aprueba, se corrige o se rechaza. Lo que propuso el modelo se
   conserva intacto junto a lo que eligió la persona.

---

## Prompt injection: la defensa es arquitectónica

Un correo puede contener texto diseñado para manipular al modelo. La defensa **no** es
detectar frases sospechosas, es que el modelo no tenga por dónde expresar una orden:

| Barrera | Qué hace |
|---|---|
| **Esquema en Ollama** | La generación se restringe a un esquema JSON durante el muestreo de tokens. No es una petición en el prompt. |
| **Validación Pydantic** | Lo que no valide se descarta. Los campos que el modelo se invente se ignoran. |
| **ENUM nativo de PostgreSQL** | Última barrera, y se aplica aunque alguien inserte saltándose la aplicación. |
| **Autoescapado en la plantilla** | El asunto y la explicación del modelo se pintan como texto. Un correo con `<script>` se lee, no se ejecuta. |

El modelo solo puede devolver **una de siete categorías, un número entre 0 y 1, y un
texto de explicación**. No existe ningún campo por el que pedir una acción.

El texto de explicación se muestra a la persona, pero **nunca se interpreta como
instrucción ni se ejecuta**.

Hay un test que lo comprueba alimentando un correo hostil a un modelo que le obedece:
aun así, la salida no valida y no entra al sistema.

---

## Evaluación: lo que costó llegar al número real

El clasificador se mide contra conjuntos de correos etiquetados a mano.

| Prompt | Conjunto | Acierto | |
|---|---|---|---|
| v1 | dev | 50,0 % | punto de partida |
| v2 | dev | 87,5 % | ⚠️ inflado: afinado sobre esos mismos correos |
| v2 | test | 70,0 % | primer conjunto limpio |
| v4 | test | 92,5 % | ⚠️ inflado: el prompt se escribió viendo sus fallos |
| **v4** | **test2** | **73,8 %** | **medición honesta** |
| v5 | test2 | 76,2 % | tras corregir el fallo que test2 destapó |
| **v5** | **test3** | **82,1 %** | **medición honesta, etiquetada a ciegas** |

**El 92,5 % era un espejismo de 18,7 puntos.** Afinar el prompt mirando los fallos del
mismo conjunto con el que se mide infla el resultado, y solo un conjunto que nadie ha
tocado lo revela.

### Tres cosas que enseñó medir bien

**Una palabra ambigua costaba 16 puntos.** La definición de `promociones` decía
«ofertas no solicitadas». En español *oferta* significa descuento **y** vacante de
empleo, así que los avisos de portales de trabajo caían en publicidad. Corregirlo llevó
`trabajo` de 3/16 a 16/16. No era un fallo del modelo: era ambigüedad de la
especificación.

**El acierto global puede subir mientras se rompe lo importante.** Una versión del
prompt subió al 86,2 % y hundió la categoría `personal` a 0 de 5: los correos de
personas reales acabaron en el cajón de dudas. Sin mirar la matriz de confusión, ese
cambio parecía un éxito.

**Enseñar la respuesta del modelo antes de preguntar cambia la respuesta.** Los mismos
160 correos, partidos por la mitad, mismo prompt, mismo día. Una mitad se etiquetó viendo
lo que proponía el modelo y la otra a ciegas:

| | a ciegas | viendo la propuesta |
|---|---|---|
| acierto global | 82,1 % | 85,0 % |
| acierto en la categoría ambigua | 65,4 % | 87,5 % |

El efecto se concentra justo donde la decisión es dudosa: aprobar es un clic y llevarle
la contraria cuesta. Por eso el dashboard tiene un **modo ciego** que oculta la propuesta:
sin él, cada medición saldría inflada y nadie se enteraría.

**La confianza del modelo no sirve como umbral.** Medido con dos modelos y cinco
versiones de prompt, la diferencia de confianza entre aciertos y fallos nunca superó
+0,07, y a veces fue +0,006. Pedirle explícitamente al modelo que se calibrara la
empeoró.

> **Consecuencia de diseño:** no se puede aprobar nada automáticamente por confianza
> alta. Toda propuesta pasa por una persona.

### El modelo con más aciertos no siempre es el mejor

| Modelo | Acierto | `personal` |
|---|---|---|
| qwen3:8b | 70,0 % | 3/5 |
| llama3.1:8b | **73,8 %** | **0/5** |

llama3.1 acierta más, pero **nunca predijo `personal`**: mandó los cinco al cajón de
dudas. Perder correos de personas reales pesa mucho más que confundir una promoción, así
que se eligió qwen3. El criterio no es el porcentaje, es cuánto cuesta cada tipo de error.

---

## Estado

- [x] **Fase 1** — Gmail API + OAuth 2.0, con scope `gmail.readonly`
- [x] **Fase 2** — Ingestión idempotente
- [x] **Fase 3** — Modelo de datos con SQLAlchemy + Alembic
- [x] **Fase 4** — API con FastAPI
- [x] **Fase 5** — Clasificación local con Ollama
- [x] **Fase 6** — Evaluación con conjuntos etiquetados
- [x] **Fase 7** — Sistema de propuestas y decisiones
- [x] **Fase 8** — Dashboard para revisar y decidir
- [ ] **Fase 9** — Acciones reales sobre Gmail (etiquetar, mover a papelera)
- [ ] **Fase 10+** — Seguridad avanzada, CI/CD, observabilidad

---

## Stack

Python 3.14 · FastAPI · PostgreSQL 17 · SQLAlchemy 2 · Alembic · Ollama · Docker
Compose · pytest

Sin coste: Gmail API, modelos locales y herramientas de código abierto.

---

## Puesta en marcha

**Requisitos:** Docker, Python 3.12+, [Ollama](https://ollama.com), y credenciales
OAuth de la Gmail API.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # y rellenar

docker compose up -d          # PostgreSQL
alembic upgrade head          # crear el esquema

ollama pull qwen3:8b
```

Las credenciales de Google (`credentials/client_secret.json`) se descargan de Google
Cloud Console. La carpeta está excluida del repositorio.

### Uso

```bash
python scripts/test_auth.py            # verificar OAuth
python scripts/ingest.py --limit 80    # Gmail -> PostgreSQL
python scripts/classify.py             # clasificar con Ollama
python scripts/propose.py              # generar propuestas

uvicorn mailpilot.api:app --reload --app-dir src
```

- `http://localhost:8000/` — **dashboard**: las propuestas pendientes, con siete
  categorías por correo. Pulsar la que propuso el modelo es aceptarla; pulsar otra es
  corregirle. La corrección se guarda junto a lo que dijo el modelo, sin sustituirlo.
- `http://localhost:8000/docs` — documentación interactiva de la API, generada
  automáticamente a partir de los tipos.

El dashboard **no tiene endpoints de escritura propios**: sirve HTML y sus botones
llaman a la misma API JSON que usaría cualquier otro cliente. Así las reglas viven en un
único sitio y la pantalla no es un camino privilegiado. Un test comprueba que todas sus
rutas son `GET`.

### Tests

```bash
pytest                    # todo (necesita PostgreSQL levantado)
pytest -m "not db"        # solo los que no tocan la base de datos
pytest -k idempot         # por patrón
```

### Evaluación

```bash
python scripts/build_labels.py
python scripts/evaluate.py --name mi-prueba --split test
python scripts/evaluate.py --rescore mi-prueba    # repuntuar sin repetir inferencia
```

---

## Estructura

```
src/mailpilot/
  auth.py         credenciales OAuth (único módulo que accede a credentials/)
  gmail.py        lectura de la Gmail API
  db.py           motor y sesiones
  models.py       cuatro tablas + los enums cerrados
  schemas.py      esquemas de la API, separados de los modelos a propósito
  repository.py   persistencia, propuestas y decisiones
  classifier.py   clasificación con Ollama
  api.py          FastAPI: la API JSON, único camino de escritura
  web.py          dashboard: solo rutas GET, solo sirve HTML
  templates/      dashboard.html
migrations/       Alembic
evaluation/       conjuntos etiquetados (los datos no se versionan)
docs/decisions/   ADRs
```

**Los esquemas de la API están separados de los modelos de base de datos a propósito.**
Así, añadir una columna a una tabla no la publica sola por HTTP: exponer un campo es una
decisión explícita. Un test fija el conjunto exacto de campos que devuelve el listado.

---

## Seguridad y privacidad

- El contenido de los correos **no sale de la máquina**.
- **No se descarga el cuerpo** de los mensajes: solo asunto, remitente y extracto.
- Credenciales, `.env` y datos de evaluación excluidos del repositorio.
- PostgreSQL escucha **solo en `127.0.0.1`**, nunca expuesto a la red local.
- Scope OAuth mínimo (`gmail.readonly`). Escalarlo será una decisión documentada cuando
  existan acciones reales.
- Los endpoints de escritura están en una **lista blanca** verificada por un test:
  cualquier ruta de escritura nueva lo hace fallar hasta que se añada a conciencia.
- **No existe ningún endpoint `DELETE`**, y un test lo garantiza.

---

## Decisiones de arquitectura

En [`docs/decisions/`](docs/decisions/), con contexto, alternativas descartadas y
consecuencias.

- [ADR 001 — Categorías de clasificación](docs/decisions/001-categorias-de-clasificacion.md):
  por qué siete, por qué un enum cerrado, y cómo cambiaron las definiciones al medirlas
  contra correo real.
- [ADR 002 — Tirar no es corregir](docs/decisions/002-tirar-no-es-corregir.md): por qué
  «esto es promociones» y «esto lo tiro» son dos decisiones separadas. Mezclarlas subiría
  el acierto medido 3,3 puntos borrando el 42 % de la muestra.
