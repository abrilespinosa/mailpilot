"""
Guardado de correos en la base de datos.

Aísla el SQL del resto del sistema: gmail.py sabe hablar con Gmail, este
módulo sabe hablar con PostgreSQL, y ninguno sabe del otro.
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mailpilot.gmail import EmailData
from mailpilot.models import AuditLog, Classification, Email


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


def emails_sin_clasificar(session: Session, limit: int = 50) -> list[Email]:
    """
    Correos que todavía no tiene ninguna clasificación.

    Permite reejecutar la clasificación sin volver a pagar el coste de los
    correos ya procesados: un modelo de 8B tarda segundos por correo.
    """
    ya_clasificados = select(Classification.email_id).distinct()

    return list(
        session.execute(
            select(Email)
            .where(Email.id.not_in(ya_clasificados))
            .order_by(Email.received_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def save_classification(
    session: Session,
    email_id: int,
    category,
    confidence: float,
    reasoning: str,
    model_used: str,
) -> Classification:
    """
    Guarda una clasificación. Siempre INSERT, nunca UPDATE.

    Reclasificar un correo añade una fila nueva en vez de sobrescribir la
    anterior. Eso deja histórico para comparar modelos y prompts en la Fase 6.
    """
    classification = Classification(
        email_id=email_id,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        model_used=model_used,
    )
    session.add(classification)

    session.add(
        AuditLog(
            email_id=email_id,
            event_type="email_classified",
            detail={
                "category": category.value,
                "confidence": confidence,
                "model": model_used,
            },
        )
    )
    session.commit()

    return classification
