"""
Genera propuestas a partir de las clasificaciones existentes.

No toca Gmail ni decide nada: solo deja filas pendientes esperando a que la
usuaria opine, desde el dashboard o desde la API.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mailpilot.db import SessionLocal
from mailpilot.repository import generar_propuestas, propuestas_pendientes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    with SessionLocal() as session:
        creadas = generar_propuestas(session, limit=args.limit)
        pendientes = propuestas_pendientes(session, limit=5)
        total = len(propuestas_pendientes(session, limit=10_000))

        print(f"propuestas nuevas: {creadas}")
        print(f"pendientes totales: {total}\n")

        if pendientes:
            print("Primeras pendientes:")
            for p in pendientes:
                print(
                    f"  [{p.id:4}] {p.category.value:12} {p.confidence:.2f}  "
                    f"{p.email.subject[:44]}"
                )


if __name__ == "__main__":
    main()
