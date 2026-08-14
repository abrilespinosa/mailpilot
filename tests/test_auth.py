"""
Tests de las credenciales OAuth.

No tocan la red ni el navegador: comprueban la decisión de "¿me vale el token
que tengo guardado?", que es donde estuvo el fallo real.
"""

import json

import pytest

from mailpilot import auth

MODIFY = "https://www.googleapis.com/auth/gmail.modify"
READONLY = "https://www.googleapis.com/auth/gmail.readonly"


@pytest.fixture
def token(tmp_path, monkeypatch):
    """Sustituye el token del proyecto por uno de mentira en un directorio temporal."""
    ruta = tmp_path / "token.json"
    monkeypatch.setattr(auth, "TOKEN_PATH", ruta)
    monkeypatch.setattr(auth, "SCOPES", [MODIFY])
    return ruta


def escribir(ruta, scopes):
    ruta.write_text(json.dumps({"token": "x", "refresh_token": "y", "scopes": scopes}))


def test_un_token_con_los_scopes_pedidos_vale(token):
    escribir(token, [MODIFY])

    assert auth._token_tiene_los_scopes() is True


def test_un_token_de_solo_lectura_no_vale_para_escribir(token):
    """
    EL TEST QUE IMPORTA.

    Aquí estuvo el fallo: la comprobación miraba `creds.scopes`, pero
    `Credentials.from_authorized_user_file(ruta, SCOPES)` se queda con los
    SCOPES que le pasas y descarta los del archivo. O sea que preguntaba por lo
    que habíamos pedido, no por lo que Google concedió, y siempre decía que sí.

    El resultado era un `invalid_scope` al refrescar, o un 403 al escribir muy
    lejos de la causa. Hay que leer el JSON a mano.
    """
    escribir(token, [READONLY])

    assert auth._token_tiene_los_scopes() is False


def test_sobran_scopes_pero_estan_los_necesarios(token):
    """Tener de más no estorba: se comprueba que estén los que pedimos."""
    escribir(token, [MODIFY, READONLY])

    assert auth._token_tiene_los_scopes() is True


def test_sin_token_no_vale(token):
    assert auth._token_tiene_los_scopes() is False


def test_un_token_corrupto_no_revienta(token):
    """Ante un archivo ilegible, reautenticar es mejor que propagar el error."""
    token.write_text("{ esto no es json")

    assert auth._token_tiene_los_scopes() is False


def test_el_scope_pedido_no_permite_borrado_permanente():
    """
    La barrera de seguridad más importante del proyecto, y no la pone el código.

    `users.messages.delete` (borrado permanente) exige el scope completo
    `https://mail.google.com/`. Con `gmail.modify` solo existe `messages.trash`,
    reversible 30 días en Gmail.

    Si alguien añadiera aquí el scope completo, el borrado permanente pasaría a
    ser posible aunque nadie lo implemente. Ver ADR 003.
    """
    assert auth.SCOPES == [MODIFY]
    assert "https://mail.google.com/" not in auth.SCOPES
