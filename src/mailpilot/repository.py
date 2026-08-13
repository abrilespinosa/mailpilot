"""
Guardado de correos en la base de datos.

Aísla el SQL del resto del sistema: gmail.py sabe hablar con Gmail, este
módulo sabe hablar con PostgreSQL, y ninguno sabe del otro.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mailpilot.gmail import EmailData
from mailpilot.models import AuditLog, Email


def _count_emails(session: Session) -> int:
    return session.execute(select(func.count()).select_from(Email)).scalar_one()


def upsert_emails(session: Session, emails: list[EmailData]) -> tuple[int, int]:
    """
    Guarda correos de forma idempotente. Devuelve (nuevos, actualizados).

    Usa INSERT ... ON CONFLICT de PostgreSQL sobre gmail_message_id, que tiene
    constraint UNIQUE. Ejecutar la ingestión dos veces sobre los mismos correos
    no duplica nada: la segunda vez actualiza en vez de insertar.

    Solo se actualizan los campos que de verdad cambian en Gmail. El asunto y
    el remitente de un correo ya recibido no cambian nunca; las labels sí (al
    marcarlo como leído desaparece UNREAD, por ejemplo).
    """
    if not emails:
        return (0, 0)

    rows = [
        {
            "gmail_message_id": email.gmail_message_id,
            "gmail_thread_id": email.gmail_thread_id,
            "subject": email.subject,
            "sender": email.sender,
            "snippet": email.snippet,
            "received_at": email.received_at,
            "raw_labels": email.raw_labels,
        }
        for email in emails
    ]

    before = _count_emails(session)

    statement = insert(Email).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=[Email.gmail_message_id],
        set_={
            # `excluded` es la fila que se intentaba insertar. Es vocabulario
            # de PostgreSQL, no de SQLAlchemy.
            "raw_labels": statement.excluded.raw_labels,
            "snippet": statement.excluded.snippet,
        },
    )
    session.execute(statement)

    # Contar antes y después es suficiente aquí porque la ingestión corre en
    # un solo proceso. Si algún día hubiera ingestiones concurrentes, este
    # cálculo dejaría de ser fiable.
    after = _count_emails(session)
    inserted = after - before
    updated = len(rows) - inserted

    session.add(
        AuditLog(
            event_type="emails_ingested",
            detail={
                "requested": len(rows),
                "inserted": inserted,
                "updated": updated,
            },
        )
    )
    session.commit()

    return (inserted, updated)
