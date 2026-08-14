# Assets del dashboard

Todo lo que haya en esta carpeta se sirve tal cual en `/static/<archivo>`.

## Nombres que la plantilla reconoce sola

Si existe el archivo, se usa. Si no, la página funciona igual sin él: no hay
imágenes rotas ni huecos.

| archivo | dónde sale | tamaño recomendado |
|---|---|---|
| `logo.*` | junto al título, arriba a la izquierda | alto de 32 px, fondo transparente |
| `favicon.*` | la pestaña del navegador | 32×32 o 64×64 |

Extensiones aceptadas, por orden de preferencia: `svg`, `png`, `webp`, `jpg`,
`jpeg`, `gif`. Un `svg` se ve nítido en cualquier pantalla y suele pesar menos.

No hace falta reiniciar uvicorn al añadir una imagen: la plantilla la busca en
cada petición. Basta con recargar la página.

## Cualquier otro archivo

Se sirve igual en `/static/loquesea.png`, pero para que aparezca hay que
escribirlo a mano en `templates/dashboard.html`.

## Colores de la marca

```
#1D4ED8   azul    acciones, la categoría propuesta, enlaces
#021237   marino  el texto en claro, el fondo entero en oscuro
```

Están en las variables CSS `--acento` y `--marino` de `dashboard.html`. Cambiar
esos dos valores repinta la página entera.

## Cuidado con lo que dejas aquí

Esta carpeta **sí se sube al repositorio**, a diferencia de `credentials/` o
`evaluation/`. No dejes aquí capturas de la bandeja con remitentes y asuntos
reales: acabarían publicadas el día que el repo se haga público.
