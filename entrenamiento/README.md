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
| Contestar siempre `seguridad` | ~21 % | el suelo: un modelo que no piensa |
| `qwen3:8b` con el prompt v8 | ~82 % | el listón real |

Sin el suelo, un 60 % no dice nada. Sin el listón, un 70 % parece un éxito
cuando en realidad sería un retroceso.

## Qué esperar

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
2. Medir el baseline tonto
3. Vectorizar con TF-IDF (remitente + dominio + asunto + snippet)
4. Entrenar la regresión logística
5. Evaluar sobre el `test`: acierto, precisión y recall por categoría, matriz
   de confusión, y la distancia con el acierto en `train` (sobreajuste)
6. Comparar con `qwen3:8b` sobre el mismo `test`
