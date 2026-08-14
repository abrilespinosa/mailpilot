"""
Guardado de correos en la base de datos.

Aísla el SQL del resto del sistema: gmail.py sabe hablar con Gmail, este
módulo sabe hablar con PostgreSQL, y ninguno sabe del otro.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mailpilot.gmail import EmailData
from mailpilot.models import (
    ActionProposal,
    AuditLog,
    Category,
    Classification,
    Email,
    ProposalStatus,
    ProposedAction,
)


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


# ---------------------------------------------------------------------------
# Propuestas
#
# Nada de aquí toca Gmail. Una propuesta es solo una fila en la base de datos
# esperando a que la usuaria decida. La ejecución real llega en la Fase 9.
# ---------------------------------------------------------------------------


def ultima_clasificacion_por_email():
    """
    La clasificación más reciente de cada correo.

    Classification es uno-a-muchos a propósito (guarda histórico), así que
    "la categoría actual" es la última fila por fecha. DISTINCT ON es la
    forma de PostgreSQL de decir "una fila por grupo, la primera según el
    ORDER BY". Es mucho más rápido que una subconsulta con MAX.

    El desempate por `id` NO es decorativo. `created_at` usa func.now(), que en
    PostgreSQL devuelve la hora de INICIO DE LA TRANSACCIÓN, no la del INSERT:
    dos filas escritas en la misma transacción tienen el mismo timestamp al
    microsegundo. Sin desempate, con un empate el orden queda indefinido y
    "la última clasificación" podía salir la primera. El id es creciente y
    resuelve el empate siempre.
    """
    return (
        select(Classification)
        .distinct(Classification.email_id)
        .order_by(
            Classification.email_id,
            Classification.created_at.desc(),
            Classification.id.desc(),
        )
    )


def generar_propuestas(session: Session, limit: int = 100) -> int:
    """
    Crea propuestas pendientes a partir de las clasificaciones. Devuelve
    cuántas creó.

    Dos reglas de idempotencia, y las dos vienen de CLAUDE.md:

    1. Si un correo YA tiene una propuesta decidida (aprobada, rechazada,
       modificada...), no se genera otra. La usuaria ya opinó; volver a
       preguntarle lo mismo sería un error.
    2. Si ya tiene una pendiente, tampoco. Esto además lo garantiza el índice
       único parcial de PostgreSQL, no solo este código.

    Por ahora solo propone CATEGORIZE. Proponer papelera llegará cuando exista
    la ejecución real y se pueda probar de punta a punta.
    """
    con_propuesta = select(ActionProposal.email_id).distinct()

    clasificaciones = (
        session.execute(
            ultima_clasificacion_por_email().where(
                Classification.email_id.not_in(con_propuesta)
            )
        )
        .scalars()
        .all()
    )[:limit]

    for clasificacion in clasificaciones:
        session.add(
            ActionProposal(
                email_id=clasificacion.email_id,
                proposed_action=ProposedAction.CATEGORIZE,
                category=clasificacion.category,
                reason=clasificacion.reasoning,
                confidence=clasificacion.confidence,
                status=ProposalStatus.PENDING,
            )
        )

    if clasificaciones:
        session.add(
            AuditLog(
                event_type="proposals_generated",
                detail={"count": len(clasificaciones)},
            )
        )
    session.commit()

    return len(clasificaciones)


def propuestas_pendientes(session: Session, limit: int = 50, offset: int = 0):
    """Propuestas sin decidir, de la más reciente a la más antigua."""
    return (
        session.execute(
            select(ActionProposal)
            .join(Email)
            .where(ActionProposal.status == ProposalStatus.PENDING)
            .order_by(Email.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )


class PropuestaYaDecidida(Exception):
    """Se intentó decidir una propuesta que ya no está pendiente."""


def decidir_propuesta(
    session: Session,
    proposal_id: int,
    decision: ProposalStatus,
    categoria_elegida: Category | None = None,
) -> ActionProposal:
    """
    Registra la decisión de la usuaria sobre una propuesta.

    - APPROVED: se acepta la categoría propuesta. final_category = category.
    - MODIFIED: la usuaria elige otra. final_category = su elección, y
      `category` se conserva intacta para poder saber en qué falló el modelo.
    - REJECTED: no se aplica nada. final_category queda a null.

    `category` NUNCA se sobrescribe. Si al corregir machacáramos la propuesta
    original, perderíamos exactamente la información que hace útil todo esto.

    Solo se puede decidir una vez: una propuesta ya decidida lanza excepción en
    vez de cambiar en silencio. Sin eso, dos pestañas abiertas podrían
    sobrescribirse la una a la otra sin que nadie se entere.
    """
    propuesta = session.get(ActionProposal, proposal_id)
    if propuesta is None:
        raise LookupError(f"No existe la propuesta {proposal_id}")

    if propuesta.status is not ProposalStatus.PENDING:
        raise PropuestaYaDecidida(
            f"La propuesta {proposal_id} ya está en estado "
            f"'{propuesta.status.value}'"
        )

    if decision is ProposalStatus.MODIFIED:
        if categoria_elegida is None:
            raise ValueError("Modificar requiere indicar la categoría elegida")
        propuesta.final_category = categoria_elegida
    elif decision is ProposalStatus.APPROVED:
        propuesta.final_category = propuesta.category

    propuesta.status = decision
    propuesta.decided_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            action_proposal_id=propuesta.id,
            email_id=propuesta.email_id,
            event_type=f"proposal_{decision.value}",
            detail={
                "propuesta": propuesta.category.value if propuesta.category else None,
                "elegida": (
                    propuesta.final_category.value
                    if propuesta.final_category
                    else None
                ),
                "acierto": (
                    propuesta.category == propuesta.final_category
                    if propuesta.final_category
                    else None
                ),
            },
        )
    )
    session.commit()

    return propuesta


def propuestas_decididas(session: Session, limit: int = 20, offset: int = 0):
    """Propuestas ya decididas, de la decisión más reciente a la más antigua."""
    return list(
        session.execute(
            select(ActionProposal)
            .where(ActionProposal.status != ProposalStatus.PENDING)
            .order_by(
                ActionProposal.decided_at.desc(),
                ActionProposal.id.desc(),   # desempate, igual que en las clasificaciones
            )
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )


class PropuestaNoDecidida(Exception):
    """Se intentó rectificar una propuesta que todavía está pendiente."""


def rectificar_decision(
    session: Session,
    proposal_id: int,
    categoria_elegida: Category | None,
) -> ActionProposal:
    """
    Cambia una decisión YA TOMADA. Para cuando el clic fue el equivocado.

    Es un camino distinto de `decidir_propuesta` a propósito, no una versión
    relajada. Aquella se niega a decidir dos veces (409) para que dos pestañas
    abiertas no se pisen en silencio; esto es lo contrario: alguien vuelve a
    propósito sobre algo que ya miró. Mezclar las dos cosas convertiría el 409
    en papel mojado.

    `category` sigue sin tocarse NUNCA: es lo que dijo el modelo y es el dato
    que permite medirlo. Lo que cambia es `final_category`, y el estado se
    recalcula solo: coincide con el modelo -> aprobada; no coincide ->
    modificada; sin categoría -> rechazada.

    Cada rectificación deja su propia entrada en el audit log con el valor
    anterior. Eso importa más de lo que parece: los números de evaluación salen
    de estas filas, así que sin el registro, rectificar hoy haría irreproducible
    una medición de ayer.
    """
    propuesta = session.get(ActionProposal, proposal_id)
    if propuesta is None:
        raise LookupError(f"No existe la propuesta {proposal_id}")

    if propuesta.status is ProposalStatus.PENDING:
        raise PropuestaNoDecidida(
            f"La propuesta {proposal_id} está pendiente: decídela primero"
        )

    anterior = propuesta.final_category

    if categoria_elegida is None:
        propuesta.final_category = None
        propuesta.status = ProposalStatus.REJECTED
    else:
        propuesta.final_category = categoria_elegida
        propuesta.status = (
            ProposalStatus.APPROVED
            if categoria_elegida == propuesta.category
            else ProposalStatus.MODIFIED
        )

    propuesta.decided_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            action_proposal_id=propuesta.id,
            email_id=propuesta.email_id,
            event_type="proposal_rectified",
            detail={
                "propuesta": propuesta.category.value if propuesta.category else None,
                "antes": anterior.value if anterior else None,
                "ahora": (
                    propuesta.final_category.value
                    if propuesta.final_category
                    else None
                ),
            },
        )
    )
    session.commit()

    return propuesta


def estadisticas(session: Session) -> dict:
    """
    Resumen de lo decidido hasta ahora, para la cabecera del dashboard.

    `acierto` es el porcentaje de veces que la usuaria aceptó lo que propuso el
    modelo, contando solo aprobadas y corregidas: las rechazadas no dicen si la
    categoría era buena o mala, así que no entran en el cálculo.

    Ojo con este número: NO es comparable con el 73,8 % de la Fase 6. Aquel se
    midió sobre correos elegidos al azar; este sale de los correos que a la
    usuaria le apeteció revisar, que no son una muestra representativa. Sirve
    para ver la tendencia, no para presumir.
    """
    filas = session.execute(
        select(ActionProposal.status, func.count())
        .group_by(ActionProposal.status)
    ).all()

    conteo = {estado: total for estado, total in filas}

    aprobadas = conteo.get(ProposalStatus.APPROVED, 0)
    corregidas = conteo.get(ProposalStatus.MODIFIED, 0)
    decididas_con_categoria = aprobadas + corregidas

    return {
        "pendientes": conteo.get(ProposalStatus.PENDING, 0),
        "aprobadas": aprobadas,
        "corregidas": corregidas,
        "rechazadas": conteo.get(ProposalStatus.REJECTED, 0),
        "acierto": (
            aprobadas / decididas_con_categoria if decididas_con_categoria else None
        ),
    }


def correcciones(session: Session) -> list[ActionProposal]:
    """
    Propuestas donde la usuaria eligió algo distinto a lo que dijo el modelo.

    Es la consulta que da valor a todo esto: son etiquetas correctas
    conseguidas con uso real, sin etiquetar nada a mano. Alimentan el conjunto
    de evaluación y señalan qué categorías conviene afinar.
    """
    return list(
        session.execute(
            select(ActionProposal)
            .where(ActionProposal.status == ProposalStatus.MODIFIED)
            .order_by(ActionProposal.decided_at.desc())
        )
        .scalars()
        .all()
    )
