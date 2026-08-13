# Evaluación del clasificador

Mide qué porcentaje de correos clasifica bien el modelo, para poder comparar
prompts y modelos con datos en vez de con impresiones.

## Por qué esta carpeta está casi vacía en el repositorio

`labels.json` y `runs/` están en `.gitignore`: contienen remitentes y asuntos
reales de la bandeja. Este repositorio será público, así que los datos
personales se quedan en la máquina.

Lo que sí se versiona son los scripts que los generan y esta explicación.

## Cómo reconstruirlos

```bash
python scripts/ingest.py --limit 80      # traer correos
python scripts/build_labels.py           # crear labels.json con propuestas
# revisar labels.json a mano y corregir el campo "expected"
python scripts/evaluate.py --name baseline
```

## Sobre las etiquetas

`build_labels.py` rellena `expected` con una propuesta, pero **la propuesta no
es la verdad**. Es la bandeja de la usuaria y su criterio el que decide qué es
correcto. Los casos donde el ADR 001 no decide con claridad llevan un campo
`revisar` con la duda concreta.

Cuando una de esas dudas se resuelva, la respuesta debe acabar en el ADR 001,
no solo en el archivo de etiquetas: si la definición de una categoría es
ambigua para una persona, también lo es para el modelo.

## Cómo leer los resultados

- **Acierto**: el porcentaje global. Útil para comparar, insuficiente para
  entender.
- **Matriz de confusión**: filas la categoría correcta, columnas la predicha.
  La diagonal son los aciertos. Lo de fuera dice *qué se confunde con qué*, que
  es lo que indica dónde tocar el prompt.
- **Confianza en aciertos vs en fallos**: si los dos números se parecen, la
  confianza no informa y no puede usarse como umbral para aprobar nada
  automáticamente.
