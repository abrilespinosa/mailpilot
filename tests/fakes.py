"""
Dobles de prueba para la Gmail API.

Los tests NO llaman a Gmail de verdad. Motivos: sería lento, gastaría cuota,
necesitaría credenciales válidas en CI, y sobre todo el resultado cambiaría
cada vez que llega un correo nuevo — un test que a veces pasa y a veces no
es peor que no tener test.

Estas clases imitan la forma encadenada de la librería de Google:
    service.users().messages().list(...).execute()
"""


class _FakeRequest:
    """Lo que devuelven list() y get() antes de llamar a .execute()."""

    def __init__(self, payload: dict):
        self._payload = payload

    def execute(self) -> dict:
        return self._payload


class FakeMessagesResource:
    """
    Imita service.users().messages().

    `pages` son las respuestas que irá devolviendo list(), en orden.
    `messages` es un diccionario id -> respuesta de get().

    Guarda las llamadas recibidas en `list_calls` para poder comprobar CÓMO se
    llamó a la API, no solo qué devolvió.
    """

    def __init__(self, pages: list[dict] | None = None, messages: dict | None = None):
        self.pages = pages or []
        self.messages = messages or {}
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def list(self, **kwargs) -> _FakeRequest:
        self.list_calls.append(kwargs)
        return _FakeRequest(self.pages[len(self.list_calls) - 1])

    def get(self, **kwargs) -> _FakeRequest:
        self.get_calls.append(kwargs)
        return _FakeRequest(self.messages[kwargs["id"]])


class FakeService:
    """Imita el objeto que devuelve build("gmail", "v1", ...)."""

    def __init__(self, messages_resource: FakeMessagesResource):
        self._messages = messages_resource

    def users(self):
        return self

    def messages(self):
        return self._messages


def make_raw_message(
    message_id: str = "abc123",
    thread_id: str = "thread1",
    subject: str | None = "Un asunto",
    sender: str | None = "Alguien <alguien@ejemplo.com>",
    snippet: str = "Texto de ejemplo",
    internal_date: str = "1755000000000",
    labels: list[str] | None = None,
) -> dict:
    """
    Construye una respuesta de messages.get con la misma forma que la real.

    Si subject o sender son None, la cabecera no se incluye: así se prueba el
    caso de correos que llegan sin asunto.
    """
    headers = []
    if subject is not None:
        headers.append({"name": "Subject", "value": subject})
    if sender is not None:
        headers.append({"name": "From", "value": sender})

    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "internalDate": internal_date,
        "labelIds": labels if labels is not None else ["INBOX"],
        "payload": {"headers": headers},
    }


# ---------------------------------------------------------------------------
# Dobles para la ESCRITURA en Gmail (Fase 9)
#
# Estos importan más que los de lectura: aquí un fallo del doble ocultaría que
# el código hace algo destructivo. Por eso guardan todo lo que reciben, y por
# eso NO tienen método send() ni delete(): si algún día el código intentara
# llamarlos, el test reventaría con AttributeError en vez de pasar en silencio.
# ---------------------------------------------------------------------------


class FakeWritableMessages(FakeMessagesResource):
    """messages() con modify() y trash(), que son las dos únicas escrituras."""

    def __init__(self, fallar_con: Exception | None = None, **kwargs):
        super().__init__(**kwargs)
        self.modify_calls: list[dict] = []
        self.trash_calls: list[dict] = []
        self.fallar_con = fallar_con

    def modify(self, **kwargs) -> _FakeRequest:
        if self.fallar_con:
            raise self.fallar_con
        self.modify_calls.append(kwargs)
        return _FakeRequest({"id": kwargs["id"]})

    def trash(self, **kwargs) -> _FakeRequest:
        if self.fallar_con:
            raise self.fallar_con
        self.trash_calls.append(kwargs)
        return _FakeRequest({"id": kwargs["id"], "labelIds": ["TRASH"]})


class FakeLabels:
    """
    Imita service.users().labels().

    `existentes` es {nombre: id}. create() añade a ese diccionario, así que el
    doble se comporta como Gmail: crear dos veces la misma etiqueta se nota.
    """

    def __init__(self, existentes: dict[str, str] | None = None):
        self.existentes = dict(existentes or {})
        self.create_calls: list[dict] = []

    def list(self, **kwargs) -> _FakeRequest:
        return _FakeRequest(
            {"labels": [{"name": n, "id": i} for n, i in self.existentes.items()]}
        )

    def create(self, **kwargs) -> _FakeRequest:
        self.create_calls.append(kwargs)
        nombre = kwargs["body"]["name"]
        nuevo = f"Label_{len(self.existentes) + 1}"
        self.existentes[nombre] = nuevo
        return _FakeRequest({"id": nuevo, "name": nombre})


class FakeWritableService:
    """Como FakeService, pero con labels() y escrituras."""

    def __init__(self, messages: FakeWritableMessages, labels: FakeLabels | None = None):
        self._messages = messages
        self._labels = labels or FakeLabels()

    def users(self):
        return self

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels
