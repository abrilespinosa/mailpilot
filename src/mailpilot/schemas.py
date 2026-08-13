"""
Esquemas de la API (Pydantic).

Deliberadamente separados de models.py. Los modelos de SQLAlchemy describen
cómo se GUARDAN los datos; estos describen cómo se EXPONEN.

La ventaja aparece el día que añadas una columna interna a una tabla: no se
publica sola por la API. Exponer un campo es una decisión explícita que se
toma aquí, escribiéndolo.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmailSummary(BaseModel):
    """Lo justo para pintar una fila en un listado."""

    # from_attributes permite construir el esquema desde un objeto de
    # SQLAlchemy leyendo sus atributos, en vez de desde un diccionario.
    model_config = ConfigDict(from_attributes=True)

    id: int
    gmail_message_id: str
    subject: str
    sender: str
    received_at: datetime


class EmailDetail(EmailSummary):
    """Un correo completo. Hereda de EmailSummary y añade el resto."""

    gmail_thread_id: str
    snippet: str
    raw_labels: list[str]
    created_at: datetime


class EmailPage(BaseModel):
    """
    Una página de resultados.

    Devolver una lista pelada obliga al cliente a adivinar si hay más
    resultados. Con total/limit/offset, el dashboard puede pintar la
    paginación sin llamadas extra.
    """

    total: int
    limit: int
    offset: int
    items: list[EmailSummary]
