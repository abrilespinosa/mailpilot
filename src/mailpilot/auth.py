"""
Gestión de credenciales OAuth 2.0 para la Gmail API.

Este módulo es el ÚNICO punto del sistema que debe manejar tokens de Gmail.
Ningún otro componente (IA, base de datos, API) debe leer credentials/ directamente.
"""

import json
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# gmail.modify: etiquetar y mover a papelera. Decisión documentada en
# docs/decisions/003-scope-gmail-modify.md.
#
# Es el scope MÍNIMO que permite lo que necesita la Fase 9, y esa minimalidad
# es una barrera de seguridad, no papeleo: `users.messages.delete` (borrado
# permanente) exige el scope completo `https://mail.google.com/`, que NO
# pedimos. Con esto, aunque alguien escribiera esa llamada por error, Google la
# rechazaría. La regla "nunca borrado permanente" deja de depender de nuestra
# disciplina.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_DIR = Path(__file__).resolve().parents[2] / "credentials"
CLIENT_SECRET_PATH = CREDENTIALS_DIR / "client_secret.json"
TOKEN_PATH = CREDENTIALS_DIR / "token.json"


def _token_tiene_los_scopes() -> bool:
    """
    ¿El token guardado fue emitido con los permisos que pedimos ahora?

    Hay que leer el JSON a mano, y esto es una trampa fea de la librería:
    `Credentials.from_authorized_user_file(ruta, SCOPES)` se queda con los
    SCOPES que le pasas y descarta los del archivo, así que `creds.scopes`
    devuelve lo que pediste, NUNCA lo que Google concedió. Comprobarlo ahí
    siempre da que sí, aunque el token sea de solo lectura.

    Sin esta comprobación, ampliar permisos falla de dos formas confusas:
    `invalid_scope` al refrescar, o un 403 al escribir muy lejos de la causa.
    Los scopes no se amplían refrescando; hay que volver a pasar por el
    navegador.
    """
    try:
        info = json.loads(TOKEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return False

    concedidos = set(info.get("scopes") or [])
    faltan = set(SCOPES) - concedidos

    if faltan:
        print(f"El token guardado no incluye: {', '.join(sorted(faltan))}")
        print("Los permisos no se amplían refrescando. Reautenticando...")
        return False

    return True


class NecesitaReautenticacion(Exception):
    """
    Hace falta el flujo de navegador y quien llamó dijo que no podía abrirlo.

    Existe para que el servidor web NUNCA acabe dentro de `run_local_server`:
    esa llamada se queda esperando un callback del navegador, y en una petición
    HTTP ese callback no llega jamás. La petición se colgaría, el botón del
    dashboard giraría para siempre y no habría ni un mensaje que explicara nada.

    Con el OAuth en modo Testing el refresh token caduca a los 7 días, así que
    esto no es un caso raro: pasa cada semana.
    """


def get_credentials(interactivo: bool = True) -> Credentials:
    """
    Devuelve credenciales válidas, reutilizando el token guardado si existe
    y sigue siendo válido, o refrescándolo si ha caducado.

    `interactivo=True` (el defecto) permite abrir el navegador si no queda otra.
    Es lo que quieren los scripts de `scripts/`, que los lanza una persona
    delante de una terminal.

    `interactivo=False` lo prohíbe y lanza `NecesitaReautenticacion`. Es lo que
    tiene que usar CUALQUIER código que atienda una petición HTTP. El defecto es
    el permisivo porque romper los scripts sería peor, pero el servidor lo pone
    explícito y hay un test que lo vigila.
    """
    creds: Credentials | None = None

    if TOKEN_PATH.exists() and _token_tiene_los_scopes():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as error:
                # Segunda red, por si el token está bien de scopes pero Google
                # lo ha revocado: en modo Testing el refresh token caduca a los
                # 7 días. Reautenticar es la respuesta correcta a las dos cosas.
                print(f"No se pudo refrescar el token ({error}). Reautenticando...")
                creds = None

        if not creds:
            # EL CORTE. Antes de tocar nada que pueda bloquear, comprobar si
            # quien llama puede permitirse un navegador. El servidor no.
            if not interactivo:
                raise NecesitaReautenticacion(
                    "El token de Gmail ha caducado o no existe. Reautentica "
                    "desde una terminal:  python scripts/test_auth.py"
                )

            if not CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"No encuentro {CLIENT_SECRET_PATH}. "
                    "Copia ahí el JSON descargado de Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)

        TOKEN_PATH.write_text(creds.to_json())

    return creds
