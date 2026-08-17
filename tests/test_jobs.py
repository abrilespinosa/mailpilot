"""
Tests del botón "Cargar correos".

Lo que se prueba aquí no es que traiga correos —eso ya lo cubren los tests de
ingestión y de clasificación— sino las tres formas en que este botón puede
hacer daño si nadie lo vigila:

1. Colgarse esperando un navegador que en un servidor nunca llega.
2. Arrancar dos tandas a la vez y clasificar los mismos correos dos veces.
3. Morirse en silencio y dejar la barra girando para siempre.
"""

import pytest
from fastapi.testclient import TestClient

from mailpilot import jobs
from mailpilot.api import app, get_session
from mailpilot.auth import NecesitaReautenticacion


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def estado_limpio():
    """
    El progreso vive en un diccionario de módulo, así que hay que dejarlo como
    estaba. Sin esto, un test que arranca una carga contamina al siguiente y
    los fallos dependen del orden.
    """
    yield
    jobs._estado.update(
        estado="inactivo", hechos=0, total=0, nuevos=0, mensaje="",
        empezado=None, segundos_por_correo=None,
    )


pytestmark = pytest.mark.db


def test_un_token_caducado_sale_como_503_con_instrucciones(client, monkeypatch):
    """
    EL FALLO QUE SE REPITE CADA SEMANA.

    Con el OAuth en modo Testing el refresh token caduca a los 7 días, así que
    este camino se recorre constantemente. Tiene que explicarse solo: si saliera
    un 500 genérico, la barra se quedaría parada sin decir qué hacer.

    Y sobre todo: NO puede acabar en `flow.run_local_server()`, que se queda
    esperando un callback del navegador que a un servidor no le llega nunca.
    """
    def caducado(*args, **kwargs):
        raise NecesitaReautenticacion(
            "El token de Gmail ha caducado o no existe. Reautentica "
            "desde una terminal:  python scripts/test_auth.py"
        )

    monkeypatch.setattr("mailpilot.api.get_service", caducado)

    respuesta = client.post("/jobs/load")

    assert respuesta.status_code == 503
    assert "Reautentica" in respuesta.json()["detail"]
    assert "scripts/test_auth.py" in respuesta.json()["detail"]


def test_no_deja_arrancar_dos_cargas_a_la_vez(client, monkeypatch):
    """
    Dos tandas en paralelo se pisarían la cuenta del progreso y clasificarían
    los mismos correos dos veces. La segunda pulsación tiene que rebotar, no
    hacer daño en silencio.
    """
    monkeypatch.setattr("mailpilot.api.get_service", lambda *a, **k: object())
    jobs._estado.update(estado="clasificando")

    respuesta = client.post("/jobs/load")

    assert respuesta.status_code == 409
    assert "en marcha" in respuesta.json()["detail"]


def test_el_estado_calcula_porcentaje_y_lo_que_queda():
    """
    La cuenta se hace en el servidor, no en el navegador, para que viva en un
    solo sitio.
    """
    jobs._estado.update(
        estado="clasificando", hechos=25, total=100, segundos_por_correo=10.0
    )

    estado = jobs.estado_actual()

    assert estado["porcentaje"] == 25
    assert estado["segundos_restantes"] == 750  # 75 correos x 10 s
    assert estado["ocupado"] is True


def test_sin_total_no_divide_entre_cero():
    """Al arrancar, `total` es 0. Es el caso que rompería la barra."""
    jobs._estado.update(estado="trayendo", hechos=0, total=0)

    estado = jobs.estado_actual()

    assert estado["porcentaje"] == 0
    assert estado["segundos_restantes"] is None


def test_un_fallo_en_el_fondo_llega_a_la_pantalla(monkeypatch):
    """
    Un hilo de fondo que muere en silencio deja la barra girando para siempre,
    que es la peor forma de fallar: no se puede ni saber que ha fallado.
    """
    def revienta(*args, **kwargs):
        raise RuntimeError("Gmail no contesta")

    monkeypatch.setattr("mailpilot.jobs.list_message_ids", revienta)

    jobs.cargar_y_clasificar(service=object(), limite=10)

    estado = jobs.estado_actual()
    assert estado["estado"] == "error"
    assert "Gmail no contesta" in estado["mensaje"]
    assert estado["ocupado"] is False


def test_el_tope_por_tanda_es_de_cien(client, monkeypatch):
    """
    Cien no es un límite técnico: es que un lote más grande tarda tanto que
    deja de ser "cargar correos" y pasa a ser un proceso nocturno.
    """
    monkeypatch.setattr("mailpilot.api.get_service", lambda *a, **k: object())
    monkeypatch.setattr("mailpilot.jobs.cargar_y_clasificar", lambda *a, **k: None)

    assert jobs.MAXIMO_POR_TANDA == 100
    assert client.post("/jobs/load?limit=101").status_code == 422
    assert client.post("/jobs/load?limit=100").status_code == 200
