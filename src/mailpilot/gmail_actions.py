"""
El ÚNICO módulo que escribe en Gmail.

Todo lo que MailPilot puede hacerle a tu cuenta está aquí, y son dos cosas:
poner una etiqueta y mover a la papelera. Ningún otro módulo debe llamar a la
API de escritura; `tests/test_limites_gmail.py` lo comprueba rastreando el
código fuente.

LO QUE NO HACE, Y POR QUÉ IMPORTA
---------------------------------
- **No envía correo.** `gmail.modify` lo permitiría: esa garantía no la impone
  Google, la impone este módulo más el test que lo vigila. Ver ADR 003.
- **No borra permanentemente.** Eso sí lo impide Google: `messages.delete`
  exige el scope `https://mail.google.com/`, que no pedimos.
- **No toca tus etiquetas.** Solo crea las suyas bajo `MailPilot/` y las añade.
  Nunca quita etiquetas existentes ni borra ninguna.
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
    service, gmail_message_id: str, label_id: str, otras_nuestras: list[str] = ()
) -> None:
    """
    Deja el mensaje con UNA etiqueta de MailPilot: la que toca.

    `otras_nuestras` son los ids de las demás etiquetas `MailPilot/*`, que se
    quitan. Hace falta porque las decisiones cambian: si etiquetas un correo
    como `promociones` y luego lo corriges a `trabajo`, sin quitar la primera
    en Gmail acabarían las dos y la etiqueta dejaría de significar nada.

    LA REGLA QUE NO SE PUEDE ROMPER: `removeLabelIds` solo puede contener
    etiquetas de MailPilot. Nunca las de la usuaria, ni las de Gmail (INBOX,
    UNREAD, STARRED). Quitar INBOX archivaría el correo, que es una acción
    destructiva que nadie ha pedido. Quien llame a esta función es responsable
    de pasar solo ids de `MailPilot/*`, y hay un test que lo comprueba.

    Añadir una etiqueta que ya está puesta no hace nada, así que reintentar es
    seguro.
    """
    cuerpo: dict[str, list[str]] = {"addLabelIds": [label_id]}

    sobrantes = [otra for otra in otras_nuestras if otra != label_id]
    if sobrantes:
        cuerpo["removeLabelIds"] = sobrantes

    service.users().messages().modify(
        userId="me", id=gmail_message_id, body=cuerpo
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
            # Solo las nuestras. El filtro contra un conjunto CERRADO —los
            # siete nombres del enum— es lo que garantiza que `removeLabelIds`
            # jamás toque una etiqueta de la usuaria, ni INBOX, ni STARRED.
            nuestras = [
                id_
                for nombre_etiqueta, id_ in cache.items()
                if nombre_etiqueta in NUESTRAS_ETIQUETAS
            ]
            aplicar_etiqueta(service, email.gmail_message_id, label_id, nuestras)
            detalle = {"etiqueta": nombre}
        else:
            mover_a_papelera(service, email.gmail_message_id)
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
