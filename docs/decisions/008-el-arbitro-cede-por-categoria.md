# ADR 008 — El árbitro cede por categoría, no por confianza

Fecha: 2026-08-30
Estado: aceptado

## Contexto

El ADR 007 dejó dos clasificadores que **se equivocan en correos distintos**.
Medido sobre el test de la generación 2 (199 correos etiquetados a ciegas):

```
de acuerdo       82 (41 %)   y ahí aciertan el 85,4 %
en desacuerdo   117 (59 %)   solo el entrenado 50, solo qwen3 38,
                             ninguno de los dos 29
```

Un árbitro que siempre supiera a quién creer llegaría al **79,4 %** frente al
60,3 % del mejor por separado. Ese margen es la razón de este ADR. El 79,4 % es
un techo teórico, no una expectativa: supone resolver justamente el problema.

## Decisión

**Ceder a qwen3 cuando dice `seguridad` y el modelo entrenado discrepa. En todo
lo demás, mandar el modelo entrenado.**

La lista de categorías que ceden es un conjunto cerrado
(`arbitro.CEDER_A_QWEN3`) y hay un test que fija sus valores exactos, igual que
con `GmailActionType`: ampliarla obliga a venir a cambiarla a mano.

## Alternativas consideradas

### Umbral de confianza — PROBADA Y DESCARTADA

Era la hipótesis de partida, y tenía buen aspecto. La confianza del modelo
entrenado **sí está calibrada**, al contrario que la de qwen3:

| | separación aciertos−fallos |
|---|---|
| qwen3, cuatro mediciones | +0,004 a +0,019 |
| modelo entrenado, fuera de partición | **+0,266** |

Y sube monótona por tramos: 35,6 % de acierto por debajo de 0,3 de confianza y
99,4 % por encima de 0,8. La regla se escribía sola: si el entrenado duda, que
conteste qwen3.

**La mató un solo número.** En los 264 correos de `train` donde el entrenado
duda (confianza < 0,60):

```
modelo entrenado  58,3 %
qwen3:8b          50,4 %
```

qwen3 es **peor** justo donde hacía falta que fuera mejor. Su 54,3 % global
nunca fue su nota en el correo difícil: era el promedio de acertar mucho en lo
fácil. No hay bolsa que ceder. La validación anidada lo confirma: 76,6 % ± 5,0
contra el 77,8 % de no hacer nada.

Es un resultado que merece quedarse escrito, porque la intuición ("si dudo,
pregunto a quien razona") es fuerte y falsa.

### Meta-modelo apilado (*stacking*) — NO INTENTADA, A PROPÓSITO

Un tercer modelo que aprendiera a arbitrar a partir de las dos predicciones y
las probabilidades. Es la respuesta de manual y sigue disponible como plan B.

No se ha hecho porque una regla de una línea ya captura el efecto, y con 559
ejemplos un meta-modelo tiene más formas de memorizar que de aprender. Este
proyecto ya sabe lo que cuesta leer ruido y llamarlo mejora.

### Ceder también en `tramites` — FUERA, DE MOMENTO

10 aciertos contra 4 fallos por su cuenta, p = 0,18. Sugerente y nada más.
Apoyar un componente que no se sostiene solo en otro que sí es exactamente cómo
se cuela el ruido. Lo decide la generación 3.

## Por qué `seguridad`, y por qué es creíble

Test pareado (McNemar exacto) sobre los 559 de `train`, con predicciones fuera
de partición:

| qwen3 dice | arregla | rompe | p |
|---|---|---|---|
| **`seguridad`** | **10** | **0** | **0,002** |
| `tramites` | 10 | 4 | 0,180 |
| `avisos` | 7 | 24 | 0,003 |
| `promociones` | 2 | 14 | 0,004 |

Tres cosas lo separan de una casualidad:

1. **Sobrevive a la corrección por comparaciones múltiples.** Se miraron diez
   categorías; 0,002 × 10 = 0,02, todavía por debajo de 0,05.
2. **Hay significación en las DOS direcciones.** Ceder en `avisos` rompería 24
   correos y arreglaría 7. Si la tabla fuera ruido, no habría efectos fuertes de
   signo contrario.
3. **Hay mecanismo, que es lo que más pesa.** `seguridad` se define por *"¿va de
   acceder a una cuenta mía?"*, una pregunta sobre lo que el correo te PIDE
   hacer. TF-IDF cuenta palabras, y "confirma tu cuenta" comparte casi todo su
   vocabulario con "bienvenido a". Es **la misma frontera** donde meter el
   cuerpo del correo dio la mayor mejora al modelo entrenado (+0,11 en
   `seguridad`, ADR 007). Dos señales independientes apuntando al mismo sitio.

## Consecuencias

**El árbitro cuesta una llamada al LLM, no ninguna.** Como la regla depende de
lo que diga qwen3, hay que preguntarle. Se pierde parte de la ventaja de
velocidad del modelo entrenado (0,001 s frente a 6,3 s) en el correo que se
consulte. La regla actual no necesita consultarlo todo: basta con el correo
donde el entrenado no dice ya `seguridad`.

**El número honesto no existe todavía.** Todo esto se eligió mirando `train`.
El test de la generación 2 está gastado —se le miraron los desacuerdos por
categoría para diseñar esto—, así que no puede juzgar un diseño sacado de él.
La generación 3 bloquea ahora dos cosas: medir el árbitro y decidir sobre
`tramites`.

**Hallazgo colateral, y vale más que el árbitro.** La confianza del modelo
entrenado está calibrada, y el 31 % del correo cae por encima de 0,8 con un
99,4 % de acierto. Desde la Fase 5 constaba que no se podía auto-aprobar por
confianza; con qwen3 sigue siendo cierto, con el modelo entrenado no. Eso es
una capacidad de producto —un tercio de la bandeja que podría no pasar por las
manos de la usuaria— y está sin explotar. No entra en este ADR porque es una
decisión de producto, no de arquitectura.

**Lección de método que se lleva el proyecto.** La validación anidada daba
80,0 % ± 5,6 (invisible, ruido) y el test pareado da p = 0,002 sobre los mismos
datos. Los dos sistemas ven los MISMOS correos y coinciden en casi todos, así
que comparar porcentajes globales tira la información justo donde está. Con
lotes de este tamaño: comparar en pareado, o no ver nada.
