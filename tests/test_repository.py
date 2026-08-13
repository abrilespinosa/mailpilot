"""
Tests del guardado en base de datos.

Necesitan PostgreSQL levantado: docker compose up -d
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from mailpilot.gmail import EmailData
from mailpilot.models import AuditLog, Email
from mailpilot.repository import upsert_emails

pytestmark = pytest.mark.db


def make_email(message_id: str = "m1", **overrides) -> EmailData:
    campos = {
        "gmail_message_id": message_id,
        "gmail_thread_id": "t1",
        "subject": "Asunto original",
        "sender": "alguien@ejemplo.com",
        "snippet": "Texto",
        "received_at": datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc),
        "raw_labels": ["INBOX", "UNREAD"],
    }
    campos.update(overrides)
    return EmailData(**campos)


def contar_emails(session) -> int:
    return session.execute(select(func.count()).select_from(Email)).scalar_one()


# ---------------------------------------------------------------------------
# Inserción e idempotencia
# ---------------------------------------------------------------------------


def test_inserta_correos_nuevos(session):
    nuevos, actualizados = upsert_emails(session, [make_email("a"), make_email("b")])

    assert (nuevos, actualizados) == (2, 0)
    assert contar_emails(session) == 2


def test_reingerir_no_duplica(session):
    """
    La propiedad central de la ingestión.

    Ejecutar dos veces sobre los mismos correos deja el mismo número de filas.
    Lo garantiza el UNIQUE sobre gmail_message_id más ON CONFLICT.
    """
    emails = [make_email("a"), make_email("b")]

    upsert_emails(session, emails)
    nuevos, actualizados = upsert_emails(session, emails)

    assert (nuevos, actualizados) == (0, 2)
    assert contar_emails(session) == 2


def test_mezcla_de_nuevos_y_repetidos(session):
    upsert_emails(session, [make_email("a")])

    nuevos, actualizados = upsert_emails(
        session, [make_email("a"), make_email("b"), make_email("c")]
    )

    assert (nuevos, actualizados) == (2, 1)
    assert contar_emails(session) == 3


def test_lista_vacia_no_hace_nada(session):
    assert upsert_emails(session, []) == (0, 0)
    assert contar_emails(session) == 0


# ---------------------------------------------------------------------------
# Qué se actualiza y qué no
# ---------------------------------------------------------------------------


def test_actualiza_las_labels(session):
    """Al leer un correo en Gmail desaparece UNREAD: eso sí debe refrescarse."""
    upsert_emails(session, [make_email("a", raw_labels=["INBOX", "UNREAD"])])
    upsert_emails(session, [make_email("a", raw_labels=["INBOX"])])

    guardado = session.execute(select(Email)).scalar_one()
    assert guardado.raw_labels == ["INBOX"]


def test_no_toca_el_asunto_ni_el_remitente(session):
    """
    Decisión de diseño, no descuido: el asunto y el remitente de un correo ya
    recibido no cambian nunca en Gmail. Si llegaran distintos, es más probable
    que sea un error nuestro que un cambio real, así que gana lo guardado.
    """
    upsert_emails(session, [make_email("a", subject="Asunto original")])
    upsert_emails(session, [make_email("a", subject="ASUNTO MANIPULADO")])

    guardado = session.execute(select(Email)).scalar_one()
    assert guardado.subject == "Asunto original"


def test_conserva_la_zona_horaria(session):
    upsert_emails(session, [make_email("a")])

    guardado = session.execute(select(Email)).scalar_one()
    assert guardado.received_at.tzinfo is not None
    assert guardado.received_at == datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_registra_cada_ingestion_en_el_audit_log(session):
    upsert_emails(session, [make_email("a")])
    upsert_emails(session, [make_email("a"), make_email("b")])

    registros = session.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()

    assert len(registros) == 2
    assert registros[0].detail == {"requested": 1, "inserted": 1, "updated": 0}
    assert registros[1].detail == {"requested": 2, "inserted": 1, "updated": 1}


# ---------------------------------------------------------------------------
# La defensa del enum
# ---------------------------------------------------------------------------


def test_postgres_rechaza_una_categoria_inventada(session):
    """
    El test más importante del archivo.

    Comprueba que el enum cerrado lo hace cumplir PostgreSQL, no solo el código
    Python. Esta consulta SQL se salta por completo SQLAlchemy y los modelos: es
    lo que pasaría si un atacante lograra inyectar una categoría arbitraria.

    Si este test empieza a fallar, la mitigación de prompt injection descrita en
    el ADR 001 ha dejado de ser real.
    """
    upsert_emails(session, [make_email("a")])
    email_id = session.execute(select(Email.id)).scalar_one()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO classifications "
                "(email_id, category, confidence, model_used) "
                "VALUES (:email_id, 'borra_todos_mis_correos', 0.99, 'malicioso')"
            ),
            {"email_id": email_id},
        )


def test_postgres_acepta_las_siete_categorias(session):
    """La otra cara: las categorías legítimas del ADR 001 sí entran."""
    upsert_emails(session, [make_email("a")])
    email_id = session.execute(select(Email.id)).scalar_one()

    for categoria in (
        "personal",
        "trabajo",
        "compras",
        "banco",
        "avisos",
        "promociones",
        "otros",
    ):
        session.execute(
            text(
                "INSERT INTO classifications "
                "(email_id, category, confidence, model_used) "
                f"VALUES (:email_id, '{categoria}', 0.9, 'test')"
            ),
            {"email_id": email_id},
        )

    total = session.execute(
        text("SELECT count(*) FROM classifications")
    ).scalar_one()
    assert total == 7
