"""
Lectura de correos desde la Gmail API.

Este módulo NO accede a credentials/ directamente: le pide las credenciales
a auth.py y construye el cliente de la API por encima.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from googleapiclient.discovery import build

from mailpilot.auth import get_credentials

# Gmail acepta como mucho 500 por página, pero páginas más pequeñas hacen
# que se note antes si algo va mal en el bucle de paginación.
PAGE_SIZE = 100

# Solo estas cabeceras. La fecha NO se pide: viene de internalDate, que la
# pone Gmail y es más fiable que la cabecera Date del remitente.
METADATA_HEADERS = ["Subject", "From"]


@dataclass(frozen=True)
class EmailData:
    """
    Un correo ya parseado, con la forma que espera la tabla Email.

    Es `frozen` a propósito: representa un correo que ya ha llegado, un hecho
    del pasado. Nada del sistema debería modificarlo por el camino.
    """

    gmail_message_id: str
    gmail_thread_id: str
    subject: str
    sender: str
    snippet: str
    received_at: datetime
    raw_labels: list[str]


def get_service():
    """Devuelve un cliente autenticado de la Gmail API."""
    return build("gmail", "v1", credentials=get_credentials())


def list_message_ids(service, limit: int = 10) -> list[str]:
    """
    Devuelve hasta `limit` IDs de mensajes, del más reciente al más antiguo.

    Gmail devuelve los resultados por páginas: cada respuesta trae un puñado
    de mensajes y, si hay más, un `nextPageToken` para pedir la siguiente.
    Este bucle encadena páginas hasta llegar a `limit` o hasta que se acaben
    los correos.

    Los correos en Spam y Papelera quedan fuera (es el comportamiento por
    defecto de la API, no hace falta pedirlo).
    """
    ids: list[str] = []
    page_token: str | None = None

    while len(ids) < limit:
        response = (
            service.users()
            .messages()
            .list(
                userId="me",
                maxResults=min(PAGE_SIZE, limit - len(ids)),
                pageToken=page_token,
            )
            .execute()
        )

        for message in response.get("messages", []):
            ids.append(message["id"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # Recorte defensivo: si una página devolviera más mensajes de los pedidos
    # en maxResults, sin esto la función devolvería más de `limit`.
    return ids[:limit]


def _headers_to_dict(payload: dict) -> dict[str, str]:
    """
    Gmail devuelve las cabeceras como lista de {"name": ..., "value": ...}.
    Las pasamos a diccionario para poder buscarlas por nombre.

    Las claves van en minúscula porque el estándar de correo dice que los
    nombres de cabecera no distinguen mayúsculas: hay servidores que mandan
    "Subject", otros "SUBJECT" y otros "subject".
    """
    return {
        header["name"].lower(): header["value"]
        for header in payload.get("headers", [])
    }


def fetch_message(service, message_id: str) -> EmailData:
    """
    Descarga un correo y lo convierte en EmailData.

    Pide `format="metadata"`: trae cabeceras, labels, fecha y snippet, pero
    NO el cuerpo del mensaje. Es lo único que necesita el modelo de datos.
    """
    raw = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=METADATA_HEADERS,
        )
        .execute()
    )

    headers = _headers_to_dict(raw.get("payload", {}))

    # internalDate viene como string de milisegundos desde 1970, en UTC.
    received_at = datetime.fromtimestamp(
        int(raw["internalDate"]) / 1000, tz=timezone.utc
    )

    return EmailData(
        gmail_message_id=raw["id"],
        gmail_thread_id=raw["threadId"],
        # Hay correos sin asunto y correos sin remitente legible. Si faltan,
        # preferimos un valor por defecto a que reviente la ingestión entera.
        subject=headers.get("subject", "(sin asunto)"),
        sender=headers.get("from", "(sin remitente)"),
        snippet=raw.get("snippet", ""),
        received_at=received_at,
        raw_labels=raw.get("labelIds", []),
    )


def fetch_messages(service, message_ids: list[str]) -> list[EmailData]:
    """
    Descarga varios correos, uno por uno.

    Una llamada HTTP por correo. Para unos cientos es perfectamente asumible;
    si llega a molestar, Gmail tiene peticiones por lotes (batch). No lo
    optimizamos antes de tener un número que lo justifique.
    """
    return [fetch_message(service, message_id) for message_id in message_ids]


def ids_en_papelera(service) -> set[str]:
    """
    Los ids de todos los mensajes que están en la papelera de Gmail.

    Una consulta paginada para todos, no una llamada por correo: con 118 en la
    papelera la diferencia es de una llamada frente a 118.

    `messages.list` excluye la papelera por defecto, así que hay que pedirla a
    propósito con `q='in:trash'`. Ese mismo comportamiento es la razón de que
    un correo tirado desaparezca de la ingestión y su fila se quede con datos
    viejos: nunca vuelve a pasar por el upsert.
    """
    encontrados: set[str] = set()
    page_token: str | None = None

    while True:
        respuesta = (
            service.users()
            .messages()
            .list(userId="me", q="in:trash", maxResults=500, pageToken=page_token)
            .execute()
        )
        encontrados.update(m["id"] for m in respuesta.get("messages", []))

        page_token = respuesta.get("nextPageToken")
        if not page_token:
            return encontrados
