# ADR 004 — Clasificar un correo lo saca de Recibidos

Fecha: 2026-08-14
Estado: aceptado

## Contexto

La usuaria lo pide así:

> otro detalle a tener en cuenta es que si lo he clasificado desaparezcan de
> recibidos

Es el motivo por el que existe el proyecto. El problema declarado en la Fase 0
es *la sobrecarga de decidir qué hacer con cada correo entrante*, y hasta ahora
MailPilot no lo resolvía: añadía una etiqueta y dejaba el correo exactamente
donde estaba. La bandeja de entrada seguía creciendo, solo que con etiquetas.

Con esto, Recibidos pasa a significar **lo que todavía no has mirado**.

## Qué es archivar

En Gmail, archivar es quitar la etiqueta `INBOX`. Nada más. El correo:

- sigue existiendo, en Todos
- sigue buscable
- sigue con su etiqueta de MailPilot en la barra lateral
- vuelve a Recibidos volviendo a añadir `INBOX`

No es la papelera, no es borrar, y no cuenta como acción destructiva.

## El problema real

Hasta hoy `gmail_actions.py` tenía esto escrito en mayúsculas:

> LA REGLA QUE NO SE PUEDE ROMPER: `removeLabelIds` solo puede contener
> etiquetas de MailPilot. (…) Quitar INBOX archivaría el correo, que es una
> acción destructiva que nadie ha pedido.

La última frase dejó de ser cierta en cuanto la usuaria lo pidió. Pero la regla
existe por algo que **no** ha cambiado: si la lista de lo que se puede quitar se
construye a ojo, un descuido acaba quitando `UNREAD` (marcando como leído lo que
no has leído) o `STARRED` (borrando tus destacados).

Así que la regla no se abre: se estrecha y se hace explícita.

```python
QUITABLES = NUESTRAS_ETIQUETAS | {"INBOX"}
```

Y sobre todo, **la lista deja de construirla quien llama**. Antes `ejecutar`
filtraba el catálogo y le pasaba los ids a `aplicar_etiqueta`; ahora el filtro
vive dentro de `aplicar_etiqueta`, que es el único sitio que compone
`removeLabelIds`. Por construcción no hay forma de colar nada que no esté en el
conjunto cerrado, en vez de confiar en que cada llamada lo haga bien.

## Decisión

**Archivar va dentro de `apply_label`. No se añade una acción nueva al enum.**

## Alternativas descartadas

- **Un cuarto valor `archive` en `GmailActionType`**: rechazada, aunque es lo
  purista según el ADR 002 (clasificar y archivar son ejes distintos). Motivo:
  nunca existe el caso "etiquétalo pero déjalo en Recibidos". No son dos
  decisiones, es una decisión con su consecuencia. Un enum de cuatro valores y
  el doble de filas en la cola, para algo que siempre va junto y que podría
  desincronizarse —etiquetado pero no archivado—, es peor de mantener y no
  compra nada.

- **Archivar desde el dashboard con un botón aparte**: rechazada. Es un clic más
  por correo sobre miles, para una decisión que la usuaria ya tomó al clasificar.

- **Dejarlo como estaba y archivar a mano en Gmail**: rechazada. Es exactamente
  el trabajo manual que el proyecto existe para quitar.

## Consecuencias

### El nombre del enum miente un poco

`apply_label` ahora también archiva. Es el coste asumido de no ampliar el enum,
y es real: en un proyecto donde el enum cerrado ES la barrera de seguridad,
auditar los tres valores y quedarse tranquila ya no basta.

Se compensa **escribiéndolo en el audit log de cada ejecución**:

```json
{"accion": "apply_label", "etiqueta": "Promociones", "archivado": true}
```

Auditar qué le pasó a un correo no debe depender de leerse el código.

### Recuperar tiene que ganar sobre archivar

`pedir_recuperacion` encola `restore_from_trash` **y** `apply_label`. Como
`apply_label` ahora quita INBOX, ejecutarlas en ese orden sacaría el correo de
la papelera para archivarlo acto seguido: el gesto de la usuaria deshecho por
un efecto secundario.

Las acciones se ejecutan ordenadas por `id`, así que **la etiqueta se encola
primero y la recuperación después**. La última escritura es la del gesto más
reciente, que es la que debe mandar.

Lo fija `test_recuperar_deja_el_correo_en_recibidos_pese_a_que_etiquetar_archiva`.

### Los correos ya etiquetados siguen en Recibidos

Las 268 acciones `apply_label` ya ejecutadas se hicieron sin archivar. Esos
correos están etiquetados y en la bandeja. Archivarlos requiere volver a encolar
su etiqueta, y **es una decisión aparte**: son cientos de correos saliendo de
Recibidos de golpe. Reversible, pero no es algo que deba pasar como efecto
colateral de un cambio de código.

### La papelera no se ve afectada

`move_to_trash` ya sacaba el correo de Recibidos, porque Gmail quita INBOX al
tirar. No hay interacción entre las dos cosas.
