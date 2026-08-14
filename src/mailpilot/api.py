"""
API HTTP de MailPilot.

Los endpoints de escritura que existen SOLO registran decisiones de la usuaria
sobre propuestas. Ninguno toca Gmail: eso llega en la Fase 9.

Ninguna acción ocurre sin una decisión humana explícita. La API no tiene forma
de aprobar nada por su cuenta, ni la IA de llamarse a sí misma.

Arrancar en desarrollo:
    uvicorn mailpilot.api:app --reload --app-dir src
"""

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mailpilot import web
from mailpilot.db import get_session
from mailpilot.models import ActionProposal, Email, ProposalStatus
from mailpilot.repository import PropuestaYaDecidida, decidir_propuesta, propuestas_pendientes
from mailpilot.schemas import DecisionIn, EmailDetail, EmailPage, ProposalOut, ProposalPage

app = FastAPI(
    title="MailPilot",
    version="0.1.0",
    description="Capa de gestión inteligente sobre Gmail. Solo lectura por ahora.",
)

# El dashboard (Fase 8) solo añade rutas GET: sirve HTML y nada más. Sus
# botones llaman a los endpoints POST de este mismo archivo, que son el único
# camino de escritura del sistema.
app.include_router(web.router)

# Imágenes del dashboard. StaticFiles solo responde a GET y HEAD, y bloquea las
# rutas con `..` que intentarían salir de la carpeta, así que montar una
# carpeta no abre la puerta al resto del disco.
app.mount("/static", StaticFiles(directory=web.STATIC_DIR), name="static")


@app.get("/health", tags=["sistema"])
def health(session: Session = Depends(get_session)) -> dict:
    """
    Comprueba que la API vive Y que la base de datos responde.

    Un health check que solo devuelve {"ok": true} sin tocar nada miente: la
    API puede estar viva con la base de datos caída.
    """
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@app.get("/emails", response_model=EmailPage, tags=["correos"])
def list_emails(
    limit: int = Query(20, ge=1, le=100, description="Cuántos devolver"),
    offset: int = Query(0, ge=0, description="Cuántos saltar"),
    session: Session = Depends(get_session),
) -> EmailPage:
    """
    Lista correos, del más reciente al más antiguo.

    El tope de 100 en `limit` no es decorativo: sin él, cualquiera puede pedir
    un millón de filas de una vez y tumbar el proceso. FastAPI valida los
    rangos y devuelve 422 antes de que el código llegue a ejecutarse.
    """
    total = session.execute(select(func.count()).select_from(Email)).scalar_one()

    items = (
        session.execute(
            select(Email)
            .order_by(Email.received_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )

    return EmailPage(total=total, limit=limit, offset=offset, items=items)


@app.get("/emails/{email_id}", response_model=EmailDetail, tags=["correos"])
def get_email(
    email_id: int,
    session: Session = Depends(get_session),
) -> Email:
    """Devuelve un correo por su id interno, o 404 si no existe."""
    email = session.get(Email, email_id)

    if email is None:
        raise HTTPException(status_code=404, detail="Correo no encontrado")

    return email


# ---------------------------------------------------------------------------
# Propuestas
#
# Estos son los primeros endpoints de escritura del proyecto. Escriben en la
# base de datos, nunca en Gmail, y solo para registrar lo que decidió una
# persona.
# ---------------------------------------------------------------------------


@app.get("/proposals", response_model=ProposalPage, tags=["propuestas"])
def list_proposals(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> ProposalPage:
    """Propuestas pendientes de decidir, de la más reciente a la más antigua."""
    total = session.execute(
        select(func.count())
        .select_from(ActionProposal)
        .where(ActionProposal.status == ProposalStatus.PENDING)
    ).scalar_one()

    items = propuestas_pendientes(session, limit=limit, offset=offset)

    return ProposalPage(total=total, limit=limit, offset=offset, items=items)


def _decidir(
    session: Session,
    proposal_id: int,
    decision: ProposalStatus,
    categoria=None,
) -> ActionProposal:
    """
    Traduce los errores del repositorio a códigos HTTP.

    404 si no existe, 409 si ya se decidió. El 409 (Conflict) es el correcto:
    la petición es válida, pero choca con el estado actual del recurso. Con dos
    pestañas abiertas, la segunda recibe 409 en vez de pisar la decisión de la
    primera en silencio.
    """
    try:
        return decidir_propuesta(session, proposal_id, decision, categoria)
    except LookupError:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada")
    except PropuestaYaDecidida as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.post("/proposals/{proposal_id}/approve", response_model=ProposalOut, tags=["propuestas"])
def approve_proposal(
    proposal_id: int, session: Session = Depends(get_session)
) -> ActionProposal:
    """Acepta la categoría propuesta. final_category = category."""
    return _decidir(session, proposal_id, ProposalStatus.APPROVED)


@app.post("/proposals/{proposal_id}/modify", response_model=ProposalOut, tags=["propuestas"])
def modify_proposal(
    proposal_id: int,
    decision: DecisionIn,
    session: Session = Depends(get_session),
) -> ActionProposal:
    """
    Corrige la categoría.

    Lo que propuso el modelo se conserva en `category`; la elección de la
    usuaria va a `final_category`. Esa diferencia es el dato que permite saber
    en qué se equivoca el clasificador, con uso real y sin etiquetar a mano.
    """
    if decision.category is None:
        raise HTTPException(
            status_code=422, detail="Modificar requiere indicar una categoría"
        )
    return _decidir(session, proposal_id, ProposalStatus.MODIFIED, decision.category)


@app.post("/proposals/{proposal_id}/reject", response_model=ProposalOut, tags=["propuestas"])
def reject_proposal(
    proposal_id: int, session: Session = Depends(get_session)
) -> ActionProposal:
    """Descarta la propuesta. No se aplica ninguna categoría."""
    return _decidir(session, proposal_id, ProposalStatus.REJECTED)
