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

from mailpilot.models import Category, ProposalStatus, ProposedAction


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


class ProposalOut(BaseModel):
    """Una propuesta con el correo al que se refiere, para pintar una fila."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email_id: int
    proposed_action: ProposedAction
    category: Category | None
    final_category: Category | None
    reason: str | None
    confidence: float | None
    status: ProposalStatus
    created_at: datetime
    decided_at: datetime | None
    email: EmailSummary


class ProposalPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ProposalOut]


class DecisionIn(BaseModel):
    """
    Lo que manda la usuaria al decidir.

    `category` solo se usa al modificar. Es un enum, así que la API rechaza
    con 422 cualquier categoría inventada antes de tocar la base de datos:
    la misma defensa que se aplica a la salida del LLM, ahora en la entrada
    de la API.
    """

    category: Category | None = None


class ActionOut(BaseModel):
    """Respuesta al PEDIR una acción. Nada ha pasado en Gmail todavía."""

    pedida: bool
    accion: str


class ExecutionOut(BaseModel):
    """Resultado de ejecutar acciones contra Gmail. Aquí sí ha pasado algo."""

    ejecutadas: int
    fallidas: int
    pendientes: int
