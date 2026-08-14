"""
Modelo de datos de MailPilot.

Cinco tablas: Email (lo que llega de Gmail), Classification (lo que dice la
IA), ActionProposal (qué categoría se propone y cuál decidió la usuaria),
GmailAction (qué se hace sobre Gmail) y AuditLog (qué pasó de verdad).

ActionProposal y GmailAction están separadas a propósito: clasificar y tirar
son dos ejes independientes. Ver docs/decisions/002-tirar-no-es-corregir.md.

Las categorías están definidas en docs/decisions/001-categorias-de-clasificacion.md
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
#
# Estos enums son la mitigación de prompt injection, no solo tipos de dato.
# El LLM únicamente puede devolver uno de estos valores; cualquier otra cosa
# se descarta antes de llegar aquí. Ver ADR 001.
# ---------------------------------------------------------------------------


class Category(str, enum.Enum):
    """
    Las siete categorías. Cambiar esta lista requiere migración de Alembic.

    `tramites` se llamaba `banco` hasta 2026-08-13. Se renombró porque ya
    contenía ayudas públicas y trámites con la administración, que de bancario
    no tienen nada. Ver ADR 001.
    """

    PERSONAL = "personal"
    TRABAJO = "trabajo"
    COMPRAS = "compras"
    TRAMITES = "tramites"
    AVISOS = "avisos"
    PROMOCIONES = "promociones"
    OTROS = "otros"


class ProposedAction(str, enum.Enum):
    """
    Acciones que MailPilot puede proponer.

    No existe borrado permanente y no debe añadirse: está fuera del alcance
    del proyecto, no solo del MVP. move_to_trash es reversible 30 días.
    """

    CATEGORIZE = "categorize"
    MOVE_TO_TRASH = "move_to_trash"


class GmailActionType(str, enum.Enum):
    """
    Lo ÚNICO que MailPilot puede hacerle a Gmail.

    Dos valores, y la lista es cerrada a propósito. No existe `send` ni
    `delete`, así que ninguna parte del sistema tiene forma de pedirlos: no es
    que estén prohibidos, es que no se pueden nombrar.

    - `apply_label`: crea (si hace falta) y aplica una etiqueta de MailPilot.
      Nunca toca ni borra las etiquetas que la usuaria ya tenía.
    - `move_to_trash`: papelera de Gmail, reversible 30 días.
    - `restore_from_trash`: saca de la papelera. La única que no quita nada:
      deshace, y por eso ampliar la lista para incluirla es defendible.

    Ampliar este enum tiene que ser SIEMPRE una decisión deliberada. Hay un
    test que fija sus valores exactos, así que añadir uno sin querer es
    imposible: hay que ir a cambiarlo a mano.

    El borrado permanente además es imposible por scope: exige
    `https://mail.google.com/`, que no pedimos. Enviar correo sí lo permitiría
    `gmail.modify`, y por eso hace falta este enum más el test que rastrea el
    código fuente. Ver ADR 003.
    """

    APPLY_LABEL = "apply_label"
    MOVE_TO_TRASH = "move_to_trash"
    RESTORE_FROM_TRASH = "restore_from_trash"


class GmailActionStatus(str, enum.Enum):
    """
    Ciclo de vida de una acción sobre Gmail.

    No hay estado de "aprobada": una fila solo existe si la usuaria la pidió.
    Crear la fila ES la autorización, así que no puede haber una acción
    esperando permiso ni un camino que se salte la aprobación.
    """

    PENDING = "pending"
    EXECUTED = "executed"
    FAILED = "failed"


class ProposalStatus(str, enum.Enum):
    """Ciclo de vida de una propuesta, desde que nace hasta que se ejecuta."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXECUTED = "executed"
    FAILED = "failed"


def _pg_enum(enum_class: type[enum.Enum], name: str) -> SAEnum:
    """
    Crea un tipo ENUM nativo de PostgreSQL a partir de un Enum de Python.

    values_callable hace que en la base de datos se guarden los valores
    ("personal") y no los nombres de miembro ("PERSONAL"). Sin esto, lo que
    ves en la base de datos no coincide con lo que ves en el código.
    """
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


# Se crean UNA vez y se reutilizan. Category aparece en dos tablas, y si cada
# columna construyera su propio tipo, PostgreSQL recibiría dos CREATE TYPE con
# el mismo nombre y la migración fallaría.
CATEGORY_ENUM = _pg_enum(Category, "category")
PROPOSED_ACTION_ENUM = _pg_enum(ProposedAction, "proposed_action")
PROPOSAL_STATUS_ENUM = _pg_enum(ProposalStatus, "proposal_status")
GMAIL_ACTION_TYPE_ENUM = _pg_enum(GmailActionType, "gmail_action_type")
GMAIL_ACTION_STATUS_ENUM = _pg_enum(GmailActionStatus, "gmail_action_status")


# ---------------------------------------------------------------------------
# Tablas
# ---------------------------------------------------------------------------


class Email(Base):
    """Un correo tal y como vino de Gmail. No se modifica nunca."""

    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Clave de idempotencia: es el identificador de Gmail, no el nuestro.
    # El UNIQUE es lo que permite el patrón upsert al reingerir.
    gmail_message_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    gmail_thread_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )

    subject: Mapped[str] = mapped_column(Text, nullable=False)
    sender: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # timezone=True: Gmail da internalDate en UTC y así se guarda. Guardar
    # fechas sin zona horaria es una fuente clásica de errores silenciosos.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Labels de Gmail como JSON. Copia informativa: no restringe nada ni
    # tiene relación con las categorías de MailPilot.
    raw_labels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ESTADO ACTUAL en Gmail, no histórico. Lo mantiene la sincronización, y
    # también se marca cuando MailPilot tira un correo.
    #
    # Es un campo aparte de `gmail_actions` a propósito: aquella tabla guarda
    # lo que HICIMOS y no se reescribe nunca. Si "está en la papelera" se
    # dedujera de una acción ejecutada, rescatar el correo desde Gmail dejaría
    # el dato mintiendo para siempre. Los correos tirados a mano en Gmail
    # tampoco tienen ninguna acción detrás.
    en_papelera: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), index=True
    )
    sincronizado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    classifications: Mapped[list["Classification"]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )
    proposals: Mapped[list["ActionProposal"]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )
    gmail_actions: Mapped[list["GmailAction"]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Email {self.gmail_message_id} {self.subject[:40]!r}>"


class Classification(Base):
    """
    Resultado de clasificar un correo con el LLM.

    Uno-a-muchos con Email a propósito: reclasificar guarda una fila nueva en
    vez de sobrescribir. Eso da histórico para comparar modelos y prompts en
    la Fase 6.
    """

    __tablename__ = "classifications"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_confidence_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True
    )

    category: Mapped[Category] = mapped_column(CATEGORY_ENUM, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Explicación del modelo. Es texto generado por el LLM: se muestra a la
    # usuaria pero NUNCA se interpreta como instrucción ni se ejecuta.
    reasoning: Mapped[str | None] = mapped_column(Text)

    # Qué modelo lo produjo, p. ej. "llama3.1:8b". Imprescindible para poder
    # comparar resultados entre modelos en la Fase 6.
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    email: Mapped["Email"] = relationship(back_populates="classifications")

    def __repr__(self) -> str:
        return f"<Classification {self.category.value} {self.confidence:.2f}>"


class ActionProposal(Base):
    """
    Una acción propuesta sobre un correo, y qué decidió la usuaria.

    Fusiona ActionProposal + UserDecision del diseño original en una sola
    tabla con un campo `status`, por simplicidad de MVP.

    Nada de esta tabla se ejecuta contra Gmail hasta que status es APPROVED.
    """

    __tablename__ = "action_proposals"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_proposal_confidence_range"
        ),
        # Índice único PARCIAL: un correo no puede tener dos propuestas
        # pendientes a la vez. Las ya decididas no cuentan para el índice, así
        # que sí puede acumular histórico. Evita propuestas duplicadas al
        # reingerir, y lo garantiza PostgreSQL, no el código de la aplicación.
        Index(
            "uq_one_pending_proposal_per_email",
            "email_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True
    )

    proposed_action: Mapped[ProposedAction] = mapped_column(
        PROPOSED_ACTION_ENUM, nullable=False
    )
    # Lo que propuso la IA. NO se modifica nunca, ni cuando la usuaria corrige:
    # es el registro de lo que el modelo dijo, y sin él no se puede saber en qué
    # se equivocó. Nullable porque solo aplica a la acción CATEGORIZE.
    category: Mapped[Category | None] = mapped_column(CATEGORY_ENUM)

    # Lo que decidió la usuaria. Null mientras la propuesta está pendiente.
    # Al aprobar se copia `category`; al modificar se guarda su elección.
    #
    # Que sean dos columnas y no una es lo que permite la consulta que importa:
    #   WHERE category != final_category  ->  aquí se equivocó el modelo
    # Esas filas son etiquetas correctas conseguidas gratis, con datos de uso
    # real, y hacen crecer el conjunto de evaluación sin etiquetar a mano.
    final_category: Mapped[Category | None] = mapped_column(CATEGORY_ENUM)

    reason: Mapped[str | None] = mapped_column(Text)

    # Copia de la confianza que había al proponer. Desnormalizado a propósito:
    # si el correo se reclasifica después, la propuesta debe conservar la
    # confianza con la que se generó.
    confidence: Mapped[float | None] = mapped_column(Float)

    status: Mapped[ProposalStatus] = mapped_column(
        PROPOSAL_STATUS_ENUM,
        nullable=False,
        default=ProposalStatus.PENDING,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    email: Mapped["Email"] = relationship(back_populates="proposals")

    def __repr__(self) -> str:
        return f"<ActionProposal {self.proposed_action.value} {self.status.value}>"


class GmailAction(Base):
    """
    Lo que MailPilot va a hacer, o ya ha hecho, sobre Gmail.

    POR QUÉ ES UNA TABLA APARTE Y NO UNAS COLUMNAS EN ActionProposal
    ----------------------------------------------------------------
    El ADR 002 dice que clasificar y tirar son dos ejes independientes: la
    usuaria tira muchos correos de `promociones` y `otros` que están
    perfectamente clasificados.

    La prueba de que hacía falta separarlos es concreta. Metiendo la papelera
    en `ActionProposal`, las funciones `estadisticas()` y `correcciones()`
    necesitarían un `WHERE proposed_action = 'categorize'` para no contar lo
    tirado. El ADR 002 dejó dicho que tener que tocarlas era exactamente la
    señal de que los ejes se estaban mezclando. Con esta tabla no se tocan.

    Y el número que hay detrás: si lo tirado saliera del cálculo del acierto,
    este subiría 3,3 puntos borrando el 42 % de la muestra, justo las dos
    categorías donde el modelo va peor.

    NO HAY ENVIAR NI BORRAR
    -----------------------
    `GmailActionType` tiene dos valores y ninguno es `send` ni `delete`. Es la
    misma defensa de enum cerrado que se usa contra la prompt injection: no se
    trata de vigilar que nadie lo pida, es que no existe la forma de pedirlo.
    Ver ADR 003.
    """

    __tablename__ = "gmail_actions"
    __table_args__ = (
        # Una acción pendiente por tipo y correo. Sin esto, dos clics seguidos
        # en "a la papelera" encolarían la misma acción dos veces.
        Index(
            "uq_una_accion_pendiente_por_tipo",
            "email_id",
            "action",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # De qué decisión sale esta acción. Nullable porque mover a papelera lo
    # pide la usuaria directamente, sin que la IA lo haya propuesto.
    action_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_proposals.id", ondelete="SET NULL"), index=True
    )

    action: Mapped[GmailActionType] = mapped_column(
        GMAIL_ACTION_TYPE_ENUM, nullable=False
    )
    status: Mapped[GmailActionStatus] = mapped_column(
        GMAIL_ACTION_STATUS_ENUM, nullable=False, default=GmailActionStatus.PENDING
    )

    # Qué etiqueta se aplicó, o el error de Gmail si falló.
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Cuántas veces se ha intentado. Solo sube con errores PASAJEROS (429 de
    # Gmail, 5xx, cortes de red): esos dejan la fila en `pending` para que se
    # reintente sola. El tope existe para que un correo que siempre falla no
    # bloquee la cola eternamente; al llegar a MAX_INTENTOS pasa a `failed`.
    intentos: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    email: Mapped["Email"] = relationship(back_populates="gmail_actions")
    # De qué decisión sale la etiqueta que hay que poner. Nullable porque
    # mover a papelera no necesita ninguna propuesta detrás.
    proposal: Mapped["ActionProposal | None"] = relationship()

    def __repr__(self) -> str:
        return f"<GmailAction {self.action.value} {self.status.value}>"


class AuditLog(Base):
    """
    Registro de lo que ha pasado de verdad.

    Ambas claves ajenas son nullable a propósito: hay eventos que no pertenecen
    a ninguna propuesta (una ingestión, un refresco de token OAuth) y eventos
    que sí son de un correo pero no de una propuesta concreta.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    action_proposal_id: Mapped[int | None] = mapped_column(
        ForeignKey("action_proposals.id", ondelete="SET NULL"), index=True
    )
    email_id: Mapped[int | None] = mapped_column(
        ForeignKey("emails.id", ondelete="SET NULL"), index=True
    )

    event_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.event_type}>"
