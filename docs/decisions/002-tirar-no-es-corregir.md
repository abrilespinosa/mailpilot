# ADR 002 — Tirar un correo y clasificarlo son dos decisiones distintas

Fecha: 2026-08-14
Estado: aceptado e implementado en la Fase 9. La tabla `gmail_actions` existe, y
`GmailActionType` pasó de dos valores a tres al añadir `restore_from_trash`.

## Contexto

La Fase 9 añade la única acción destructiva del proyecto: mover a papelera.

La usuaria lo dice claro:

> muchos clasificados como promociones u otros los eliminaré porque no los
> quiero pero no significa que estén mal categorizados

Es decir: **"esto es promociones" y "esto lo tiro" son dos afirmaciones
independientes**, y la segunda no contradice a la primera. Un correo puede
estar perfectamente clasificado y aun así sobrar.

Hoy el dashboard solo tiene un eje. Decidir es elegir categoría, o descartar
la propuesta. Si en la Fase 9 el botón de papelera se colgara de ese mismo
eje —por ejemplo reutilizando `rejected`, o metiendo `trashed` como un estado
más— pasarían dos cosas, las dos malas.

### 1. Se perdería la etiqueta

`rejected` deja `final_category` a NULL. Tirar un correo por ese camino
borraría la única información que dice qué era. Cada correo tirado sería una
etiqueta perdida, y son etiquetas conseguidas con uso real.

### 2. El acierto medido subiría solo, y sería mentira

Medido sobre los 158 correos revisados hasta hoy:

| | acierto |
|---|---|
| contando todo | 132/158 = **83,5 %** |
| si lo tirado saliera del cálculo | 79/91 = **86,8 %** |

**+3,3 puntos de espejismo**, y desaparecería el **42 % de la muestra**.

No es un sesgo cualquiera: se llevaría por delante precisamente `promociones`
y `otros`, que son la categoría más numerosa y la que peor acierta (76 %). El
número subiría justo por perder de vista los casos difíciles. Es el mismo
error de la Fase 6 —medir sobre un conjunto que ya no representa el problema—
por una puerta nueva.

## Decisión

**Dos ejes ortogonales por correo. Ninguno puede pisar al otro.**

| eje | pregunta | dónde vive |
|---|---|---|
| clasificación | ¿qué es esto? | `category` (IA) y `final_category` (usuaria) |
| acción | ¿qué hago con ello? | `proposed_action` + su propio estado |

Reglas que se derivan:

1. **Tirar un correo exige haberlo categorizado.** La papelera no es una
   categoría ni sustituye a ninguna. En la interfaz son dos gestos.
2. **El acierto se calcula SIEMPRE sobre el eje de clasificación**, con todos
   los correos que tengan `final_category`, se hayan tirado o no. Que un correo
   esté en la papelera no lo saca de la evaluación.
3. **`rejected` no es la papelera.** Sigue significando "no apliques nada, no
   me pronuncio". Son cosas distintas y deben poder distinguirse.
4. Un correo tirado **conserva su fila y su categoría**. En Gmail la papelera
   es reversible 30 días; en MailPilot el registro no se borra nunca.

## Alternativas descartadas

- **`trashed` como un valor más de `ProposalStatus`**: rechazada. Es lo que
  provoca exactamente el problema de arriba: mete la acción en el campo que
  describe la clasificación, y los dos ejes dejan de poder leerse por separado.
- **Una categoría `basura`**: rechazada. Convertiría "no lo quiero" en una
  respuesta al "¿qué es esto?", que es otra pregunta. Además contaminaría el
  enum que valida la salida del LLM, y ese enum es una barrera de seguridad,
  no una lista de preferencias.
- **Deducir qué tirar a partir de la categoría** ("todo lo que sea
  promociones, a la papelera"): rechazada. Rompe el principio del proyecto: la
  IA propone, la persona decide. Una regla así ejecutaría acciones destructivas
  sin que nadie mire cada caso.

## Consecuencias

- Hace falta una tabla o unas columnas nuevas para el eje de acción, con su
  migración de Alembic. Se diseñará al empezar la Fase 9.
- `estadisticas()` y `correcciones()` en `repository.py` **no deben cambiar**
  cuando llegue la papelera: siguen leyendo solo el eje de clasificación. Si
  alguna vez hay que tocarlas para tener en cuenta lo tirado, es señal de que
  los ejes se han mezclado.
- El dashboard necesitará un gesto aparte para la papelera, separado
  visualmente de los siete chips de categoría.
- Escalar el scope de OAuth a `gmail.modify` sigue siendo una decisión propia
  y pendiente, que se documentará en su ADR cuando toque.
- El borrado permanente sigue fuera del alcance del proyecto. Lo único
  destructivo permitido es mover a papelera.


## Revisión 2026-08-14 — recuperar de la papelera

La papelera dejó de ser un camino de ida. Se añadió `restore_from_trash` al
enum de acciones, que pasa de dos valores a tres.

**Ampliar ese enum es lo que este proyecto intenta que cueste**, así que la
justificación tiene que estar escrita: es la única de las tres acciones que
**no quita nada**. Deshace. Un bug en `apply_label` puede desetiquetar algo y
uno en `move_to_trash` puede tirar lo que no toca; el peor fallo posible en
`restore_from_trash` es sacar de la papelera un correo que sobraba.

El test `test_las_acciones_posibles_son_exactamente_estas` fija la lista, así
que hubo que ir a cambiarlo a mano. Es el comportamiento buscado: ampliar lo
que MailPilot puede hacerle a una cuenta no puede colarse en un commit sin que
nadie lo mire.

### Recuperar son DOS acciones, no una

Porque en Gmail son dos cosas distintas:

1. `untrash` — quita la etiqueta TRASH. **No devuelve el correo a Recibidos**:
   Gmail le quitó INBOX al tirarlo. Sin más, el correo saldría de la papelera y
   quedaría archivado, imposible de encontrar.
2. `apply_label` — le devuelve su categoría.

La segunda importa más de lo que parece en los correos que se tiraron a mano
en Gmail **antes** de que se aplicara su etiqueta: sin ella reaparecerían sin
clasificar, aunque la decisión lleve semanas guardada en la base de datos.

### Volver a Recibidos, pero solo si se estaba

Se decide mirando `raw_labels`, que es la foto de justo antes de tirarlo: un
correo en la papelera desaparece de la ingestión, así que ese campo nunca se
sobrescribió.

Recuperar un correo que ya estaba archivado y desarchivarlo de propina sería
hacer más de lo que nadie pidió, y hacer de más también es un error.
