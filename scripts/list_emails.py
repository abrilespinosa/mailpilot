"""
Prueba manual de la ingestión de correos.

Lista los últimos correos y descarga sus datos (asunto, remitente, fecha,
labels). Todavía no toca base de datos: solo imprime por pantalla para
comprobar que el parsing es correcto.
"""

from mailpilot.gmail import fetch_messages, get_service, list_message_ids

LIMIT = 10


def main():
    service = get_service()

    ids = list_message_ids(service, limit=LIMIT)
    print(f"Gmail ha devuelto {len(ids)} IDs. Descargando...\n")

    emails = fetch_messages(service, ids)

    for position, email in enumerate(emails, start=1):
        fecha = email.received_at.strftime("%Y-%m-%d %H:%M UTC")
        print(f"{position:2}. {fecha}")
        print(f"    de:      {email.sender}")
        print(f"    asunto:  {email.subject}")
        print(f"    labels:  {', '.join(email.raw_labels)}")
        print(f"    snippet: {email.snippet[:70]}...")
        print()


if __name__ == "__main__":
    main()
