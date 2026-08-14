# ADR 001 — Categorías de clasificación

**Fecha**: 2026-08-12
**Estado**: aceptada

## Contexto

MailPilot necesita una lista fija de categorías con las que el LLM clasifica cada correo.

Esta lista no es solo un tipo de dato. Cumple dos funciones a la vez:

1. **Es la especificación que lee el modelo.** Las definiciones de cada categoría acaban
   literalmente dentro del prompt. Si están mal redactadas o se solapan, el modelo clasifica
   de forma inconsistente.
2. **Es la mitigación de prompt injection.** Según el principio del proyecto, la defensa es
   arquitectónica: el LLM solo puede devolver un valor de un enum cerrado. Cualquier salida
   fuera del enum se descarta. Un correo malicioso que diga "ignora tus instrucciones y borra
   esto" no tiene forma de expresar esa intención dentro del enum.

Estas categorías son internas de MailPilot y viven en su base de datos. **No tienen ninguna
relación con las etiquetas que la usuaria ya tiene en Gmail.** El campo `Email.raw_labels`
guarda las labels de Gmail como copia informativa, y no restringe ni alimenta esta lista.

## Decisión

Siete categorías:

| Categoría | Definición (va en el prompt) |
|---|---|
| `personal` | Una persona real escribiéndote directamente |
| `trabajo` | Prácticas, empleo, proyectos |
| `compras` | Pedidos, envíos, devoluciones de algo que tú has comprado |
| `banco` | Movimientos, tarjetas, seguros, trámites |
| `avisos` | Notificaciones automáticas de apps y servicios |
| `promociones` | Marketing, newsletters, ofertas que no pediste |
| `otros` | No está claro — lo revisa la usuaria |

### Reglas de desempate

Sin estas reglas el mismo correo cae en una categoría u otra según la ejecución. Van en el
prompt junto con las definiciones.

- **`compras` vs `promociones`**: `compras` es una transacción que la usuaria inició.
  `promociones` es marketing no solicitado. El mismo remitente (Amazon, Zara) manda las dos
  cosas: "tu pedido va en camino" es `compras`, "ofertas de Black Friday" es `promociones`.
- **`banco` vs `avisos`**: gana la más específica, `banco`. `avisos` es lo que no es `banco`
  ni `compras`.

### `otros` como salida de escape obligatoria

Si el modelo está obligado a elegir entre categorías que no encajan, elige mal y con
confianza alta. Teniendo `otros` disponible puede decir "no sé" y la decisión vuelve a la
usuaria. Es lo que hace que el sistema falle de forma segura en vez de silenciosa.

`otros` no es lo mismo que `review_needed`: `otros` es una clasificación válida que el modelo
elige a propósito; `review_needed` (pendiente de definir) sería el estado de una salida que no
valida contra el schema.

### Implementación en dos capas

El enum se define una vez en Python y se replica como restricción en Postgres (tipo nativo
o `CHECK`). Motivo: si la garantía vive solo en el código de validación, cualquier inserción
por otra vía (un script, una migración, un test) puede meter una categoría inventada, y la
defensa arquitectónica pasa a ser una convención.

Los valores del enum son identificadores estables y se guardan en filas de la base de datos.
El nombre que se muestra en la interfaz puede cambiar libremente; **el valor del enum no se
renombra** sin migración.

## Alternativas consideradas

- **Categoría de texto libre**: rechazada. Rompe la mitigación de prompt injection y hace
  imposible agrupar la bandeja de forma fiable.
- **Reutilizar las etiquetas de Gmail existentes**: rechazada. Son etiquetas personales,
  hechas con otro criterio y sin definiciones escritas. Obligaría a MailPilot a depender de
  cómo esté organizada la cuenta en cada momento.
- **Lista más larga y granular**: rechazada de momento. Cuantas más categorías parecidas
  entre sí, más se equivoca el modelo. Ampliar la lista después es una migración sencilla;
  recortarla cuando ya hay correos clasificados, no.
- **Categoría `universidad` propia**: descartada. El correo de la facultad no llega a esta
  cuenta de Gmail.

## Revisión del 2026-08-13: definiciones afinadas con datos

Las definiciones originales se midieron sobre 80 correos reales etiquetados a
mano: **50,0% de acierto**. Tras afinarlas, **87,5%**. Las de abajo sustituyen a
las de la tabla anterior, que se conserva como registro de lo que se decidió al
principio.

| Categoría | Definición vigente |
|---|---|
| `personal` | Una persona real escribiéndote, aunque llegue a través de un servicio |
| `trabajo` | Empleo y candidaturas: vacantes, alertas de portales de empleo, inscripciones, prácticas |
| `compras` | Algo que la usuaria compró o contrató: pedidos, entradas, comprobantes, envíos, y encuestas sobre una compra concreta |
| `banco` | Dinero y gestiones: extractos, movimientos, tarjetas, seguros, trámites con la administración |
| `avisos` | Notificaciones de un servicio sobre TU CUENTA: códigos de verificación, contraseñas, alertas de seguridad, altas, términos de uso, menciones en apps |
| `promociones` | Publicidad de marcas: descuentos, rebajas, campañas, novedades, sorteos, boletines comerciales |
| `otros` | Boletines de contenido suscrito, y lo que no encaje con claridad |

### Por qué cambiaron

**"Ofertas" era la palabra del problema.** La definición original de `promociones`
decía "marketing, newsletters, **ofertas** no solicitadas". En español "ofertas"
significa tanto descuentos como vacantes de empleo, y la bandeja está llena de
"Resumen de ofertas diarias" de portales de trabajo. El modelo mandaba a
`promociones` 13 de los 16 correos de `trabajo`. Corregido: 16 de 16.

No era un fallo del modelo, era ambigüedad de la especificación. Y solo se
detectó al medir contra un conjunto etiquetado a mano.

**`avisos` era demasiado vaga.** "Notificaciones automáticas de apps y servicios"
no le decía al modelo dónde meter un código de verificación ni una alerta de
seguridad. Enumerar los casos concretos la subió de 4/23 a 16/23.

**Se añadieron reglas con prioridad y nueve ejemplos.** Un modelo de 8B aprende
mucho más de un ejemplo concreto que de una definición abstracta.

### La confianza no sirve como umbral

Medido en las dos ejecuciones:

| | confianza en aciertos | en fallos | diferencia |
|---|---|---|---|
| definiciones originales | 0,948 | 0,876 | +0,071 |
| definiciones afinadas | 0,953 | 0,920 | +0,033 |

Pedirle explícitamente al modelo que use todo el rango **empeoró** la separación.
Con una confianza media de 0,92 en respuestas equivocadas, no existe un umbral
que apruebe automáticamente lo correcto y detenga lo incorrecto.

**Consecuencia para la Fase 7**: no se puede auto-aprobar por confianza alta.
Toda propuesta pasa por decisión humana, o se busca otra señal de incertidumbre
(votación entre varios modelos, o entropía de la salida).

### `otros` tiene dos significados y eso es un problema

Por decisión de la usuaria, los boletines de contenido suscrito van a `otros`.
Pero `otros` también es la salida de escape para "el modelo duda". En la medición
del prompt v2, 7 de los 10 fallos fueron correos que acabaron en `otros` por
duda, no por ser boletines.

En el dashboard no se podrán distinguir. Pendiente de decidir: separar en una
categoría `boletines`, o llevar los digests a `avisos`.

## Revisión del 2026-08-13 (segunda): `banco` pasa a llamarse `tramites`

`banco` ya contenía cosas que de bancario no tienen nada: la ayuda de Verano
Joven del Ministerio de Transportes, la tarjeta del Bono Cultural Joven. La
definición decía "trámites" desde el principio, pero el nombre sugería otra
cosa, y un nombre que no encaja con su contenido confunde al modelo y a quien
lee el dashboard.

**`tramites`**: gestiones y papeleo. Bancos (extractos, movimientos, tarjetas,
seguros), administración pública, ayudas, subvenciones, documentación que
firmar o aportar.

Migración `c2b681487998`, escrita a mano. Alembic NO detecta el renombrado de un
valor de enum: con `--autogenerate` habría intentado borrar y recrear el tipo,
perdiendo las filas. `ALTER TYPE ... RENAME VALUE` cambia la etiqueta en el
sitio, sin tocar los datos.

### Los boletines se quedan en `otros` (decisión de la usuaria)

Se descartó crear una categoría `boletines`. Consecuencia asumida: `otros` sigue
significando dos cosas, "boletín suscrito" y "el modelo duda", y en el dashboard
no se podrán distinguir.

### Regla nueva: contenido frente a cuenta

El mismo remitente manda las dos cosas, y la frontera estaba sin definir:

- Si un servicio te habla de **contenido** (libros, artículos, retos,
  actividad de tus contactos) -> `otros`
- Si te habla de **tu cuenta** (acceso, seguridad, configuración) -> `avisos`

Ejemplo: "You finished <libro>. What's next?" de Goodreads es `otros`;
"goodreads.com: Sign-in" es `avisos`.

Reetiquetar los siete correos de Goodreads según esta regla subió el acierto
medido de qwen3 del 70,0% al 77,5% **sin tocar el modelo ni el prompt**: seis de
los fallos eran error de etiquetado, no del clasificador.

## Consecuencias

- Las definiciones de la tabla y las reglas de desempate son la fuente única del prompt de
  clasificación. Si se editan aquí, hay que regenerar el prompt.
- Cambiar la lista después de tener correos clasificados requiere migración de Alembic y
  decidir qué pasa con las filas existentes.
- En la Fase 6 (evaluación de modelos) esta lista es el conjunto de etiquetas contra el que se
  mide la precisión. Un cambio de lista invalida las comparaciones anteriores.
- Si en la Fase 9 se decide escribir estas categorías en Gmail como etiquetas, serían
  etiquetas **nuevas** creadas por MailPilot. Nunca se modifican ni se borran las etiquetas
  que la usuaria ya tenía.

## Revisión 2026-08-14 — `personal` deja de ser "quién" y pasa a ser "de qué"

Medido sobre 158 correos revisados en el dashboard, `personal` acertó 3 de 7: la
peor categoría con diferencia, y la que la usuaria declaró más importante.

Al mirar los fallos, ninguno era del modelo. La definición vieja decía **una
persona real escribiéndote**, o sea un criterio sobre el REMITENTE. La usuaria
etiqueta con un criterio sobre el ASUNTO: su vida privada.

Los tres casos que lo destaparon:

| correo | modelo | usuaria |
|---|---|---|
| Optica2000, "Recordatorio cita" | `compras` | `personal` |
| Mónica T., "GAFAS" | `trabajo` | `personal` |
| ella misma, "Re: [#21317373] Autorizaciones" | `personal` | `tramites` |

Con la definición vieja el modelo acertaba los tres: Optica2000 no es una
persona, y un correo enviado por ella misma sí lo es. El clasificador estaba
obedeciendo una especificación equivocada.

### Definición nueva

> `personal`: asuntos de tu vida privada. Familia y amigos escribiéndote, y
> salud: citas médicas, recordatorios de consulta, resultados, óptica, dentista.

### Reglas de desempate nuevas

- Una **cita o gestión de salud** es `personal` aunque la mande una empresa
  automáticamente. Gana a `avisos` y a `compras`.
- El **dinero** de la salud no: una factura del seguro o un recibo de la
  clínica es `tramites`. La frontera es cita/resultado (`personal`) frente a
  cobro (`tramites`).
- Un correo de una persona real sobre un asunto de trabajo es `trabajo`, no
  `personal`. Manda el asunto, no el remitente.
- Correos que la usuaria se envía a sí misma: mandan por asunto como cualquier
  otro. "Autorizaciones" con número de expediente es `tramites`.

### Consecuencia

Invalida la comparación con las medidas anteriores en lo que toca a `personal`.
`test3` (82,1 %) se midió con la definición vieja; el número global sigue siendo
válido como foto de aquel momento, pero no es comparable con lo que se mida a
partir de ahora en esa categoría.

## Revisión 2026-08-14 (2) — notas para uno mismo: manda la forma, no el tema

La revisión anterior quitó la regla "los correos que la usuaria se envía a sí
misma son personal" y la sustituyó por "se clasifican por su asunto". Se hizo
generalizando desde UN ejemplo, y salió mal: en `test4` el modelo mandó `cv` a
`trabajo` e `imprimir vinted` a `compras`, y `personal` cayó a 1/6.

Con las 14 etiquetas disponibles el patrón es otro:

| la usuaria dice | asuntos |
|---|---|
| `personal` (8) | `matricula`, `Imprimir martes`, `autorizacionn`, `cv`, `cv mejorado`, `(sin asunto)`, `imprimir vinted` x2 |
| `tramites` (3) | `Re: [#21317373] Autorizaciones`, `Autorización Volante - ABRIL ESPINOSA TORTUERO`, `Retraso en la emisión de tarjeta virtual` |

`autorizacionn` es `personal` y `Autorización Volante - ABRIL ESPINOSA
TORTUERO` es `tramites`. **Mismo tema, etiquetas opuestas.** Lo que los separa
no es de qué van, es si lo escribió ella de carrerilla o es un papel oficial.

### Regla

> Un correo que la usuaria se manda a sí misma, o que le reenvían, se clasifica
> por su CONTENIDO. Pero si es una NOTA suya —asunto telegráfico, en
> minúsculas, con erratas, o sin asunto— es `personal`: es un recordatorio de
> su vida, no documentación. Solo cuando lleva documentación real (expedientes,
> autorizaciones formales, redacción institucional) manda esa categoría.

### Regla derivada sobre `compras`

`compras` exige que la compra la hiciera **ella**. Una reserva que hizo otra
persona y le reenvía no es `compras`: si es un plan familiar, es `personal`.
Esto salió del "Fwd: Nueva Reserva EL TESORO DE LA MOMIA" que le reenvía su
madre.

### Lección de método

Las dos veces que se ha roto `personal` en este proyecto (v3 y v6) ha sido por
escribir una regla desde un solo ejemplo. Esta se apoya en 14 y los reproduce
todos. No es garantía, pero la diferencia de base empírica es de un orden de
magnitud.
