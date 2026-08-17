"""ten categories, part 1: rename trabajo and add the three new values

Revision ID: d1a4e7c93f20
Revises: c6bfeb1778c7
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1a4e7c93f20'
down_revision: Union[str, Sequence[str], None] = 'c6bfeb1778c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Primera mitad del ADR 006: deja el enum con los diez valores.

    POR QUÉ ESTO VA SEPARADO DE MOVER LOS DATOS
    -------------------------------------------
    En PostgreSQL, un valor añadido con ALTER TYPE ... ADD VALUE **no se puede
    usar en la misma transacción en que se crea**. Alembic ejecuta cada
    migración dentro de una transacción, así que si aquí mismo intentáramos un
    `UPDATE ... SET final_category = 'boletines'`, fallaría con un error que no
    se parece en nada a la causa. Por eso hay una segunda migración.

    Escrita a mano y no con --autogenerate: Alembic no entiende de cambios en
    valores de enum. Lo que generaría sería borrar el tipo y recrearlo, que se
    lleva por delante las 738 filas que ya lo usan.

    `empleo` es un RENAME, no un valor nuevo. La categoría se llamaba `trabajo`
    y el nombre mentía: de sus 80 correos, 75 eran de portales de empleo.
    Renombrar cambia la etiqueta en el sitio y las filas siguen válidas sin un
    solo UPDATE, porque por dentro PostgreSQL guarda una referencia al valor y
    no el texto.
    """
    op.execute("ALTER TYPE category RENAME VALUE 'trabajo' TO 'empleo'")

    # IF NOT EXISTS para que la migración se pueda repetir sin romperse. Sin
    # él, aplicarla dos veces (o sobre una base a medio migrar) revienta.
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'seguridad'")
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'boletines'")
    op.execute("ALTER TYPE category ADD VALUE IF NOT EXISTS 'social'")


def downgrade() -> None:
    """
    Deshace el rename. Los tres valores nuevos NO se pueden quitar.

    PostgreSQL no tiene ALTER TYPE ... DROP VALUE. Quitar un valor de un enum
    exige recrear el tipo entero y reescribir todas las columnas que lo usan,
    y eso falla igualmente si queda una sola fila usándolo.

    Es la razón de que el ADR 006 pidiera confirmar los diez nombres ANTES de
    escribir esto: añadir es barato, quitar no se puede.
    """
    op.execute("ALTER TYPE category RENAME VALUE 'empleo' TO 'trabajo'")
