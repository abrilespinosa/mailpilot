"""
Prueba manual del flujo de autenticación.

Ejecuta esto una vez para verificar que:
1. Se abre el navegador y puedes aprobar el acceso.
2. Se genera credentials/token.json.
3. Puedes listar tus últimos correos (solo lectura).
"""

from googleapiclient.discovery import build

from mailpilot.auth import get_credentials


def main():
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    results = service.users().messages().list(userId="me", maxResults=5).execute()
    messages = results.get("messages", [])

    if not messages:
        print("No se encontraron correos.")
        return

    print(f"Últimos {len(messages)} correos (solo IDs por ahora):")
    for msg in messages:
        print(f"  - {msg['id']}")


if __name__ == "__main__":
    main()
