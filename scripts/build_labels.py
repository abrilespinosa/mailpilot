"""
Genera evaluation/labels.json a partir de las etiquetas puestas a mano.

Dos conjuntos separados, y la separación importa:

- dev  (80 correos): el que se usa para AFINAR el prompt. Se mira, se estudia,
  se itera sobre sus fallos.
- test (80 correos): el que se usa para COMPROBAR. No se mira al diseñar
  prompts. Si se afina mirando sus fallos, deja de servir para medir.

Sin esta separación, ajustar el prompt sobre los mismos correos con los que se
mide infla el resultado: el prompt aprende esos casos concretos en vez de la
regla general. Se llama sobreajuste al conjunto de evaluación.

Las etiquetas son PROPUESTAS. La usuaria decide: es su bandeja y su criterio.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from mailpilot.db import SessionLocal
from mailpilot.models import Email

SALIDA = Path(__file__).resolve().parents[1] / "evaluation" / "labels.json"

# --------------------------------------------------------------------------
# dev: sobre estos se afinó el prompt v2. Su 87,5% está inflado.
# --------------------------------------------------------------------------
DEV = {
    1: "otros",         # Goodreads habla de libros, no de la cuenta
    2: "tramites",
    3: "promociones",
    4: "empleo",       # Yobalia es un portal de empleo
    5: "promociones",
    6: "promociones",
    7: "promociones",
    8: "promociones",
    9: "empleo",
    10: "promociones",
    11: "promociones",
    12: "promociones",
    13: "empleo",
    14: "avisos",       # código de verificación
    15: "avisos",       # alerta de seguridad de Google, NO banco
    16: "avisos",
    17: "empleo",
    18: "promociones",
    19: "avisos",       # alta de cuenta
    20: "avisos",
    61: "avisos",       # cambio de términos de uso
    62: "promociones",
    63: "empleo",      # inscripción en ofertas de empleo
    64: "promociones",  # BBVA haciendo marketing, no una gestión bancaria
    65: "avisos",       # password reset
    66: "tramites",        # trámite de una ayuda pública
    67: "avisos",
    68: "empleo",
    69: "empleo",
    70: "promociones",
    71: "avisos",
    72: "empleo",
    73: "avisos",
    74: "empleo",
    75: "promociones",
    76: "empleo",
    77: "promociones",
    78: "avisos",
    79: "avisos",       # verificación de correo
    80: "promociones",  # onboarding comercial de Docker
    81: "avisos",       # código de un solo uso
    82: "avisos",
    83: "compras",      # reseña tras una compra (decisión de la usuaria)
    84: "avisos",
    85: "otros",        # digest de Substack (decisión de la usuaria)
    86: "empleo",
    87: "promociones",
    88: "empleo",
    89: "promociones",
    90: "otros",        # digest de LeetCode (decisión de la usuaria)
    91: "promociones",
    92: "personal",     # una persona real comparte una carpeta familiar
    93: "compras",      # encuesta post-compra, igual que la reseña de Fnac
    94: "empleo",
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
    105: "otros",       # digest de LeetCode
    106: "promociones",
    107: "promociones",
    108: "empleo",
    109: "promociones",
    110: "avisos",      # mención en Discord
    111: "compras",     # ticket de una compra
    112: "empleo",
    113: "promociones",
    114: "avisos",
    115: "empleo",
    116: "otros",       # digest de Substack
    117: "promociones",
    118: "promociones",
    119: "avisos",
    120: "avisos",
}

# --------------------------------------------------------------------------
# test: etiquetado SIN mirar los fallos del modelo. Es la medida honesta.
# --------------------------------------------------------------------------
TEST = {
    121: "empleo",
    122: "promociones",
    123: "avisos",      # aviso de que un servicio se retira
    204: "avisos",
    205: "otros",       # Goodreads: recomendación de lectura
    206: "personal",    # Lucía comparte una página de Notion
    207: "avisos",
    208: "avisos",
    209: "promociones",
    210: "otros",
    211: "otros",       # Goodreads: recomendación de lectura
    212: "promociones",
    213: "promociones",
    214: "promociones",
    215: "avisos",
    216: "avisos",
    217: "avisos",      # verificación de identidad de AWS
    218: "avisos",      # inicio de sesión
    219: "avisos",
    220: "promociones",
    221: "otros",       # Goodreads: recomendación de lectura
    222: "avisos",
    223: "tramites",       # documentación previa a una firma
    224: "promociones",
    225: "compras",     # encuesta de Fnac sobre una compra
    226: "promociones",
    227: "avisos",
    228: "avisos",
    229: "empleo",
    230: "otros",       # Goodreads: recomendación de lectura
    231: "promociones",
    232: "tramites",       # el banco avisando de una estafa
    233: "empleo",
    234: "empleo",
    235: "otros",       # Goodreads: recomendación de lectura
    236: "avisos",
    237: "personal",
    238: "promociones",
    239: "promociones",
    240: "empleo",
    241: "promociones",
    242: "tramites",       # trámite de la tarjeta del Bono Cultural
    243: "promociones",
    244: "empleo",
    245: "personal",
    246: "tramites",
    247: "personal",
    248: "avisos",      # código de un solo uso
    249: "avisos",
    250: "avisos",
    251: "avisos",      # inicio de sesión en Apple
    252: "otros",
    253: "avisos",      # cambio de términos
    254: "promociones",
    255: "promociones",
    256: "promociones",
    257: "promociones",
    258: "empleo",
    259: "promociones",  # onboarding comercial de Kaggle
    260: "avisos",       # código de InfoJobs
    261: "empleo",
    262: "tramites",        # Mónica reenvía una notificación del BBVA
    263: "avisos",
    264: "avisos",
    265: "avisos",
    266: "personal",     # correo de la usuaria a sí misma
    267: "promociones",
    268: "promociones",
    269: "promociones",
    270: "promociones",
    271: "promociones",
    272: "avisos",       # código de acceso de Inditex
    273: "tramites",
    274: "avisos",
    275: "avisos",
    276: "promociones",
    277: "promociones",  # BBVA vendiendo un producto
    278: "otros",       # Goodreads: recomendación de lectura
    279: "promociones",
    280: "promociones",  # boletín de una fundación
}

# --------------------------------------------------------------------------
# test2: conjunto LIMPIO. Etiquetado el 2026-08-13 antes de pasarle el modelo.
# Los prompts v3 y v4 se escribieron mirando los fallos de `test`, así que
# aquel dejó de servir para medir. Este solo vale mientras no se afine nada
# mirando sus resultados: en cuanto se haga, hay que crear un test3.
# --------------------------------------------------------------------------
TEST2 = {
    441: "compras",      # Enterticket, mensaje a quien tiene entradas
    442: "otros",        # Goodreads habla de libros
    443: "avisos",       # términos de servicio
    444: "avisos",
    445: "personal",     # correo de la usuaria a sí misma
    446: "personal",
    447: "empleo",
    448: "avisos",       # Pinterest actúa sobre tu cuenta
    449: "promociones",
    450: "tramites",     # una matrícula es un trámite (decisión de la usuaria)
    451: "avisos",
    452: "avisos",       # estado de una solicitud en la plataforma EMT
    453: "avisos",
    454: "avisos",       # alta en la plataforma
    455: "personal",     # Álvaro comparte una carpeta (regla 0)
    456: "avisos",       # token de GitHub
    457: "promociones",  # un reenvío se clasifica por su CONTENIDO,
                         # no por quién lo reenvía (decisión de la usuaria)
    458: "empleo",
    459: "tramites",
    460: "promociones",
    461: "avisos",       # política de privacidad
    462: "promociones",
    463: "tramites",     # igual que el 242
    464: "avisos",
    465: "empleo",      # selección de personal
    466: "promociones",
    467: "empleo",      # trabajo remunerado
    468: "avisos",       # código de recuperación
    469: "empleo",
    470: "promociones",  # el banco anunciando su app
    471: "avisos",
    472: "compras",      # encuesta de satisfacción del seguro
    473: "avisos",       # inicio de sesión
    474: "personal",     # correo de la usuaria a sí misma, igual que el 266
    475: "avisos",
    476: "tramites",     # firma de operaciones bancarias
    477: "avisos",
    478: "promociones",  # revista de una fundación
    479: "promociones",
    480: "promociones",
    481: "avisos",
    482: "tramites",     # igual que el 232
    483: "promociones",  # captación de voluntariado
    484: "personal",
    485: "avisos",       # a alguien le gustó tu publicación (regla 6)
    486: "promociones",
    487: "promociones",
    488: "tramites",
    489: "avisos",
    490: "promociones",
    491: "promociones",
    492: "promociones",  # campaña de donación de sangre
    493: "avisos",
    494: "tramites",
    495: "promociones",
    496: "promociones",  # Substack anuncia una función nueva
    497: "empleo",
    498: "empleo",
    499: "promociones",
    500: "promociones",
    501: "promociones",
    502: "avisos",       # cambio de ajustes de la cuenta
    503: "promociones",  # convocatoria de becas
    504: "tramites",
    505: "avisos",       # activación de cuenta
    506: "empleo",
    507: "empleo",
    508: "empleo",
    509: "promociones",
    510: "empleo",
    511: "compras",      # encuesta sobre un juego comprado
    512: "compras",      # encuesta post-compra
    513: "compras",
    514: "compras",
    515: "compras",
    516: "compras",
    517: "compras",
    518: "empleo",
    519: "empleo",
    520: "tramites",
}

# Casos donde la definición del ADR 001 no decide con claridad.
DUDOSOS = {
    66: "resuelto como 'tramites' porque su definición incluye 'trámites'. "
        "Reabrir si se crea una categoría propia para gestiones",
    242: "instrucciones sobre la tarjeta del Bono Cultural: ¿'tramites' por ser "
         "un trámite, o 'promociones' como el otro correo del mismo emisor?",
    262: "una persona real reenvía una notificación del banco. Puesto en "
         "'tramites' por contenido, siguiendo el criterio de las entradas de "
         "Cinesa reenviadas. ¿O 'personal' por remitente?",
    266: "correo de la usuaria a sí misma, sin extracto. Imposible de "
         "clasificar por asunto",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true", help="regenerar aunque el archivo exista"
    )
    args = parser.parse_args()

    if SALIDA.exists() and not args.force:
        print(f"{SALIDA} ya existe. Usa --force para regenerarlo.")
        return

    SALIDA.parent.mkdir(exist_ok=True)
    todas = {**{k: ("dev", v) for k, v in DEV.items()},
             **{k: ("test", v) for k, v in TEST.items()},
             **{k: ("test2", v) for k, v in TEST2.items()}}

    with SessionLocal() as session:
        emails = session.execute(select(Email).order_by(Email.id)).scalars().all()

        registros = []
        for email in emails:
            if email.id not in todas:
                continue
            split, categoria = todas[email.id]
            registro = {
                "email_id": email.id,
                "split": split,
                "gmail_message_id": email.gmail_message_id,
                "sender": email.sender,
                "subject": email.subject,
                "expected": categoria,
            }
            if email.id in DUDOSOS:
                registro["revisar"] = DUDOSOS[email.id]
            registros.append(registro)

    SALIDA.write_text(json.dumps(registros, indent=2, ensure_ascii=False) + "\n")

    por_split = {}
    for r in registros:
        por_split[r["split"]] = por_split.get(r["split"], 0) + 1

    print(f"{len(registros)} etiquetas escritas en {SALIDA}")
    for split, n in sorted(por_split.items()):
        print(f"  {split}: {n}")
    print(f"{len(DUDOSOS)} marcadas para revisar")


if __name__ == "__main__":
    main()
