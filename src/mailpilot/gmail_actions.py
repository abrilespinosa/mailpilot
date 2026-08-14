"""
El ÚNICO módulo que escribe en Gmail.

Todo lo que MailPilot puede hacerle a tu cuenta está aquí, y son tres cosas:
etiquetar (que además archiva), mover a la papelera y sacar de la papelera.
Ningún otro módulo debe llamar a la API de escritura;
`tests/test_limites_gmail.py` lo comprueba rastreando el código fuente.

LO QUE NO HACE, Y POR QUÉ IMPORTA
---------------------------------
- **No envía correo.** `gmail.modify` lo permitiría: esa garantía no la impone
  Google, la impone este módulo más el test que lo vigila. Ver ADR 003.
- **No borra permanentemente.** Eso sí lo impide Google: `messages.delete`
  exige el scope `https://mail.google.com/`, que no pedimos.
- **No toca tus etiquetas.** Solo puede quitar las siete suyas e INBOX, que es
  la lista blanca cerrada `QUITABLES`. Nunca las tuyas, nunca UNREAD, nunca
  STARRED. Y no borra ninguna etiqueta de la cuenta, solo las despega de un
  mensaje.
- **Archiva al clasificar** (ADR 004): quitar INBOX es lo que en Gmail
  significa archivar. Ni borra ni mueve nada, y volver a añadir INBOX lo
  deshace.
- **No decide nada.** Ejecuta filas de `gmail_actions` que existen porque
  alguien las pidió. Este módulo no sabe clasificar ni le pregunta al modelo.

IDEMPOTENCIA
------------
Entre "Gmail ya lo hizo" y "PostgreSQL se enteró" no hay transacción posible:
son dos sistemas. Si el proceso muere en medio, la fila se queda en `pending` y
la acción ya está hecha.

La defensa no es evitar ese hueco, es que repetir no duela. Añadir una etiqueta
que ya está es un no-op en Gmail, y mandar a la papelera algo que ya está en la
papelera, también. Así que reintentar una acción `pending` siempre es seguro.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mailpilot.models import (
    AuditLog,
    Category,
    GmailAction,
    GmailActionStatus,
    GmailActionType,
)

# Cómo se llaman las etiquetas EN GMAIL.
#
# Con tildes y mayúscula, porque las vas a ver a diario en la barra lateral.
# En la base de datos el enum sigue siendo `tramites` sin tilde: los
# identificadores internos y lo que se le enseña a una persona son cosas
# distintas, y mezclarlas obliga a poner tildes en el código.
#
# Hubo una versión anterior con prefijo (`MailPilot/tramites`) que se descartó
# a petición de la usuaria. El prefijo servía para saber qué etiquetas eran
# nuestras; ahora ese papel lo hace este diccionario, que es un conjunto igual
# de cerrado porque sale del enum `Category`.
NOMBRES_EN_GMAIL = {
    Category.PERSONAL: "Personal",
    Category.TRABAJO: "Trabajo",
    Category.COMPRAS: "Compras",
    Category.TRAMITES: "Trámites",
    Category.AVISOS: "Avisos",
    Category.PROMOCIONES: "Promociones",
    Category.OTROS: "Otros",
}

# El conjunto de etiquetas que MailPilot considera SUYAS. Es lo único que
# `removeLabelIds` puede llegar a contener: ver `aplicar_etiqueta`.
NUESTRAS_ETIQUETAS = frozenset(NOMBRES_EN_GMAIL.values())

# Archivar en Gmail es exactamente esto: quitar INBOX. El correo no se borra
# ni se mueve, sigue en Todos y sigue con su etiqueta.
INBOX = "INBOX"

# La lista blanca COMPLETA de lo que se puede quitar de un mensaje. Es una
# frontera de seguridad, no una comodidad: ampliarla es darle a MailPilot
# permiso para deshacer algo que hiciste tú.
#
# Lo que NO está aquí, y por qué:
#   UNREAD   quitarlo marcaría como leído lo que no has leído
#   STARRED  quitarlo borraría tus destacados
#   las tuyas   son tuyas
QUITABLES = NUESTRAS_ETIQUETAS | {INBOX}


def nombre_de_etiqueta(categoria: Category) -> str:
    return NOMBRES_EN_GMAIL[categoria]


# ---------------------------------------------------------------------------
# Operaciones sobre Gmail. Ninguna toca la base de datos.
# ---------------------------------------------------------------------------


def etiquetas_existentes(service) -> dict[str, str]:
    """Devuelve {nombre: id} de todas las etiquetas de la cuenta."""
    respuesta = service.users().labels().list(userId="me").execute()
    return {etiqueta["name"]: etiqueta["id"] for etiqueta in respuesta.get("labels", [])}


def asegurar_etiqueta(service, nombre: str, cache: dict[str, str] | None = None) -> str:
    """
    Devuelve el id de la etiqueta, creándola si no existe.

    El `cache` evita pedir la lista de etiquetas una vez por correo: al
    procesar cien acciones seguidas, sin él serían cien llamadas idénticas.
    """
    if cache is None:
        cache = etiquetas_existentes(service)

    if nombre in cache:
        return cache[nombre]

    creada = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": nombre,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    cache[nombre] = creada["id"]
    return creada["id"]


def aplicar_etiqueta(
    service,
    gmail_message_id: str,
    label_id: str,
    cache: dict[str, str],
    archivar: bool = True,
) -> None:
    """
    Deja el mensaje con UNA etiqueta de MailPilot y fuera de Recibidos.

    Quita las demás etiquetas nuestras porque las decisiones cambian: si
    clasificas un correo como `promociones` y luego lo corriges a `trabajo`,
    sin quitar la primera acabarían las dos y la etiqueta dejaría de
    significar nada.

    Y quita INBOX, que en Gmail es exactamente lo que significa archivar. Es
    el motivo del proyecto: la bandeja de entrada se queda con lo que aún no
    has mirado. El correo no se borra —sigue en Todos, sigue buscable y sigue
    con su etiqueta—, y volver a añadir INBOX lo deshace.

    LA REGLA QUE NO SE PUEDE ROMPER
    -------------------------------
    `removeLabelIds` solo puede contener etiquetas de `QUITABLES`: las siete
    nuestras y INBOX. Nunca las de la usuaria, nunca UNREAD (marcaría como
    leído lo que no has leído), nunca STARRED (borraría tus destacados).

    Antes esa regla era un comentario y la lista la construía quien llamaba.
    Ahora se construye AQUÍ, filtrando el catálogo de etiquetas contra el
    conjunto cerrado: por construcción no hay forma de colar nada más.

    Añadir una etiqueta que ya está, o quitar una que no está, no hace nada en
    Gmail. Reintentar siempre es seguro.
    """
    quitar = [
        id_
        for nombre, id_ in cache.items()
        if nombre in NUESTRAS_ETIQUETAS and id_ != label_id
    ]
    if archivar:
        quitar.append(INBOX)

    cuerpo: dict[str, list[str]] = {"addLabelIds": [label_id]}
    if quitar:
        cuerpo["removeLabelIds"] = quitar

    service.users().messages().modify(
        userId="me", id=gmail_message_id, body=cuerpo
    ).execute()


def restaurar_de_papelera(service, gmail_message_id: str, devolver_a_inbox: bool) -> None:
    """
    Saca un mensaje de la papelera. La única acción que no quita nada.

    `untrash` solo borra la etiqueta TRASH. NO devuelve el correo a Recibidos,
    porque Gmail le quitó INBOX al tirarlo: sin más, el correo saldría de la
    papelera pero quedaría archivado, y buscarlo sería un misterio.

    Por eso `devolver_a_inbox` se decide fuera, mirando si el correo TENÍA
    INBOX antes de que lo tiraran (lo sabemos: está en `raw_labels`). Así
    recuperar un correo archivado no lo desarchiva de propina, que sería hacer
    más de lo que nadie pidió.
    """
    service.users().messages().untrash(userId="me", id=gmail_message_id).execute()

    if devolver_a_inbox:
        service.users().messages().modify(
            userId="me", id=gmail_message_id, body={"addLabelIds": ["INBOX"]}
        ).execute()


def mover_a_papelera(service, gmail_message_id: str) -> None:
    """
    Mueve un mensaje a la papelera de Gmail. Reversible 30 días.

    Es la ÚNICA acción destructiva del proyecto. El borrado permanente
    (`messages.delete`) no se implementa y además sería imposible: exige el
    scope completo, que no pedimos. Ver ADR 003.
    """
    service.users().messages().trash(userId="me", id=gmail_message_id).execute()


# ---------------------------------------------------------------------------
# Ejecutar una acción pedida
# ---------------------------------------------------------------------------


def ejecutar(session: Session, service, accion: GmailAction, cache=None) -> GmailAction:
    """
    Ejecuta una acción pendiente y registra qué pasó.

    Un fallo NUNCA se traga en silencio: la fila queda en `failed` con el error
    guardado en `detail`, y el audit log recoge las dos cosas. Si algo saliera
    mal a las tres de la mañana, la base de datos tiene que poder contarlo.

    Reejecutar una acción ya ejecutada no vuelve a llamar a Gmail: cortamos
    aquí en vez de fiarnos solo de la idempotencia de la API.
    """
    if accion.status is not GmailActionStatus.PENDING:
        return accion

    email = accion.email

    try:
        if accion.action is GmailActionType.APPLY_LABEL:
            categoria = _categoria_a_aplicar(accion)
            nombre = nombre_de_etiqueta(categoria)
            if cache is None:
                cache = etiquetas_existentes(service)
            label_id = asegurar_etiqueta(service, nombre, cache)
            aplicar_etiqueta(service, email.gmail_message_id, label_id, cache)
            # `archivado` se escribe en el audit log a propósito: el valor del
            # enum se llama `apply_label` y no dice que además saque el correo
            # de Recibidos. Auditar no debería depender de leerse el código.
            detalle = {"etiqueta": nombre, "archivado": True}
        elif accion.action is GmailActionType.RESTORE_FROM_TRASH:
            # Solo vuelve a Recibidos si estaba ahí antes de que lo tiraran.
            # `raw_labels` es la foto de justo antes: un correo tirado
            # desaparece de la ingestión, así que nunca se sobrescribió.
            volvia = "INBOX" in (email.raw_labels or [])
            restaurar_de_papelera(service, email.gmail_message_id, volvia)
            email.en_papelera = False
            detalle = {"recuperado": True, "a_recibidos": volvia}

        else:
            mover_a_papelera(service, email.gmail_message_id)
            # El estado actual se marca aquí mismo para que la pestaña de
            # papelera lo refleje sin esperar a una sincronización.
            email.en_papelera = True
            detalle = {"papelera": True, "reversible_dias": 30}

    except Exception as error:
        accion.status = GmailActionStatus.FAILED
        accion.detail = {"error": f"{type(error).__name__}: {error}"}
        session.add(
            AuditLog(
                email_id=accion.email_id,
                action_proposal_id=accion.action_proposal_id,
                event_type="gmail_action_failed",
                detail={"accion": accion.action.value, **accion.detail},
            )
        )
        session.commit()
        return accion

    accion.status = GmailActionStatus.EXECUTED
    accion.detail = detalle
    accion.executed_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            email_id=accion.email_id,
            action_proposal_id=accion.action_proposal_id,
            event_type="gmail_action_executed",
            detail={"accion": accion.action.value, **detalle},
        )
    )
    session.commit()

    return accion


def _categoria_a_aplicar(accion: GmailAction) -> Category:
    """
    Qué etiqueta poner: la que decidió la usuaria, nunca la que dijo el modelo.

    `final_category` es su decisión y `category` es la propuesta de la IA. Usar
    la segunda haría que MailPilot etiquetara en Gmail algo que ella había
    corregido, que es exactamente lo que el proyecto promete no hacer.
    """
    propuesta = accion.proposal
    if propuesta is None or propuesta.final_category is None:
        raise ValueError(
            f"La acción {accion.id} no tiene una categoría decidida por la usuaria"
        )
    return propuesta.final_category
