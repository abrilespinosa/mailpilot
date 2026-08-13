"""
Conexión a PostgreSQL.

Punto único donde se crea el engine y las sesiones de SQLAlchemy. Ningún otro
módulo debería leer DATABASE_URL ni construir engines por su cuenta.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Carga .env de la raíz del proyecto. Las variables que ya existan en el
# entorno tienen prioridad, que es lo que permitirá sobrescribirlas en tests
# o en Docker sin tocar el archivo.
load_dotenv(PROJECT_ROOT / ".env")


def get_database_url() -> str:
    """
    Devuelve la cadena de conexión, o falla con un mensaje útil si no está.

    Fallar aquí y pronto es mejor que dejar que SQLAlchemy reviente más tarde
    con un error críptico sobre un dialecto vacío.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Falta DATABASE_URL. Copia .env.example a .env y rellénalo:\n"
            "  cp .env.example .env"
        )
    return url


# echo=False: pon True temporalmente si quieres ver por pantalla el SQL que
# SQLAlchemy genera. Es la mejor forma de entender qué está haciendo por dentro.
engine = create_engine(get_database_url(), echo=False, future=True)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
