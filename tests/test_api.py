"""
Tests de la API.

Usan TestClient, que llama a los endpoints en el mismo proceso: no hace falta
levantar uvicorn ni abrir puertos. Y `get_session` se sustituye por la sesión
de test, así que la API habla con la base de datos de pruebas, nunca con la
real. Eso es lo que compra la inyección de dependencias.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from mailpilot.api import app, get_session
from mailpilot.repository import upsert_emails
from tests.test_repository import make_email

pytestmark = pytest.mark.db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def guardar_correos(session, cantidad: int) -> None:
    """Crea `cantidad` correos, cada uno una hora más antiguo que el anterior."""
    upsert_emails(
        session,
        [
            make_email(
                f"m{n}",
                subject=f"Correo {n}",
                received_at=datetime(2026, 8, 13, 12 - n, 0, tzinfo=timezone.utc),
            )
            for n in range(cantidad)
        ],
    )


# ---------------------------------------------------------------------------
# Salud
# ---------------------------------------------------------------------------


def test_health_comprueba_tambien_la_base_de_datos(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------


def test_listado_vacio(client):
    response = client.get("/emails")

    assert response.status_code == 200
    assert response.json() == {"total": 0, "limit": 20, "offset": 0, "items": []}


def test_ordena_del_mas_reciente_al_mas_antiguo(client, session):
    guardar_correos(session, 3)

    items = client.get("/emails").json()["items"]

    assert [item["subject"] for item in items] == ["Correo 0", "Correo 1", "Correo 2"]


def test_limit_y_offset(client, session):
    guardar_correos(session, 5)

    cuerpo = client.get("/emails?limit=2&offset=1").json()

    # total es el total real, no el de la página: el cliente necesita saber
    # cuántos hay para poder pintar la paginación.
    assert cuerpo["total"] == 5
    assert cuerpo["limit"] == 2
    assert cuerpo["offset"] == 1
    assert [item["subject"] for item in cuerpo["items"]] == ["Correo 1", "Correo 2"]


@pytest.mark.parametrize(
    "consulta",
    ["limit=0", "limit=101", "limit=-1", "offset=-1", "limit=abc"],
)
def test_rechaza_parametros_invalidos(client, consulta):
    """
    FastAPI valida los rangos antes de ejecutar el endpoint. El tope de limit
    evita que alguien pida un millón de filas de una vez.
    """
    assert client.get(f"/emails?{consulta}").status_code == 422


# ---------------------------------------------------------------------------
# Detalle
# ---------------------------------------------------------------------------


def test_detalle_de_un_correo(client, session):
    guardar_correos(session, 1)
    email_id = client.get("/emails").json()["items"][0]["id"]

    cuerpo = client.get(f"/emails/{email_id}").json()

    assert cuerpo["subject"] == "Correo 0"
    assert cuerpo["raw_labels"] == ["INBOX", "UNREAD"]
    assert cuerpo["snippet"] == "Texto"


def test_correo_inexistente_da_404(client):
    response = client.get("/emails/99999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Correo no encontrado"


# ---------------------------------------------------------------------------
# Lo que NO debe exponerse
# ---------------------------------------------------------------------------


def test_el_listado_solo_expone_los_campos_previstos(client, session):
    """
    Este test es el que hace valiosa la separación entre models.py y schemas.py.

    Si alguien añade una columna a la tabla emails y por error acaba publicada,
    este test se pone rojo. Sin él, la filtración pasaría desapercibida.
    """
    guardar_correos(session, 1)

    item = client.get("/emails").json()["items"][0]

    assert set(item) == {
        "id",
        "gmail_message_id",
        "subject",
        "sender",
        "received_at",
    }


ESCRITURA_PERMITIDA = {
    ("POST", "/proposals/{proposal_id}/approve"),
    ("POST", "/proposals/{proposal_id}/modify"),
    ("POST", "/proposals/{proposal_id}/reject"),
    # Rectificar una decisión ya tomada. Endpoint aparte a propósito: los tres
    # de arriba devuelven 409 sobre algo ya decidido, y esa protección contra
    # dos pestañas pisándose tiene que seguir intacta.
    ("POST", "/proposals/{proposal_id}/rectify"),
    # --- Fase 9: los dos únicos que pueden acabar cambiando algo en Gmail ---
    # Pedir la papelera. Solo escribe una fila `pending`: no toca Gmail.
    ("POST", "/emails/{email_id}/trash"),
    # Preguntar a Gmail qué hay en la papelera. Es una LECTURA de Gmail que
    # escribe en nuestra base de datos: no mueve ni etiqueta nada.
    ("POST", "/actions/sync-trash"),
    # Encolar la etiqueta de decisiones tomadas antes de que existiera el eje
    # de acciones. Tampoco toca Gmail: solo deja filas `pending`.
    ("POST", "/actions/backfill"),
    # EL ÚNICO ENDPOINT DE TODO EL PROYECTO QUE ESCRIBE EN GMAIL.
    # Ejecuta acciones que ya estaban pedidas; no puede inventarse trabajo.
    ("POST", "/actions/execute"),
}


def test_solo_escriben_los_endpoints_de_decision():
    """
    Lista blanca de escritura.

    Los únicos endpoints que modifican algo son los que registran una decisión
    de la usuaria sobre una propuesta. Cualquier endpoint de escritura nuevo
    hace fallar este test hasta que alguien lo añada aquí a propósito.

    Es la salvaguarda del principio del proyecto: la IA propone, la usuaria
    decide. Si algún día aparece un POST /emails/{id}/trash que no pase por una
    propuesta aprobada, saltará por aquí.
    """
    metodos_de_escritura = {"POST", "PUT", "PATCH", "DELETE"}
    encontrados = set()

    for route in app.routes:
        if not hasattr(route, "methods"):
            continue
        for metodo in route.methods & metodos_de_escritura:
            encontrados.add((metodo, route.path))

    assert encontrados == ESCRITURA_PERMITIDA


def test_ningun_endpoint_borra_nada():
    """
    DELETE no existe y no debe existir.

    El borrado permanente está fuera del alcance del proyecto, no solo del MVP.
    Lo único destructivo permitido es mover a papelera, reversible 30 días en
    Gmail, y llegará como ejecución de una propuesta aprobada.
    """
    for route in app.routes:
        if hasattr(route, "methods"):
            assert "DELETE" not in route.methods, route.path
