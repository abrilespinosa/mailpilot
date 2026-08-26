"""
Lectura de correos desde la Gmail API.

Este módulo NO accede a credentials/ directamente: le pide las credenciales
a auth.py y construye el cliente de la API por encima.
"""

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

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


def get_service(interactivo: bool = True):
    """
    Devuelve un cliente autenticado de la Gmail API.

    `interactivo=False` hace que caducar el token lance
    `auth.NecesitaReautenticacion` en vez de abrir el navegador. Es obligatorio
    en todo lo que atienda peticiones HTTP: ver el docstring de
    `auth.get_credentials`.
    """
    return build("gmail", "v1", credentials=get_credentials(interactivo=interactivo))


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


# Tope de lo que se manda al navegador. Un correo con imágenes incrustadas en
# base64 puede pesar megas, y para decidir una categoría sobran unos miles de
# caracteres. No es seguridad, es no colgar la pestaña.
MAXIMO_CUERPO = 20_000

# Los correos comerciales meten enlaces de seguimiento de 300 caracteres, y
# llegan a ser la mayor parte del texto. Para decidir una categoría no aportan
# nada y tapan lo que sí importa, así que se colapsan. Los enlaces cortos se
# dejan tal cual: ahí el dominio se lee y a veces es justo el dato que decide.
_ENLACE_LARGO = re.compile(r"https?://\S{60,}")

# Caracteres invisibles que Gmail intercala a cientos para forzar el ancho de
# la vista previa: espacios de ancho cero, uniones de grafemas, guiones
# blandos. No se ven pero ocupan, y dejan el texto lleno de basura ilegible.
_INVISIBLES = re.compile("[\u200b-\u200d\u034f\u00ad\ufeff\u2060]")


class _SinEtiquetas(HTMLParser):
    """
    Convierte HTML en texto plano quedándose solo con lo que se lee.

    Se usa cuando el correo no trae una parte `text/plain`. No pretende
    renderizar: descarta `<script>` y `<style>` porque su contenido no es texto
    que la usuaria quiera leer, y deja pasar el resto.

    Ojo: esto NO es el saneado de seguridad. Lo que protege contra XSS es que
    el navegador inserte esto con `textContent`, nunca con `innerHTML`. Aquí
    solo se busca que el resultado sea legible.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.trozos: list[str] = []
        self._saltar = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._saltar = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._saltar = False
        elif tag in ("p", "br", "div", "tr", "li"):
            self.trozos.append("\n")

    def handle_data(self, data):
        if not self._saltar:
            self.trozos.append(data)

    def texto(self) -> str:
        # Gmail mete mucho espaciador invisible para maquetar. Sin esta
        # limpieza el resultado sale con decenas de líneas en blanco seguidas.
        crudo = "".join(self.trozos).replace("\u200c", " ").replace("\u00ad", "")
        lineas = [linea.strip() for linea in crudo.splitlines()]
        return "\n".join(linea for linea in lineas if linea)


def _decodificar(datos: str) -> str:
    """base64url -> texto. Gmail codifica así el cuerpo de cada parte."""
    return base64.urlsafe_b64decode(datos.encode()).decode("utf-8", errors="replace")


def _recorrer_partes(payload: dict) -> tuple[str, str]:
    """
    Recorre el árbol MIME y devuelve (texto_plano, texto_html).

    Un correo es un árbol: `multipart/alternative` con una parte de texto y
    otra de HTML, `multipart/mixed` con adjuntos colgando... Por eso la función
    se llama a sí misma en vez de mirar solo el primer nivel.
    """
    plano, html = "", ""
    tipo = payload.get("mimeType", "")
    datos = payload.get("body", {}).get("data")

    if datos:
        if tipo == "text/plain":
            plano = _decodificar(datos)
        elif tipo == "text/html":
            html = _decodificar(datos)

    for parte in payload.get("parts", []):
        hijo_plano, hijo_html = _recorrer_partes(parte)
        plano = plano or hijo_plano
        html = html or hijo_html

    return plano, html


def fetch_body(service, message_id: str) -> str:
    """
    Descarga el CUERPO de un correo y lo devuelve como texto plano.

    Es la única función del módulo que pide `format="full"`. El resto de la
    ingestión usa `format="metadata"` a propósito, y esa decisión NO cambia:
    el cuerpo se pide al vuelo cuando la usuaria pulsa "ver más" y **no se
    guarda en la base de datos**. Así el contenido de los correos sigue sin
    persistirse en disco y el modelo de amenazas se queda como estaba.

    Tampoco llega nunca al clasificador: el LLM sigue viendo solo metadatos.
    Alimentarlo con el cuerpo sería otra decisión, con su propio ADR, porque
    abre la superficie de prompt injection que hoy no existe.

    Prefiere la parte `text/plain`. Si el correo solo trae HTML —muy común en
    boletines y promociones— lo convierte a texto.
    """
    raw = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    plano, html = _recorrer_partes(raw.get("payload", {}))

    if not plano and html:
        parser = _SinEtiquetas()
        parser.feed(html)
        plano = parser.texto()

    # Si no hay ni una cosa ni la otra queda el snippet, que siempre viene.
    texto = plano.strip() or raw.get("snippet", "")
    texto = _INVISIBLES.sub("", texto)
    # El relleno venía en pares "invisible + espacio": al quitar el invisible
    # queda una tirada de espacios igual de larga. Se colapsan.
    texto = re.sub(r"[ \t]{3,}", " ", texto)
    texto = _ENLACE_LARGO.sub("[enlace]", texto)

    # Tras quitar los enlaces quedan bloques de líneas vacías donde estaban.
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto[:MAXIMO_CUERPO]
