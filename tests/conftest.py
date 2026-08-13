"""
Configuración compartida de los tests que tocan PostgreSQL.

Dos decisiones importantes:

1. Base de datos aparte (`mailpilot_test`). Los tests borran y crean filas
   constantemente; nunca deben hacerlo sobre la base de datos con los correos
   reales.

2. PostgreSQL de verdad, no SQLite. El esquema usa tipos ENUM nativos y JSONB,
   que SQLite no tiene. Con SQLite los tests pasarían y producción fallaría,
   que es lo peor que puede hacer un test.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from mailpilot.db import get_database_url
from mailpilot.models import Base


@pytest.fixture(scope="session")
def test_engine():
    """
    Crea la base de datos de test si no existe y monta el esquema.

    scope="session": se ejecuta una vez para toda la tanda de tests, no una vez
    por test. Crear tablas es caro.
    """
    base_url = make_url(get_database_url())
    test_url = base_url.set(database=f"{base_url.database}_test")

    # CREATE DATABASE no puede correr dentro de una transacción, y hay que
    # lanzarlo conectado a otra base de datos distinta de la que se crea.
    admin_engine = create_engine(
        base_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": test_url.database},
        ).scalar()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    admin_engine.dispose()

    engine = create_engine(test_url, future=True)

    # create_all en vez de alembic: es más rápido. A cambio, los tests no
    # comprueban que las migraciones estén al día respecto a los modelos.
    # Si eso llega a pasar factura, se cambia por "alembic upgrade head".
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    yield engine
    engine.dispose()


@pytest.fixture
def session(test_engine):
    """
    Una sesión por test, que se deshace al terminar.

    Cada test corre dentro de una transacción que se revierte al final, así que
    ningún test ve lo que escribió otro ni el orden importa.

    join_transaction_mode="create_savepoint" es la clave: el código bajo prueba
    llama a session.commit(), y sin esto ese commit cerraría la transacción
    externa y no habría nada que revertir. Con savepoints, el commit interno
    funciona de verdad pero sigue quedando dentro de la transacción del test.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()
