"""add restore_from_trash action

Revision ID: c1a7de930b44
Revises: b5de04e85c16
Create Date: 2026-08-14

Escrita a mano: Alembic NO detecta los valores nuevos de un ENUM al comparar
modelos, así que un autogenerate aquí habría salido vacío y la aplicación
habría reventado al insertar el valor nuevo.
"""
from alembic import op

revision = "c1a7de930b44"
down_revision = "b5de04e85c16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS lo hace repetible. En PostgreSQL moderno ALTER TYPE ... ADD
    # VALUE ya puede ir dentro de una transacción.
    op.execute("ALTER TYPE gmail_action_type ADD VALUE IF NOT EXISTS 'restore_from_trash'")


def downgrade() -> None:
    """
    PostgreSQL NO permite quitar un valor de un ENUM.

    Deshacerlo de verdad exigiría recrear el tipo entero y reescribir la
    columna. No merece la pena por un valor de más que nadie usaría: dejarlo
    ahí es inofensivo.
    """
    pass
