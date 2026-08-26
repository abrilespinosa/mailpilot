"""
Prepara correos para etiquetar A MANO desde el dashboard.

Crea propuestas SIN categoría: el modelo no opina, decide la usuaria. Es el
paso previo a entrenar, porque un conjunto de entrenamiento hecho corrigiendo
al modelo hereda sus sesgos, y no hay forma de medir cuánto.

Después de ejecutarlo, etiquetar en:
    http://localhost:8000/          (modo ciego, que aquí es lo único que hay)

Uso:
    python scripts/etiquetar.py             # prepara 50
    python scripts/etiquetar.py --limit 400 # prepara 400
"""

import argparse

from sqlalchemy import func, select

from mailpilot.db import SessionLocal
from mailpilot.models import ActionProposal, Email, ProposalStatus
from mailpilot.repository import crear_propuestas_en_blanco


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=50, help="cuántos preparar (por defecto 50)"
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        pendientes_antes = session.scalar(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.status == ProposalStatus.PENDING)
        )

        creadas = crear_propuestas_en_blanco(session, limit=args.limit)

        sin_tocar = session.scalar(
            select(func.count())
            .select_from(Email)
            .where(
                Email.en_papelera.is_(False),
                Email.id.not_in(select(ActionProposal.email_id).distinct()),
            )
        )

    print(f"Propuestas en blanco creadas: {creadas}")
    print(f"Pendientes de etiquetar ahora: {pendientes_antes + creadas}")
    print(f"Correos aún sin preparar:      {sin_tocar}")
    print("\nEtiqueta en http://localhost:8000/")
    print("Cada decisión queda marcada como tomada a ciegas.")


if __name__ == "__main__":
    main()
