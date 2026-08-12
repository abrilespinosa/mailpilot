"""
Gestión de credenciales OAuth 2.0 para la Gmail API.

Este módulo es el ÚNICO punto del sistema que debe manejar tokens de Gmail.
Ningún otro componente (IA, base de datos, API) debe leer credentials/ directamente.
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Scope mínimo: solo lectura. Lo escalaremos a gmail.modify
# en una fase posterior, de forma explícita y documentada en un ADR.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

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
