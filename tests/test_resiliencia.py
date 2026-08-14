"""
Qué pasa cuando Gmail dice que no.

Estos dos fallos no se habrían visto nunca con veinte correos. Aparecen al
procesar miles, que es justo cuando ya no estás mirando la pantalla:

- El token de OAuth caduca cada 7 días (modo Testing). Si el servidor entrara
  en el flujo de navegador, la petición HTTP se colgaría para siempre.
- Gmail limita el ritmo por usuario. Con 2.000 correos son más de 3.000
  llamadas, y los 429 están garantizados. Tratarlos como un error definitivo
  mataba la acción sin forma de recuperarla.
"""

import pytest
from googleapiclient.errors import HttpError

from mailpilot import auth, gmail_actions
from mailpilot.models import (
    AuditLog,
    Category,
    GmailAction,
    GmailActionStatus,
    GmailActionType,
)
from sqlalchemy import select

from tests.test_gmail_actions import (
    FakeLabels,
    FakeWritableMessages,
    FakeWritableService,
    encolar,
    preparar_decidida,
)

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# El token caducado no puede colgar una petición HTTP
# ---------------------------------------------------------------------------


def test_sin_navegador_disponible_avisa_en_vez_de_bloquear(monkeypatch, tmp_path):
    """
    EL FALLO QUE HABRÍA APARECIDO UN MARTES CUALQUIERA.

    `flow.run_local_server()` se queda esperando un callback del navegador. En
    un script está bien: hay una persona delante. En una petición HTTP ese
    callback no llega nunca, así que la petición no termina, el botón del
    dashboard gira para siempre y no hay ni un mensaje que lo explique.

    Con `interactivo=False` sale una excepción con la instrucción exacta.
    """
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "no-existe.json")

    def no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("¡Ha intentado abrir el navegador desde el servidor!")

    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file",
                        no_deberia_llamarse)

    with pytest.raises(auth.NecesitaReautenticacion) as fallo:
        auth.get_credentials(interactivo=False)

    # El mensaje tiene que decir QUÉ hacer, no solo que algo va mal.
    assert "scripts/test_auth.py" in str(fallo.value)


def test_los_scripts_si_pueden_abrir_el_navegador(monkeypatch, tmp_path):
    """
    El defecto sigue siendo el permisivo: `scripts/` lo lanza una persona en
    una terminal, y ahí reautenticar es exactamente lo que se quiere.
    """
    monkeypatch.setattr(auth, "TOKEN_PATH", tmp_path / "no-existe.json")
    monkeypatch.setattr(auth, "CLIENT_SECRET_PATH", tmp_path / "tampoco.json")

    # Llega hasta el punto de buscar el client_secret, que es más allá de donde
    # habría cortado `interactivo=False`.
    with pytest.raises(FileNotFoundError):
        auth.get_credentials()


def test_el_servidor_nunca_pide_credenciales_interactivas():
    """
    Guardarraíl de código fuente, como `test_limites_gmail.py`.

    La API construye su cliente en UN solo sitio, `_servicio_gmail()`, y ese
    sitio pasa `interactivo=False`. Si alguien añadiera otro `get_service()`
    suelto en api.py, volvería el cuelgue sin que nadie se enterase.
    """
    from pathlib import Path

    fuente = (
        Path(__file__).resolve().parents[1] / "src" / "mailpilot" / "api.py"
    ).read_text()

    llamadas = [
        linea.strip()
        for linea in fuente.splitlines()
        if "get_service(" in linea and not linea.lstrip().startswith("#")
    ]

    assert llamadas == ["return get_service(interactivo=False)"], (
        "La API solo puede construir el cliente de Gmail en _servicio_gmail(), "
        f"y siempre con interactivo=False. Encontrado: {llamadas}"
    )


# ---------------------------------------------------------------------------
# Pasajero contra definitivo
# ---------------------------------------------------------------------------


class RespuestaFalsa:
    def __init__(self, status):
        self.status = status
        self.reason = "de mentira"


def error_http(codigo):
    return HttpError(RespuestaFalsa(codigo), b"{}")


class MessagesQueFallan(FakeWritableMessages):
    """Falla siempre con el error que se le diga."""

    def __init__(self, error):
        super().__init__()
        self._error = error

    def modify(self, **kwargs):
        raise self._error


@pytest.mark.parametrize("codigo", [429, 500, 502, 503, 504])
def test_los_errores_pasajeros_no_matan_la_accion(session, codigo):
    """
    LA RAZÓN DE SER DE TODO ESTO.

    Un 429 significa "vas muy rápido", no "esto está mal". Antes acababa en
    `failed` igual que un 404, y no había ningún camino para reintentarlo:
    ni endpoint ni función. Cada límite de ritmo era una acción perdida.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        MessagesQueFallan(error_http(codigo)),
        FakeLabels({"Promociones": "Label_7"}),
    )

    with pytest.raises(gmail_actions.ErrorPasajero):
        gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.PENDING   # se reintentará sola
    assert accion.intentos == 1
    assert accion.detail["pasajero"] is True


@pytest.mark.parametrize("codigo", [400, 403, 404])
def test_los_errores_definitivos_si_marcan_fallo(session, codigo):
    """
    Un correo que ya no existe no mejora reintentando. Repetirlo cinco veces
    solo gasta cuota y esconde el problema real.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        MessagesQueFallan(error_http(codigo)),
        FakeLabels({"Promociones": "Label_7"}),
    )

    gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.FAILED
    assert accion.intentos == 1


def test_un_fallo_pasajero_eterno_acaba_rindiendose(session):
    """
    El tope existe para que una acción imposible no bloquee la cola para
    siempre. Reintentar sin límite es otra forma de colgarse.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        MessagesQueFallan(error_http(429)),
        FakeLabels({"Promociones": "Label_7"}),
    )

    for _ in range(gmail_actions.MAX_INTENTOS - 1):
        with pytest.raises(gmail_actions.ErrorPasajero):
            gmail_actions.ejecutar(session, service, accion)
        assert accion.status is GmailActionStatus.PENDING

    # El último no relanza: se rinde y lo deja escrito.
    gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.FAILED
    assert accion.intentos == gmail_actions.MAX_INTENTOS


def test_un_corte_de_red_cuenta_como_pasajero(session):
    """
    Que se caiga el wifi no dice nada sobre si la acción era válida. Estos
    errores no traen código HTTP, así que se reconocen por su tipo.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        MessagesQueFallan(ConnectionError("se cayó la red")),
        FakeLabels({"Promociones": "Label_7"}),
    )

    with pytest.raises(gmail_actions.ErrorPasajero):
        gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.PENDING


def test_el_reintento_queda_en_el_audit_log(session):
    """Un reintento silencioso es indistinguible de que no pase nada."""
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        MessagesQueFallan(error_http(429)),
        FakeLabels({"Promociones": "Label_7"}),
    )

    with pytest.raises(gmail_actions.ErrorPasajero):
        gmail_actions.ejecutar(session, service, accion)

    registro = session.execute(
        select(AuditLog).where(AuditLog.event_type == "gmail_action_reintentable")
    ).scalars().one()
    assert registro.detail["intentos"] == 1
    assert registro.email_id == email.id
