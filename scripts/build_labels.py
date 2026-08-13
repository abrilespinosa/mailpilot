"""
Genera evaluation/labels.json a partir de las etiquetas propuestas a mano.

Las etiquetas de este archivo son PROPUESTAS, no verdad absoluta. La usuaria
debe revisarlas: es su bandeja y su criterio el que define qué es correcto.
Editar directamente evaluation/labels.json; este script solo lo crea la
primera vez y no sobrescribe si ya existe.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from mailpilot.db import SessionLocal
from mailpilot.models import Email

SALIDA = Path(__file__).resolve().parents[1] / "evaluation" / "labels.json"

# id de la tabla emails -> categoría correcta
ETIQUETAS = {
    1: "avisos",        # Goodreads, resumen social automático
    2: "banco",
    3: "promociones",
    4: "trabajo",       # Yobalia es un portal de empleo
    5: "promociones",
    6: "promociones",
    7: "promociones",
    8: "promociones",
    9: "trabajo",
    10: "promociones",
    11: "promociones",
    12: "promociones",
    13: "trabajo",
    14: "avisos",       # código de verificación
    15: "avisos",       # alerta de seguridad de Google, NO banco
    16: "avisos",
    17: "trabajo",
    18: "promociones",
    19: "avisos",       # alta de cuenta
    20: "avisos",
    61: "avisos",       # cambio de términos de uso
    62: "promociones",
    63: "trabajo",      # inscripción en ofertas de empleo
    64: "promociones",  # BBVA haciendo marketing, no una gestión bancaria
    65: "avisos",       # password reset
    66: "banco",        # trámite de una ayuda pública
    67: "avisos",
    68: "trabajo",
    69: "trabajo",
    70: "promociones",
    71: "avisos",
    72: "trabajo",
    73: "avisos",
    74: "trabajo",
    75: "promociones",
    76: "trabajo",
    77: "promociones",
    78: "avisos",
    79: "avisos",       # verificación de correo
    80: "promociones",  # onboarding comercial de Docker
    81: "avisos",       # código de un solo uso
    82: "avisos",
    83: "avisos",       # petición de reseña tras una compra
    84: "avisos",
    85: "avisos",       # digest de Substack, suscrito a propósito
    86: "trabajo",
    87: "promociones",
    88: "trabajo",
    89: "promociones",
    90: "avisos",       # digest de LeetCode, suscrito a propósito
    91: "promociones",
    92: "personal",     # una persona real comparte una carpeta familiar
    93: "avisos",       # encuesta de satisfacción
    94: "trabajo",
    95: "compras",      # entradas de cine reenviadas
    96: "compras",
    97: "compras",
    98: "compras",
    99: "avisos",       # recuperación de contraseña
    100: "compras",     # comprobante de pago
    101: "avisos",
    102: "avisos",
    103: "promociones",
    104: "promociones",
    105: "avisos",
    106: "promociones",
    107: "promociones",
    108: "trabajo",
    109: "promociones",
    110: "avisos",      # mención en Discord
    111: "compras",     # ticket de una compra
    112: "trabajo",
    113: "promociones",
    114: "avisos",
    115: "trabajo",
    116: "avisos",
    117: "promociones",
    118: "promociones",
    119: "avisos",
    120: "avisos",
}

# Casos donde la definición del ADR 001 no decide claramente. Se marcan para
# que la usuaria los revise primero: son los que más valor tiene aclarar,
# porque su respuesta debería acabar en el propio ADR.
DUDOSOS = {
    66: "¿un trámite con la administración es 'banco'? La definición dice "
        "'trámites', pero suena raro",
    83: "petición de reseña tras comprar: ¿avisos o compras?",
    85: "digest de Substack al que te suscribiste: el ADR mete 'newsletters' "
        "en promociones, pero dice 'no solicitadas'",
    90: "mismo caso que Substack",
    92: "una persona real, pero llega como notificación automática de Drive",
    95: "te lo reenviaste tú: ¿personal por remitente o compras por contenido?",
    116: "mismo caso que Substack",
}


def main():
    if SALIDA.exists():
        print(f"{SALIDA} ya existe. No lo sobrescribo.")
        print("Edítalo a mano si quieres cambiar etiquetas.")
        return

    SALIDA.parent.mkdir(exist_ok=True)

    with SessionLocal() as session:
        emails = session.execute(select(Email).order_by(Email.id)).scalars().all()

        registros = []
        for email in emails:
            if email.id not in ETIQUETAS:
                continue
            registro = {
                "email_id": email.id,
                "gmail_message_id": email.gmail_message_id,
                "sender": email.sender,
                "subject": email.subject,
                "expected": ETIQUETAS[email.id],
            }
            if email.id in DUDOSOS:
                registro["revisar"] = DUDOSOS[email.id]
            registros.append(registro)

    SALIDA.write_text(json.dumps(registros, indent=2, ensure_ascii=False) + "\n")

    print(f"{len(registros)} etiquetas escritas en {SALIDA}")
    print(f"{len(DUDOSOS)} marcadas como dudosas para revisar")


if __name__ == "__main__":
    main()
