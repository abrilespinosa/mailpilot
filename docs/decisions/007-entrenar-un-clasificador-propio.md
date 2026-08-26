# ADR 007 — Entrenar un clasificador propio, y resetear las etiquetas para poder hacerlo

Fecha: 2026-08-26
Estado: aceptado

## Contexto

Cuatro versiones de prompt (v4 a v8) dejaron el acierto clavado en el 82 %. La
Fase 6 concluyó que la meseta era de la especificación, y el ADR 006 atacó esa
parte partiendo `otros` en `boletines` / `social` / `seguridad`. Funcionó
—`otros` cayó del 14 % al 2 %— pero el global no se movió.

Lo que cambió fue **la forma del error**. Antes los fallos venían en grupos, y
un grupo se caza con una regla: el v5 arregló `trabajo` de 7/13 a 13/13, el
ADR 006 se llevó los boletines de Goodreads. Medido sobre 38 correos reales tras
el v8, los 7 errores restantes eran **siete confusiones distintas, ninguna
repetida**.

Sin grupos no hay regla que escribir. El error disperso es lo que queda cuando
el criterio es correcto pero la entrada es demasiado pobre para aplicarlo: el
modelo ve remitente, asunto y un `snippet` de ~180 caracteres, vacío en 146 de
los 2.498 correos.

Además, el LLM **desperdicia la señal más fuerte que hay**. Cada vez que llega
un correo de `goodreads.com` se relee las diez definiciones y razona de cero. Un
modelo entrenado aprende ese dominio una vez.

## Decisión

Entrenar un clasificador clásico (TF-IDF + regresión logística) con las
etiquetas de la usuaria, y **compararlo con `qwen3:8b` sobre el mismo conjunto
de prueba**.

No sustituye al LLM por decreto. La pregunta que se responde es cuál es mejor y
en qué, con datos.

### Y para poder hacerlo, resetear las etiquetas

Las 371 etiquetas acumuladas **no servían para entrenar**, y el motivo no era
que fueran pocas:

- Unas se decidieron viendo la propuesta del modelo y otras a ciegas.
- Otras venían de la taxonomía de siete categorías, migrada por reglas de
  remitente.
- **Nada registraba cuál era cuál.**

El anclaje está medido en este proyecto: el acierto sobre `otros` pasa del
65,4 % al 87,5 % solo por enseñar la propuesta antes de decidir. Entrenar con
una mezcla así le enseñaría al modelo nuevo a imitar los sesgos del viejo, sin
forma de saber cuánto.

Se reseteó: 460 correos limpiados en Gmail, 1.292 clasificaciones y propuestas
borradas, y 450 correos etiquetados a mano desde cero, a ciegas, sobre
propuestas **en blanco** (`category IS NULL`, el modelo no opina).

## Alternativas consideradas

**Otra versión del prompt (v9).** Descartada: es lo que se lleva haciendo cuatro
veces sin mover el global, y cada intento quema un conjunto de evaluación. Sin
errores agrupados no hay regla que escribir.

**Darle el cuerpo del correo al LLM.** No descartada, aplazada. Multiplica por
cuatro el tiempo de inferencia (5,7 s → ~20 s por correo, y quedan 1.800 sin
clasificar) y abre una superficie de prompt injection que hoy no existe. Merece
su propio ADR.

**Fine-tuning de qwen3:8b con LoRA.** Descartada. Ollama no entrena, haría falta
MLX aparte, y 359 ejemplos son poquísimos para afinar un modelo de 8B: el riesgo
real es olvido catastrófico. Coste alto, beneficio dudoso, y como ejercicio de
aprendizaje enseña menos que construir el clasificador entero.

**Conservar las 371 etiquetas viejas y añadir las nuevas.** Descartada, y es la
decisión más cara del ADR. Habría dado 800 ejemplos en vez de 450, pero
mezclados con etiquetas de procedencia desconocida: un conjunto más grande y
menos fiable, sin forma de separar la parte buena. Más datos no compensa no
saber de dónde vienen.

## Consecuencias

**Existe `action_proposals.decidido_a_ciegas`.** Nullable a propósito: `NULL`
significa "no consta", que es distinto de `False` ("se decidió viendo la
propuesta"). Las filas anteriores a la columna no lo saben, y eso hay que poder
expresarlo sin inventarse el dato.

**`category IS NULL` marca las etiquetas 100 % humanas.** Las propuestas en
blanco reutilizan toda la maquinaria existente —los chips, `decidir_propuesta`,
el audit log— sin interfaz nueva: como ningún chip queda marcado como propuesta
del modelo, cada clic entra por `modify`.

**Se perdieron 371 etiquetas y semanas de revisión.** Asumido: no eran
utilizables para lo que hacen falta ahora.

**El conjunto es pequeño.** 359 train / 91 test. De `social` (3 en test) y
`promociones` (4) no se podrá afirmar casi nada. Crece según se etiqueta.

**La partición se congela en disco.** Rehacerla movería correos de `train` a
`test` e invalidaría cualquier medida anterior, así que el script se niega a
regenerarla sin `--force`.

**Una dependencia nueva: scikit-learn.** Justificada por el criterio del
proyecto — problema (meseta del 82 % por falta de información), solución (usar
las etiquetas propias), alternativa simple (otro prompt, ya agotada), coste (una
dependencia de desarrollo, entrenamiento de menos de un segundo, sin GPU),
beneficio de aprendizaje (train/test, estratificación, baseline, matriz de
confusión, sobreajuste, precisión frente a recall).

**Hallazgo colateral que vale por sí solo**: la categoría predice si la usuaria
tira el correo. `promociones` 20/20 a la papelera, `social` 87 %, `empleo` 86 %,
frente a `compras` 0/44 y `tramites` 0/48. Eso valida la taxonomía del ADR 006
mejor que cualquier porcentaje de acierto, y abre la puerta a proponer papelera
automáticamente — con aprobación humana, como todo lo demás.
