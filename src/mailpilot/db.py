"""
Conexión a PostgreSQL.

Punto único donde se crea el engine y las sesiones de SQLAlchemy. Ningún otro
módulo debería leer DATABASE_URL ni construir engines por su cuenta.
"""

import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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


def get_session() -> Iterator[Session]:
    """
    Abre una sesión por petición HTTP y la cierra al terminar, pase lo que pase.

    Vive aquí y no en api.py porque ahora la usan dos módulos: la API JSON y el
    dashboard. Si cada uno definiera la suya, los tests tendrían que sustituir
    dos dependencias distintas para apuntar a la base de datos de pruebas, y
    olvidarse de una significaría escribir en la base de datos REAL sin darse
    cuenta.
    """
    with SessionLocal() as session:
        yield session
