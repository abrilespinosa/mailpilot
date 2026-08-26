"""
Deja MailPilot en cero para construir un conjunto de entrenamiento fiable.

POR QUÉ EXISTE ESTE SCRIPT
--------------------------
Las etiquetas acumuladas hasta ahora no sirven para entrenar. Unas se
decidieron viendo lo que proponía el modelo (ancladas), otras vienen de la
taxonomía de siete categorías migrada por reglas de remitente, y NINGUNA lleva
registro de cuál es cuál. Entrenar con ellas sería enseñarle al modelo nuevo a
imitar los sesgos del viejo, sin forma de saber cuánto.

Así que se empieza de cero: se quitan las etiquetas de Gmail, se borran las
clasificaciones y las propuestas, y la usuaria etiqueta a mano desde el
dashboard con `decidido_a_ciegas` registrado desde el primer correo.

QUÉ NO SE BORRA
---------------
- La tabla `emails`. Son 2.498 correos ya descargados; volver a pedirlos a
  Gmail no aporta nada y cuesta horas.
- La papelera. Tirar y clasificar son dos ejes distintos (ADR 002): un correo
  tirado se tiró por un motivo que este reseteo no cuestiona. Ni se sacan de
  la papelera ni se les quita nada.
- Las etiquetas propias de la usuaria en Gmail. La lista blanca cerrada de
  `gmail_actions` lo impide por construcción.

Uso:
    python scripts/reset_entrenamiento.py              # simulacro, no toca nada
    python scripts/reset_entrenamiento.py --gmail      # limpia SOLO Gmail
    python scripts/reset_entrenamiento.py --bd         # limpia SOLO la base
    python scripts/reset_entrenamiento.py --gmail --bd # las dos cosas
"""

import argparse
import sys

from sqlalchemy import func, select

from mailpilot.db import SessionLocal
from mailpilot.gmail import get_service
from mailpilot.gmail_actions import (
    NUESTRAS_ETIQUETAS,
    etiquetas_existentes,
    quitar_nuestras_etiquetas,
)
from mailpilot.models import ActionProposal, AuditLog, Classification, Email, GmailAction


def ids_etiquetados(service, cache: dict[str, str]) -> set[str]:
    """
    Los ids de Gmail que llevan alguna etiqueta nuestra.

    Se le pregunta a GMAIL, no a la base de datos. La base sabe lo que MailPilot
    hizo; Gmail sabe lo que hay. Si alguna vez se aplicó una etiqueta y la fila
    se perdió, esta consulta la encuentra igual y el reseteo queda completo.
    """
    encontrados: set[str] = set()

    for nombre, label_id in cache.items():
        if nombre not in NUESTRAS_ETIQUETAS:
            continue

        token = None
        while True:
            respuesta = (
                service.users()
                .messages()
                .list(userId="me", labelIds=[label_id], maxResults=500, pageToken=token)
                .execute()
            )
            for mensaje in respuesta.get("messages", []):
                encontrados.add(mensaje["id"])

            token = respuesta.get("nextPageToken")
            if not token:
                break

    return encontrados


def limpiar_gmail(simulacro: bool) -> None:
    servicio = get_service()
    cache = etiquetas_existentes(servicio)

    nuestras = {n: i for n, i in cache.items() if n in NUESTRAS_ETIQUETAS}
    print(f"Etiquetas nuestras en Gmail: {sorted(nuestras)}")

    ids = ids_etiquetados(servicio, cache)
    print(f"Correos con alguna etiqueta nuestra: {len(ids)}")

    if simulacro:
        print("\n[SIMULACRO] No se ha tocado nada. Añade --gmail para ejecutar.")
        return

    fallos = 0
    for numero, message_id in enumerate(sorted(ids), start=1):
        try:
            quitar_nuestras_etiquetas(servicio, message_id, cache)
        except Exception as error:  # noqa: BLE001
            fallos += 1
            print(f"  FALLO en {message_id}: {type(error).__name__}: {error}")

        if numero % 50 == 0:
            print(f"  {numero}/{len(ids)}…")

    print(f"\nGmail limpio: {len(ids) - fallos} correos devueltos a Recibidos, {fallos} fallos.")


def limpiar_bd(simulacro: bool) -> None:
    with SessionLocal() as session:
        cuentas = {
            "clasificaciones": session.scalar(select(func.count()).select_from(Classification)),
            "propuestas": session.scalar(select(func.count()).select_from(ActionProposal)),
            "acciones_gmail": session.scalar(select(func.count()).select_from(GmailAction)),
            "audit_logs": session.scalar(select(func.count()).select_from(AuditLog)),
            "correos (SE CONSERVAN)": session.scalar(select(func.count()).select_from(Email)),
        }

        for nombre, cuantos in cuentas.items():
            print(f"  {nombre:26} {cuantos}")

        if simulacro:
            print("\n[SIMULACRO] No se ha borrado nada. Añade --bd para ejecutar.")
            return

        # El orden importa: AuditLog referencia a ActionProposal, así que si se
        # borrara la propuesta primero saltaría la clave ajena.
        borrados_audit = session.query(AuditLog).delete(synchronize_session=False)
        borradas_acciones = session.query(GmailAction).delete(synchronize_session=False)
        borradas_propuestas = session.query(ActionProposal).delete(synchronize_session=False)
        borradas_clasif = session.query(Classification).delete(synchronize_session=False)

        # `en_papelera` es ESTADO ACTUAL de Gmail, no una decisión de MailPilot
        # (ADR 002). Como no se saca nada de la papelera, sigue siendo cierto y
        # no se toca. Reescribirlo aquí dejaría el dato mintiendo.

        session.commit()

        print(
            f"\nBase limpia: {borradas_clasif} clasificaciones, "
            f"{borradas_propuestas} propuestas, {borradas_acciones} acciones, "
            f"{borrados_audit} entradas de auditoría."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gmail", action="store_true", help="quita las etiquetas en Gmail")
    parser.add_argument("--bd", action="store_true", help="borra clasificaciones y propuestas")
    args = parser.parse_args()

    simulacro = not (args.gmail or args.bd)
    if simulacro:
        print("=== SIMULACRO: nada se modifica ===\n")

    print("--- GMAIL ---")
    try:
        limpiar_gmail(simulacro=not args.gmail)
    except Exception as error:  # noqa: BLE001
        print(f"No se pudo hablar con Gmail: {type(error).__name__}: {error}")
        if args.gmail:
            sys.exit(1)

    print("\n--- BASE DE DATOS ---")
    limpiar_bd(simulacro=not args.bd)


if __name__ == "__main__":
    main()
