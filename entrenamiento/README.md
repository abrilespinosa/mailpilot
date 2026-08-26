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
