"""
Gestión de credenciales OAuth 2.0 para la Gmail API.

Este módulo es el ÚNICO punto del sistema que debe manejar tokens de Gmail.
Ningún otro componente (IA, base de datos, API) debe leer credentials/ directamente.
"""

from pathlib import Path

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


def get_credentials() -> Credentials:
    """
    Devuelve credenciales válidas, reutilizando el token guardado si existe
    y sigue siendo válido, o refrescándolo si ha caducado.
    Si no hay token previo, lanza el flujo de login en el navegador.
    """
    creds: Credentials | None = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        # AMPLIAR SCOPES NO SE ARREGLA REFRESCANDO.
        #
        # `from_authorized_user_file` se limita a copiar los SCOPES que le pasas
        # al objeto, sin comprobar cuáles concedió Google de verdad. Un token
        # emitido para `readonly` seguiría pareciendo válido aquí y fallaría más
        # tarde con un 403 al intentar escribir, lejos de la causa.
        #
        # Compararlo aquí convierte un error confuso en una reautenticación
        # automática.
        concedidos = set(creds.scopes or [])
        if not set(SCOPES).issubset(concedidos):
            faltan = ", ".join(sorted(set(SCOPES) - concedidos))
            print(f"El token guardado no incluye: {faltan}. Reautenticando...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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
