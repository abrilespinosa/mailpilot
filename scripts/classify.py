"""
Clasifica con Ollama los correos que aún no tienen categoría.

Requiere Ollama corriendo y el modelo descargado. Mide el tiempo por correo,
que es el dato que decide si un modelo es viable o no.
"""

import argparse
import time

from mailpilot.classifier import PROMPT_VERSION, OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData
from mailpilot.repository import emails_sin_clasificar, save_classification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=20, help="cuántos clasificar (por defecto 20)"
    )
    args = parser.parse_args()

    client = OllamaClient()
    # El prompt en pantalla no es decorativo: una clasificación guardada no
    # dice con qué prompt se hizo, y los prompts cambian. Sin este dato es
    # fácil acabar revisando en el dashboard la salida de una versión vieja.
    print(f"Modelo: {client.model}  ({client.base_url})  prompt: {PROMPT_VERSION}\n")

    with SessionLocal() as session:
        pendientes = emails_sin_clasificar(session, limit=args.limit)

        if not pendientes:
            print("No hay correos sin clasificar.")
            return

        print(f"{len(pendientes)} correos por clasificar.\n")
        tiempos = []
        fallos = 0

        for email in pendientes:
            # El clasificador trabaja con EmailData, no con el modelo de la
            # base de datos: así no depende de SQLAlchemy y es fácil de probar.
            datos = EmailData(
                gmail_message_id=email.gmail_message_id,
                gmail_thread_id=email.gmail_thread_id,
                subject=email.subject,
                sender=email.sender,
                snippet=email.snippet,
                received_at=email.received_at,
                raw_labels=email.raw_labels,
            )

            inicio = time.perf_counter()
            try:
                resultado = classify_email(client, datos)
            except Exception as error:
                fallos += 1
                print(f"  FALLO  {email.subject[:45]}")
                print(f"         {type(error).__name__}: {error}")
                continue
            segundos = time.perf_counter() - inicio
            tiempos.append(segundos)

            save_classification(
                session,
                email_id=email.id,
                category=resultado.category,
                confidence=resultado.confidence,
                reasoning=resultado.reasoning,
                model_used=client.model,
            )

            print(
                f"  {resultado.category.value:12} "
                f"{resultado.confidence:.2f}  {segundos:5.1f}s  "
                f"{email.subject[:42]}"
            )

        if tiempos:
            print(f"\n  clasificados: {len(tiempos)}")
            print(f"  fallos:       {fallos}")
            print(f"  media:        {sum(tiempos) / len(tiempos):.1f}s por correo")
            print(f"  total:        {sum(tiempos):.0f}s")


if __name__ == "__main__":
    main()
