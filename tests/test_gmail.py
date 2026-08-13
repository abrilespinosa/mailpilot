"""
Tests del parsing y la paginación de Gmail.

No tocan la red ni la base de datos: son rápidos y deterministas.
"""

from datetime import datetime, timezone

from mailpilot.gmail import fetch_message, fetch_messages, list_message_ids
from tests.fakes import FakeMessagesResource, FakeService, make_raw_message


# ---------------------------------------------------------------------------
# Paginación
# ---------------------------------------------------------------------------


def test_encadena_paginas_hasta_alcanzar_el_limite():
    """Con 2 correos por página y un límite de 5, hace 3 llamadas."""
    resource = FakeMessagesResource(
        pages=[
            {"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "p2"},
            {"messages": [{"id": "3"}, {"id": "4"}], "nextPageToken": "p3"},
            {"messages": [{"id": "5"}, {"id": "6"}], "nextPageToken": "p4"},
        ]
    )

    ids = list_message_ids(FakeService(resource), limit=5)

    # Nunca devuelve más de `limit`, aunque la última página traiga de más.
    assert ids == ["1", "2", "3", "4", "5"]
    assert len(resource.list_calls) == 3


def test_para_cuando_no_hay_mas_paginas():
    """
    Si la bandeja tiene menos correos de los pedidos, la función termina.

    Este es el test que protege del bucle infinito: sin el `break` cuando falta
    nextPageToken, pediría páginas inexistentes para siempre.
    """
    resource = FakeMessagesResource(
        pages=[{"messages": [{"id": "1"}, {"id": "2"}]}]  # sin nextPageToken
    )

    ids = list_message_ids(FakeService(resource), limit=100)

    assert ids == ["1", "2"]
    assert len(resource.list_calls) == 1


def test_solo_pide_los_que_le_faltan():
    """La última página pide 3, no 100: maxResults = limit - los que ya tiene."""
    resource = FakeMessagesResource(
        pages=[
            {"messages": [{"id": str(n)} for n in range(100)], "nextPageToken": "p2"},
            {"messages": [{"id": "x"}, {"id": "y"}, {"id": "z"}]},
        ]
    )

    list_message_ids(FakeService(resource), limit=103)

    assert resource.list_calls[0]["maxResults"] == 100
    assert resource.list_calls[1]["maxResults"] == 3


def test_la_primera_pagina_va_sin_token():
    resource = FakeMessagesResource(pages=[{"messages": [{"id": "1"}]}])

    list_message_ids(FakeService(resource), limit=1)

    assert resource.list_calls[0]["pageToken"] is None


def test_bandeja_vacia():
    resource = FakeMessagesResource(pages=[{}])

    assert list_message_ids(FakeService(resource), limit=10) == []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_extrae_todos_los_campos():
    resource = FakeMessagesResource(
        messages={
            "abc123": make_raw_message(
                message_id="abc123",
                thread_id="hilo9",
                subject="Informe mensual BBVA",
                sender="BBVA <bbva@informes.bbva.com>",
                snippet="Descubre como puedo ayudarte",
                labels=["INBOX", "UNREAD"],
            )
        }
    )

    email = fetch_message(FakeService(resource), "abc123")

    assert email.gmail_message_id == "abc123"
    assert email.gmail_thread_id == "hilo9"
    assert email.subject == "Informe mensual BBVA"
    assert email.sender == "BBVA <bbva@informes.bbva.com>"
    assert email.snippet == "Descubre como puedo ayudarte"
    assert email.raw_labels == ["INBOX", "UNREAD"]


def test_las_cabeceras_no_distinguen_mayusculas():
    """
    El estándar de correo dice que los nombres de cabecera son
    case-insensitive, y hay servidores que mandan SUBJECT en mayúsculas.
    """
    raw = make_raw_message()
    raw["payload"]["headers"] = [
        {"name": "SUBJECT", "value": "En mayúsculas"},
        {"name": "from", "value": "en@minusculas.com"},
    ]
    resource = FakeMessagesResource(messages={"abc123": raw})

    email = fetch_message(FakeService(resource), "abc123")

    assert email.subject == "En mayúsculas"
    assert email.sender == "en@minusculas.com"


def test_correo_sin_asunto_no_rompe_la_ingestion():
    resource = FakeMessagesResource(
        messages={"abc123": make_raw_message(subject=None, sender=None)}
    )

    email = fetch_message(FakeService(resource), "abc123")

    assert email.subject == "(sin asunto)"
    assert email.sender == "(sin remitente)"


def test_la_fecha_sale_de_internaldate_en_utc():
    """internalDate son milisegundos desde 1970. 0 = 1 de enero de 1970 UTC."""
    resource = FakeMessagesResource(
        messages={"abc123": make_raw_message(internal_date="0")}
    )

    email = fetch_message(FakeService(resource), "abc123")

    assert email.received_at == datetime(1970, 1, 1, tzinfo=timezone.utc)
    # Con zona horaria, nunca "ingenua". Guardar fechas sin tz es una fuente
    # clásica de errores silenciosos al comparar.
    assert email.received_at.tzinfo is not None


def test_pide_metadata_y_no_el_cuerpo():
    """
    Comprueba CÓMO se llama a la API, no solo el resultado.

    Si alguien cambia esto a format="full", el sistema empezaría a descargar
    cuerpos de correo. Es una decisión de minimización de datos, y este test
    la convierte en algo que no se puede cambiar por accidente.
    """
    resource = FakeMessagesResource(messages={"abc123": make_raw_message()})

    fetch_message(FakeService(resource), "abc123")

    assert resource.get_calls[0]["format"] == "metadata"


def test_fetch_messages_respeta_el_orden():
    resource = FakeMessagesResource(
        messages={
            "1": make_raw_message(message_id="1"),
            "2": make_raw_message(message_id="2"),
            "3": make_raw_message(message_id="3"),
        }
    )

    emails = fetch_messages(FakeService(resource), ["3", "1", "2"])

    assert [e.gmail_message_id for e in emails] == ["3", "1", "2"]
