# ADR 006 — De siete categorías a diez, definidas por una pregunta comprobable

Fecha: 2026-08-17
Estado: aceptado

## Contexto

Con 738 correos etiquetados a mano aparecieron dos síntomas a la vez, y son el
mismo problema visto desde los dos lados:

1. **La usuaria dudaba al etiquetar.** No con casos raros: con correos normales
   que no sabía dónde meter.
2. **El modelo se atascó en el 82 %** y cuatro versiones de prompt (v4 a v7) no
   lo movieron. z = 0,07 entre el v5 y el v7: ruido.

La Fase 6 ya había concluido que la meseta era de la especificación y no del
prompt. Lo que faltaba era saber **de qué parte** de la especificación.

### Lo que dijeron los datos

Las etiquetas reales de la usuaria, no un conjunto de evaluación:

| categoría | correos | qué había dentro |
|---|---|---|
| `avisos` | 229 (31 %) | seguridad de cuentas + redes sociales + notificaciones de apps |
| `otros` | 147 (20 %) | Goodreads 46, Substack 24, LeetCode 6… y "no sé" |
| `trabajo` | 80 (11 %) | 75 de portales de empleo. Ni un correo de trabajo real |

Y las 122 correcciones acumuladas se concentraban en una sola frontera:
`promociones` → `otros`, 35 veces.

**Diagnóstico: las categorías estaban definidas por tema, y los temas no
tienen bordes.** `avisos` no era una categoría, era un cajón con tres cosas que
no se parecen en nada ni se tratan igual. `otros` significaba a la vez "boletín
al que me suscribí" y "el modelo no sabe", así que el modelo lo usaba como
salida de emergencia y nadie podía distinguir un `otros` legítimo de una
rendición.

## Decisión

Diez categorías, y **cada una definida por una pregunta con respuesta
comprobable**, no por un tema.

| categoría | la pregunta que decide |
|---|---|
| `personal` | ¿lo ha escrito una persona, para mí? |
| `seguridad` | ¿va de acceder a una cuenta mía? |
| `tramites` | ¿tiene consecuencias si no lo atiendo? |
| `compras` | ¿es de algo que ya compré? |
| `empleo` | ¿va de conseguir trabajo? |
| `boletines` | ¿me suscribí yo a esto? |
| `social` | ¿es actividad de una red social? |
| `avisos` | ¿un servicio que uso me notifica algo operativo? |
| `promociones` | ¿me quiere vender algo **ahora**? |
| `otros` | solo "no encaja en ninguna" |

Respecto a las siete anteriores: tres nuevas (`seguridad`, `boletines`,
`social`), una renombrada (`trabajo` → `empleo`), dos estrechadas (`avisos` y
`otros`), cuatro intactas.

### Por qué la pregunta y no la definición

Una definición ("correos de tipo aviso") se puede leer de diez maneras. Una
pregunta con respuesta comprobable —"¿me suscribí yo a esto?"— tiene la misma
respuesta para la usuaria y para el modelo. Es lo que permite que la frontera
que sangraba, `promociones` ↔ `boletines`, deje de ser un juicio de tono y pase
a ser un hecho verificable.

### `otros` pasa a ser una métrica de salud

Al significar solo "no encaja", su frecuencia deja de ser un resultado normal y
pasa a ser **la señal de que la taxonomía falla**. Objetivo: por debajo del 5 %.
Si sube, es que falta una categoría, y eso ahora se puede ver.

## Alternativas consideradas

**Seguir tocando el prompt.** Es lo que se llevaba haciendo desde el v3 y lo que
la Fase 6 declaró agotado. Cada versión movía errores de sitio y quemaba un
conjunto de evaluación.

**Partir solo `otros`** (la propuesta inicial). Se quedaba corta: arreglaba una
frontera y dejaba `avisos`, la categoría más grande, siendo un cajón.

**Un eje de prioridad en vez de tema** (`requiere_accion` como booleano). Es
mejor idea de lo que parece y resuelve el "¿qué hago con esto?", pero es un eje
distinto, no un sustituto. Queda anotada como mejora futura, no como reemplazo:
la bandeja se quiere ordenada por tema.

**Menos categorías.** Habría reducido las fronteras, pero el problema no era el
número: era que las que había no tenían borde.

## Consecuencias

**Va a bajar el porcentaje de acierto, y no es un fracaso.** Con diez
categorías hay más formas de equivocarse que con siete. La comparación
82,5 % → lo que salga **no es válida**: son taxonomías distintas. Hace falta una
medición nueva desde cero, y el criterio de éxito no es el porcentaje global
sino que la usuaria deje de dudar y que `otros` baje del 5 %.

**Reetiquetado.** De los 455 correos vivos etiquetados: 170 se quedan igual, 122
se migran solos por remitente y ~163 hay que repasarlos a mano. Los 283 que
están en la papelera se dan por perdidos a propósito: ya están decididos y no
aportan.

**`empleo` es un rename, no una categoría nueva.** PostgreSQL soporta
`ALTER TYPE ... RENAME VALUE`, así que los 80 correos de `trabajo` siguen
válidos sin tocar ni una fila.

**Dos migraciones, no una.** En PostgreSQL un valor nuevo de enum no se puede
usar en la misma transacción en que se crea. La primera migración crea los
valores; la segunda mueve datos.

**Etiquetas nuevas en Gmail**: `Seguridad`, `Boletines`, `Social`, y `Empleo`
sustituyendo a `Trabajo`. La etiqueta `Trabajo` que ya existe en la cuenta se
queda huérfana; se borra a mano desde Gmail, MailPilot no borra etiquetas.

**`NUESTRAS_ETIQUETAS` crece de 7 a 10.** Es la lista blanca de lo que se puede
quitar de un mensaje, así que crece una frontera de seguridad. Sigue derivada
del enum, que es lo que impide que se cuele algo ajeno.

**Todas las mediciones anteriores quedan obsoletas.** `dev`, `test`, `test2` en
`labels.json` están etiquetados con las siete viejas. Sirven para historia, no
para comparar.
