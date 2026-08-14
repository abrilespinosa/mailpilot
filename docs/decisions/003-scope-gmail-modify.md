# ADR 003 — Subir el scope de OAuth a `gmail.modify`

Fecha: 2026-08-14
Estado: aceptado

## Contexto

Hasta hoy MailPilot pide `gmail.readonly`. Con ese permiso **no puede cambiar
nada en la cuenta ni aunque tuviera un bug o alguien lo manipulara**: no es una
promesa del código, es que Google rechazaría la petición.

La Fase 9 necesita hacer dos cosas reales: poner etiquetas y mover correos a la
papelera. `gmail.readonly` no permite ninguna de las dos.

## Decisión

Pasar a `https://www.googleapis.com/auth/gmail.modify`.

### Por qué ese y no otro

Se miraron los scopes de Gmail que permiten escribir:

| scope | permite | por qué no |
|---|---|---|
| `gmail.labels` | crear y aplicar etiquetas | **no permite mover a papelera**, que es la mitad del objetivo |
| `gmail.modify` | etiquetas + papelera + leer | **elegido**: el mínimo que cubre el caso |
| `https://mail.google.com/` | todo, incluido BORRADO PERMANENTE | rechazado, ver abajo |

**El dato que decide: `gmail.modify` NO puede borrar de forma permanente.** La
llamada `users.messages.delete` de la Gmail API exige el scope completo
`https://mail.google.com/`. Con `gmail.modify` solo existe `messages.trash`,
que es reversible 30 días.

Es decir: la regla "nunca borrado permanente" del proyecto deja de depender de
nuestra disciplina y pasa a estar **impuesta desde fuera**. Aunque alguien
escribiera esa llamada por error, Google la rechazaría con un 403. Elegir el
scope mínimo no es burocracia: es la barrera de seguridad más barata y más
fiable de todo el sistema.

## Qué cambia y qué NO cambia

Cambia:

- MailPilot pasa a poder etiquetar y mover a papelera.
- Hay que volver a pasar por la pantalla de consentimiento de Google, con el
  aviso de "app no verificada" (la app está en modo Testing).
- El token guardado deja de servir: los scopes no se amplían refrescando.

No cambia:

- **Nada se ejecuta sin aprobación humana explícita.** El scope da capacidad,
  no permiso: cada acción sigue pasando por propuesta -> decisión -> ejecución.
- El contenido de los correos sigue sin salir de la máquina.
- El borrado permanente sigue fuera del alcance del proyecto, ahora por partida
  doble: no se implementa y el scope no lo permitiría.
- La ingestión sigue usando `format=metadata`: no se descarga el cuerpo.

## Riesgos asumidos

- **Un bug ahora puede tocar la cuenta.** Contención: la ejecución solo se
  dispara desde una propuesta en estado `approved`, y queda registrada en el
  audit log. Los tests deben cubrir que ninguna otra ruta ejecuta nada.
- **La papelera es reversible pero no infinita**: 30 días en Gmail. Un error
  detectado tarde sí pierde correo.
- El token de un proyecto en modo Testing sigue caducando cada 7 días. No
  cambia con el scope.

## Consecuencias

- `SCOPES` en `src/mailpilot/auth.py` es la única línea que hay que tocar.
- Hay que **borrar `credentials/token.json`**. Un token emitido para
  `readonly` no gana permisos al refrescarse: seguiría funcionando para leer y
  fallando con 403 al escribir, que es un error confuso de diagnosticar.
  `get_credentials()` ahora detecta ese caso y relanza el flujo solo.
- Hay que añadir el scope en la pantalla de consentimiento de Google Cloud
  Console antes de reautenticar.
- Revertir es trivial: volver a `readonly`, borrar el token y reautenticar.
