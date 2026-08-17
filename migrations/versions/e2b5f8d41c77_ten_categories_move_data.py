"""ten categories, part 2: move the labels that the sender already decides

Revision ID: e2b5f8d41c77
Revises: d1a4e7c93f20
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e2b5f8d41c77'
down_revision: Union[str, Sequence[str], None] = 'd1a4e7c93f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Remitentes cuyo destino en la taxonomía nueva no admite duda, sacados de los
# 738 correos ya etiquetados a mano (ADR 006). No es una lista de reglas para
# clasificar de aquí en adelante: es solo para no repetir a mano un trabajo que
# la usuaria ya hizo una vez.
#
# Cada regla está ACOTADA POR LA ETIQUETA QUE ELLA PUSO, no solo por el
# remitente. Así se respeta su criterio: si marcó un correo de Twitch como
# `avisos` pasa a `social`, y si marcó otro como `otros` pasa a `boletines`.
# Un remitente puede mandar cosas distintas y ella ya las distinguió.
REGLAS = [
    # (etiqueta actual, regex sobre el remitente, etiqueta nueva)
    ("avisos", r"accounts\.google|accountprotection|apple|microsoft|supercell", "seguridad"),
    ("avisos", r"discord|facebookmail|instagram|twitch|tiktok", "social"),
    ("otros", r"goodreads|substack|leetcode|umusic|fcarreras", "boletines"),
]


def upgrade() -> None:
    """
    Segunda mitad del ADR 006: reetiqueta lo que el remitente ya decide.

    Va separada de la migración anterior porque en PostgreSQL un valor de enum
    recién creado no se puede usar hasta que su transacción confirme.

    QUÉ SE TOCA Y QUÉ NO
    --------------------
    Solo `final_category`, que es la decisión de la usuaria.

    `category` —lo que dijo el modelo— NO se toca nunca, aquí tampoco. Es el
    registro histórico de lo que propuso, y `WHERE category <> final_category`
    es lo que da las correcciones. Reescribirlo sería inventar que el modelo
    dijo algo que no dijo.

    El efecto colateral está asumido y documentado en el ADR: al mover las
    etiquetas de la usuaria y dejar las del modelo, las estadísticas de acierto
    anteriores dejan de tener sentido. Da igual, porque comparar un modelo de
    siete categorías con etiquetas de diez no significaba nada de todos modos.

    Se migran también los correos que están en la papelera. La usuaria pidió
    ignorarlos, y se ignoran para el repaso A MANO —que es donde cuesta tiempo—,
    pero en SQL no cuesta nada y deja la base coherente.
    """
    for etiqueta_vieja, patron, etiqueta_nueva in REGLAS:
        op.execute(
            f"""
            UPDATE action_proposals AS p
               SET final_category = '{etiqueta_nueva}'
              FROM emails AS e
             WHERE e.id = p.email_id
               AND p.final_category = '{etiqueta_vieja}'
               AND lower(e.sender) ~ '{patron}'
            """
        )


def downgrade() -> None:
    """
    Devuelve las etiquetas movidas a su categoría anterior.

    Es reversible porque cada regla sabe de dónde venía. Lo que no se puede
    deshacer es la creación de los valores del enum: ver la migración anterior.
    """
    for etiqueta_vieja, patron, etiqueta_nueva in REGLAS:
        op.execute(
            f"""
            UPDATE action_proposals AS p
               SET final_category = '{etiqueta_vieja}'
              FROM emails AS e
             WHERE e.id = p.email_id
               AND p.final_category = '{etiqueta_nueva}'
               AND lower(e.sender) ~ '{patron}'
            """
        )
