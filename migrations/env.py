import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# src/ no es un paquete instalado, así que hay que ponerlo en el path antes
# de poder importar mailpilot desde aquí.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mailpilot.db import get_database_url  # noqa: E402
from mailpilot.models import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# La URL de conexión sale de .env, no de alembic.ini. Así la contraseña vive
# en un único sitio y en un archivo que git ignora.
config.set_main_option("sqlalchemy.url", get_database_url())

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Los modelos contra los que Alembic compara la base de datos para generar
# las migraciones automáticamente.
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Sin esto, Alembic detecta columnas nuevas y borradas pero NO
            # los cambios de tipo de una columna existente.
            compare_type=True,
            # UNA TRANSACCIÓN POR MIGRACIÓN, no una para todas.
            #
            # Por defecto Alembic mete todas las migraciones pendientes en una
            # sola transacción. Eso choca de frente con PostgreSQL, donde un
            # valor de enum recién creado NO se puede usar hasta que su
            # transacción confirme: el error es
            #
            #     UnsafeNewEnumValueUsage: unsafe use of new value "seguridad"
            #
            # y aparece aunque el ADD VALUE y el UPDATE estén en migraciones
            # distintas, porque las dos iban dentro de la misma transacción.
            # Partir en dos migraciones es necesario pero no basta; esto es la
            # otra mitad.
            #
            # El precio: si una migración falla, las anteriores ya están
            # confirmadas y no se deshacen solas. A cambio, el estado en que se
            # queda la base es el de una migración concreta, que es
            # exactamente lo que `alembic current` sabe leer.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
