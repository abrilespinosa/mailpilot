"""rename category banco to tramites

Revision ID: c2b681487998
Revises: 297e22f8a8fd
Create Date: 2026-08-13 22:17:37.851470

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2b681487998'
down_revision: Union[str, Sequence[str], None] = '297e22f8a8fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Renombra el valor 'banco' del enum a 'tramites'.

    Escrita a mano, no con --autogenerate: Alembic no detecta el renombrado de
    un valor de enum. Lo que haría sería borrar el tipo y crearlo de nuevo, y
    eso se lleva por delante las filas que ya lo usan.

    ALTER TYPE ... RENAME VALUE cambia la etiqueta en el sitio. Las filas que
    dicen 'banco' pasan a decir 'tramites' solas, sin UPDATE ni pérdida de
    datos, porque por dentro PostgreSQL guarda una referencia al valor, no el
    texto.
    """
    op.execute("ALTER TYPE category RENAME VALUE 'banco' TO 'tramites'")


def downgrade() -> None:
    """Vuelve a 'banco'. Igual de seguro y sin pérdida de datos."""
    op.execute("ALTER TYPE category RENAME VALUE 'tramites' TO 'banco'")
