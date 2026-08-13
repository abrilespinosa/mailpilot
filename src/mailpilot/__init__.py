"""
MailPilot — capa de gestión inteligente sobre Gmail.

Carga el .env aquí, al importar el paquete, y no en cada módulo. Python
ejecuta este archivo antes que cualquier submódulo, así que garantiza que las
variables de entorno están disponibles sin importar el orden de los imports.
"""

from pathlib import Path

from dotenv import load_dotenv

# override=False: si la variable ya existe en el entorno, gana esa. Permite
# sobrescribir configuración en tests o en Docker sin tocar el archivo.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
