"""
Ingestión completa: Gmail -> parsing -> PostgreSQL.

Ejecútalo dos veces seguidas: la segunda no debe crear correos nuevos.
Eso es la idempotencia funcionando.
"""

import argparse

from mailpilot.db import SessionLocal
from mailpilot.gmail import fetch_messages, get_service, list_message_ids
from mailpilot.repository import upsert_emails


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=20, help="cuántos correos traer (por defecto 20)"
    )
    args = parser.parse_args()

    service = get_service()

    ids = list_message_ids(service, limit=args.limit)
    print(f"Gmail ha devuelto {len(ids)} IDs. Descargando...")

    emails = fetch_messages(service, ids)
    print(f"{len(emails)} correos parseados. Guardando...")

    with SessionLocal() as session:
        inserted, updated = upsert_emails(session, emails)

    print(f"\n  nuevos:       {inserted}")
    print(f"  actualizados: {updated}")


if __name__ == "__main__":
    main()
