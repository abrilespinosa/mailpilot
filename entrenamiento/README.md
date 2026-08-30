# Entrenamiento de un clasificador propio

Aquí vive el intento de **entrenar un modelo con las etiquetas de la usuaria** y
compararlo con `qwen3:8b`, que es lo que clasifica hoy.

No sustituye al LLM por defecto: la pregunta que se responde es *cuál es mejor,
y en qué*, medida sobre el mismo conjunto.

## Qué hay y qué no se versiona

| | |
|---|---|
| `README.md` | esto |
| `dataset.json` | **gitignored**: contiene correos reales (remitente, asunto, snippet) |

Se regenera con `python scripts/construir_dataset.py`.

## De dónde salen las etiquetas

De `action_proposals.final_category`, con **dos filtros que no son negociables**:

```sql
WHERE final_category IS NOT NULL
  AND decidido_a_ciegas IS TRUE
```

El segundo es el importante. Ver la propuesta del modelo antes de decidir
empuja a darle la razón — medido en este mismo proyecto: el acierto sobre
`otros` pasa del 65,4 % al 87,5 % **solo por enseñar la propuesta**. Una
etiqueta anclada sirve para afinar un prompt, pero entrenar con ellas le
enseñaría al modelo nuevo a copiar los sesgos del viejo, sin forma de medir
cuánto.

Las 450 actuales se etiquetaron a mano, a ciegas, sobre propuestas en blanco
(`category IS NULL`: el modelo no opinó). Las 371 anteriores se descartaron
porque mezclaban etiquetas ancladas con la taxonomía vieja migrada por reglas,
y nada registraba cuál era cuál. Esa es la razón de que exista la columna
`decidido_a_ciegas`.

## La partición

**359 train · 91 test**, estratificada por categoría, semilla `20260826`.

```
                train  test
  seguridad        77    19
  boletines        62    16
  avisos           53    13
  personal         43    11
  tramites         38    10
  compras          35     9
  empleo           23     6
  promociones      16     4
  social           12     3
```

Estratificada y no al azar porque `social` tiene 15 ejemplos en total: una
partición aleatoria podía dejarla con cero en `test` —y entonces no se sabría
nada de esa categoría— o con cero en `train`, y no se aprendería en absoluto.

**El archivo no se regenera solo.** `construir_dataset.py` se niega si ya
existe; hace falta `--force` a propósito. Rehacer la partición movería correos
de `train` a `test`, y cualquier medida anterior dejaría de significar nada.

## La regla del `test`

> El `test` solo mide mientras no se mire.

En cuanto se ajusta algo viendo sus fallos, deja de ser una medición y pasa a
ser una expectativa. En la Fase 6 el `test` decía 92,5 % y el número honesto
era 73,8 %: 18,7 puntos de diferencia, porque los prompts v3 y v4 se habían
escrito mirando sus errores.

Con un modelo entrenado el riesgo es mayor, no menor: reentrenar tarda menos de
un segundo, así que mirar el `test` "solo para ver" y volver a probar es
facilísimo de hacer sin darse cuenta.

## Contra qué se compara

**No contra el azar, contra lo que ya funciona.** Hay dos referencias y hacen
falta las dos:

| Referencia | Acierto | Qué significa |
|---|---|---|
| Contestar siempre `seguridad` | 20,9 % | el suelo: un modelo que no piensa |
| `qwen3:8b` con el prompt v8 | **72,5 %** | el listón real, medido aquí |

Sin el suelo, un 60 % no dice nada. Sin el listón, un 70 % parece un éxito
cuando en realidad sería un retroceso.

El 82 % que se citó durante meses **nunca fue una referencia válida**: se midió
con la taxonomía de siete categorías y sobre otros conjuntos. Un número medido
en otro sitio no es un listón, es una anécdota. El 72,5 % de arriba sí lo es,
porque sale de estos mismos 91 correos.

## Qué se esperaba (escrito ANTES de medir)

Se deja tal cual para poder contrastarlo con los resultados de abajo. Acertó en
lo esencial —el reparto por categorías— y falló en el global: se esperaba que
el entrenado quedara claramente por debajo, y quedó empatado.

Lo más probable es que el modelo entrenado **quede por debajo del 82 % global y
aun así gane en dos o tres categorías**. Con 359 ejemplos de entrenamiento eso
es un resultado normal, no un fracaso.

- Debería ganar donde el **remitente** casi decide: `boletines`, `promociones`.
  El LLM desperdicia esa señal, porque se relee las diez definiciones y razona
  de cero con cada correo.
- Debería perder donde hace falta **entender**: `personal` (¿hay una persona
  detrás?), `tramites` (¿tiene consecuencias si no lo atiendo?).
- De `social` (3 en test) y `promociones` (4 en test) no se podrá afirmar casi
  nada. Está asumido: es cuánto correo de ese tipo hay, no un fallo del método.

Si un modelo que entrena en un segundo empata con un LLM de 8B en la mitad de
las categorías, **ese es el hallazgo** — y sugiere la arquitectura real: el
clásico resuelve lo fácil en microsegundos y solo se llama a qwen3 cuando duda.

## Los pasos

1. ~~Sacar el conjunto y congelar la partición~~ — `construir_dataset.py`
2. ~~Baseline tonto~~ · 3. ~~TF-IDF~~ · 4. ~~Entrenar~~ · 5. ~~Evaluar~~ — `entrenar.py`
6. ~~Comparar con qwen3~~ — `comparar.py`

---

# Resultados (2026-08-26)

```
  baseline tonto      20.9 %
  modelo entrenado    73.6 %    0.001 s por correo
  qwen3:8b            72.5 %    6.3   s por correo
```

**Empate.** McNemar sobre los 31 correos en que discrepan da z = 0,00: los
1,1 puntos son ruido. Un modelo de veinte líneas que entrena en menos de un
segundo iguala a un LLM de 8B, y clasifica 6.000 veces más rápido.

**El 82 % que se citaba antes NO era comparable.** Se midió con la taxonomía de
siete categorías y sobre otros conjuntos. Medido aquí, sobre correo etiquetado
a ciegas con las diez categorías actuales, qwen3 está en 72,5 %.

## Se equivocan en cosas distintas

| categoría | entrenado | qwen3 | gana |
|---|---|---|---|
| `promociones` | **0.89** | 0.42 | entrenado |
| `empleo` | **0.91** | 0.73 | entrenado |
| `social` | **0.86** | 0.50 | entrenado |
| `avisos` | **0.65** | 0.45 | entrenado |
| `boletines` | 0.67 | 0.69 | empate |
| `compras` | 0.89 | **0.95** | qwen3 |
| `personal` | 0.75 | **0.87** | qwen3 |
| `seguridad` | 0.72 | **0.85** | qwen3 |
| `tramites` | 0.62 | **0.84** | qwen3 |

El reparto es el que predecía el ADR 007: **el entrenado gana donde el
remitente casi decide** (`promociones` con solo 16 ejemplos de entrenamiento,
`social`, `empleo`), y **qwen3 gana donde hace falta entender el correo**
(`personal` — ¿hay una persona detrás?, `tramites` — ¿tiene consecuencias si no
lo atiendo?).

## El resultado que importa

```
  fallan los dos            9
  solo falla el entrenado  15
  solo falla qwen3         16
  ────────────────────────────
  techo si se combinan   90.1 %
```

Solo 9 de 91 correos se les resisten a los dos. Un árbitro que eligiera siempre
al que acierta llegaría al **90,1 %**, frente al 73,6 % del mejor por separado:
**16,5 puntos de margen**. Es la justificación medida de la arquitectura
híbrida — el clásico resuelve en microsegundos lo que decide el remitente, y
solo se llama a qwen3 cuando hace falta leer.

## Dos lecciones de método

**La validación cruzada acertó el resultado sin gastar el `test`.** Predijo
71,3 % y el test dio 73,6 %. Se puede iterar todo lo que haga falta sobre
`train` y reservar el `test` para el final.

**La distancia train-test no es lo que hay que optimizar.** El modelo acierta
93,3 % en `train` y 73,6 % en `test`: 19,7 puntos de "sobreajuste". Pero
regularizar más lo empeora en todos los tramos —bajar a C=0.1 deja la
validación cruzada en 66,3 % y subir a C=10 la mejora a 74,1 %, con el train
al 99,2 %—, así que la distancia era descriptiva, no un problema. Lo que
importa es el acierto sobre datos no vistos, y ahí memorizar más no hizo daño.
(La diferencia entre C=1 y C=10 cae dentro del ±5,2 % de la validación
cruzada, así que tampoco es una mejora: la conclusión es que la regularización
no es la palanca.)

## Límites, para no leer de más

- **91 correos de test.** Diferencias menores de ~10 puntos no se distinguen
  del ruido. El empate entre los dos modelos es literalmente un empate.
- **`social` (3 en test) y `promociones` (4)** no permiten afirmar nada por
  categoría, por buenos que se vean sus F1.
- **El techo del 90,1 % es un techo**, no un resultado: supone un árbitro
  perfecto que todavía no existe. Construirlo es el siguiente problema.

---

# Segunda ronda (2026-08-27)

Con 109 etiquetas nuevas a ciegas (559 en total). **El `test` de 91 se mantuvo
congelado**, así que las tres cifras son comparables entre sí: mismo examen,
distinto material de estudio.

| Entrenamiento | Validación cruzada | Test |
|---|---|---|
| 359, sin cuerpo | 71,3 % ± 5,2 | 73,6 % |
| 468, sin cuerpo | 72,4 % ± 4,1 | 71,4 % |
| **468, con cuerpo** | **75,0 % ± 3,2** | **75,8 %** |

## Más datos no sirvieron de nada

Un 30 % más de ejemplos de entrenamiento y el test **bajó** 2,2 puntos. Sobre
91 correos eso son 2 correos: ruido. Pero la conclusión práctica es firme —
**etiquetar más del mismo tipo no era el cuello de botella**.

Dato que lo refuerza: `social` pasó de 15 ejemplos a 16 en 109 etiquetas
nuevas. A ese ritmo, llegar a 50 exigiría ~1.700 etiquetas más. No hay tanto
correo de redes sociales en esta bandeja, y ninguna cantidad de trabajo lo
arregla.

## El cuerpo del correo sí sirvió

+4,4 puntos en el test y +2,6 en validación cruzada, con la desviación
estrechándose de ±5,2 a ±3,2. Que suban las dos medidas a la vez lo hace más
creíble que los 4 puntos del test solos.

Y la mejora cae donde se predijo:

```
seguridad   0,56 -> 0,67   +0,11
avisos      0,50 -> 0,59   +0,09
boletines   0,67 -> 0,73   +0,06
```

**Por qué tenía que ser ahí.** Con asunto y remitente, esta frontera es
invisible:

    "Confirma tu cuenta en Club·by"   -> seguridad
    "Welcome to Supabase"             -> avisos

Las palabras se solapan —cuenta, confirmar, activar, welcome— y por fuera son
casi el mismo correo. Por dentro no: uno lleva un enlace de verificación, el
otro consejos de uso. El snippet trae 181 caracteres de media; el cuerpo,
1.049.

**No queda resuelto**: de los 7 casos concretos que fallaban, 3 se arreglaron y
4 siguen mal. Señal de que el modelo por fin mira el cuerpo: los dos correos
"amazon.com: Sign-in" —asunto idéntico— ahora dan resultados distintos.

## Dónde vive el cuerpo, y dónde no

`entrenamiento/cuerpos.json`, gitignored, y en ningún otro sitio.

- **No va a PostgreSQL.** La ingestión sigue pidiendo `format="metadata"` y la
  tabla `emails` no tiene columna de cuerpo. Esa decisión no ha cambiado.
- **No va a git.** La carpeta entera está ignorada salvo este README, con lista
  negra y excepción: un archivo nuevo queda fuera por defecto.
- Es un archivo de trabajo borrable. Existe solo para no repetir 559 llamadas
  a Gmail en cada reentrenamiento; `rm entrenamiento/cuerpos.json` y a otra
  cosa.

**Prompt injection: aquí no aplica.** Darle el cuerpo al LLM abriría esa
superficie, porque un correo podría intentar darle instrucciones. Al modelo
entrenado no le puede pasar: TF-IDF no lee instrucciones, cuenta palabras. Un
correo que diga «ignora tus reglas» solo aporta las palabras «ignora» y
«reglas» a un vector.

## El `test` está gastado

Se ha mirado varias veces el 2026-08-27: la matriz de confusión, los errores
concretos, y qué casos se arreglaron. **Cualquier ajuste a partir de aquí
necesita un conjunto nuevo etiquetado a ciegas.** Con 559 etiquetas ya hay
material de sobra para partir uno distinto.

---

# Tercera ronda (2026-08-29) — el test de generación 2

199 etiquetas nuevas a ciegas (758 en total), test viejo jubilado. **Las dos
mediciones se hundieron a la vez.**

| | test gen 1 (91) | test gen 2 (199) | |
|---|---|---|---|
| modelo entrenado | 75,8 % | **60,3 %** | −15,5 |
| qwen3:8b (v8) | 72,5 % | **54,3 %** | −18,2 |

## Por qué esto no es "mi modelo ha empeorado"

**qwen3 es el control, y eso es lo que hace interpretable la caída.** qwen3 no
aprende nada: mismo modelo, mismo prompt, función fija. Si el mismo instrumento
marca 72,5 y luego 54,3, lo que cambió no es el instrumento, **son los correos**
(p ≈ 0,002).

De ahí se sigue algo que no se esperaba: la hipótesis obvia —"TF-IDF memorizó
remitentes viejos y no generaliza"— es **falsa**. El modelo entrenado cayó
MENOS que el patrón fijo. Aguantó mejor que el listón.

Tener un modelo que no aprende midiendo al lado del que sí vale más que
cualquier validación cruzada: es lo único que distingue "mi modelo ha
empeorado" de "el examen es más difícil".

## El test es una rodaja de tiempo, no una muestra

`crear_propuestas_en_blanco` ordena por `received_at.desc()`, así que cada tanda
son los N más recientes sin preparar. Siete de nueve categorías salen con la
proporción de `train` casi clavada, pero **`empleo` (6,1 % de train) y `social`
(2,9 %) salen a CERO**. Para `empleo` no es azar: P(0 de 199) ≈ 4 en un millón.
En el correo reciente ya no llegan ofertas de empleo.

**Esto es una virtud, no un defecto**, y conviene no "arreglarlo". Un test
posterior en el tiempo a `train` es un *holdout temporal*, que es el diseño
correcto para la pregunta real: ¿cuánto acertará con el correo que llegue
mañana? La validación cruzada dentro de `train` da 77,8 % ± 1,1 contra 60,3 %
en el test — **17,5 puntos de hueco** que miden exactamente cuánto engaña una
partición al azar. El 75,8 % de la generación 1 nunca midió correo nuevo: su
test era una muestra al azar del mismo periodo que su train.

Lo que sí falta es que el informe diga de qué categorías no puede hablar. Eso
ya está: `--nuevo-test` avisa de las categorías con menos de 5 ejemplos o
ninguno.

## Por qué el correo reciente es más difícil: NO SE SABE

Lo sólido es que lo es. No inventar la explicación en la próxima sesión.

---

# Cuarta ronda (2026-08-30) — el árbitro

Ver el ADR 008 para la decisión. Aquí van las dos cosas que se aprendieron
midiendo, incluida la hipótesis que se cayó.

## Lo que se esperaba (escrito ANTES de medir)

Que la confianza del modelo entrenado sirviera de señal para ceder: si duda,
que conteste qwen3. La predicción concreta era que qwen3 rendiría por encima de
su media en el correo dudoso, porque son correos raros y un LLM razona.

## Lo que salió: la confianza está calibrada, y aun así la regla falla

**Primera confianza calibrada de todo el proyecto.**

| | separación aciertos−fallos |
|---|---|
| qwen3, cuatro mediciones | +0,004 a +0,019 |
| modelo entrenado, fuera de partición | **+0,266** |

| confianza | n | acierto |
|---|---|---|
| 0,0–0,3 | 45 | 35,6 % |
| 0,3–0,4 | 69 | 56,5 % |
| 0,4–0,5 | 84 | 63,1 % |
| 0,5–0,6 | 66 | 69,7 % |
| 0,6–0,7 | 73 | 87,7 % |
| 0,7–0,8 | 47 | 91,5 % |
| **0,8–1,0** | **175** | **99,4 %** |

Monótona, sin un escalón hacia atrás. Un tercio del correo va con 99,4 % de
acierto.

**Y la predicción era falsa.** En los 264 correos donde el entrenado duda:

```
modelo entrenado  58,3 %
qwen3:8b          50,4 %
```

qwen3 no rinde por encima de su media en el correo difícil: rinde **por
debajo**. Su 54,3 % global era el promedio de acertar mucho en lo fácil. La
intuición "si dudo, pregunto a quien razona" es fuerte y es falsa.

## Lo que sí funciona

Condicionar por lo que qwen3 **dice**, no por lo dudoso que sea el correo. Test
pareado (McNemar exacto) sobre los 559 de `train`:

| qwen3 dice | arregla | rompe | p |
|---|---|---|---|
| **`seguridad`** | **10** | **0** | **0,002** |
| `tramites` | 10 | 4 | 0,180 |
| `avisos` | 7 | 24 | 0,003 |
| `promociones` | 2 | 14 | 0,004 |

Árbitro (solo `seguridad`): **79,6 %** sobre `train`, +1,8.

Tiene mecanismo, que es lo que lo hace creíble: `seguridad` se define por lo que
el correo te PIDE hacer, y "confirma tu cuenta" comparte casi todo su
vocabulario con "bienvenido a". Es la misma frontera donde meter el cuerpo dio
la mayor mejora al entrenado (+0,11).

## La lección de método

La validación anidada daba **80,0 % ± 5,6** —invisible, ruido— y el test
pareado da **p = 0,002** sobre los mismos datos. Los dos sistemas ven los MISMOS
correos y coinciden en casi todos: comparar porcentajes globales tira la
información justo donde está. Con lotes de este tamaño, comparar en pareado o
no ver nada.

## Qué falta

El número honesto. Todo esto se eligió mirando `train`, y el gen 2 está gastado.
La generación 3 bloquea dos cosas: medir el árbitro y decidir si `tramites`
entra.
