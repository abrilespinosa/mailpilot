"""
API HTTP de MailPilot.

Casi todos los endpoints de escritura solo registran decisiones de la usuaria
en la base de datos. La ÚNICA excepción es POST /actions/execute, que sí toca
Gmail, y solo ejecuta acciones que alguien pidió antes de forma explícita.

Ninguna acción ocurre sin una decisión humana explícita. La API no tiene forma
de aprobar nada por su cuenta, ni la IA de llamarse a sí misma.

Arrancar en desarrollo:
    uvicorn mailpilot.api:app --reload --app-dir src
"""

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mailpilot import gmail_actions, web
from mailpilot.db import get_session
from mailpilot.gmail import get_service, ids_en_papelera
from mailpilot.models import (
    ActionProposal,
    Email,
    GmailActionStatus,
    GmailActionType,
    ProposalStatus,
)
from mailpilot.repository import (
    PropuestaNoDecidida,
    PropuestaYaDecidida,
    acciones_pendientes,
    decidir_propuesta,
    encolar_etiquetas_atrasadas,
    sincronizar_papelera,
    encolar_accion,
    propuestas_pendientes,
    rectificar_decision,
)
from mailpilot.schemas import (
    ActionOut,
    BackfillOut,
    SyncOut,
    DecisionIn,
    EmailDetail,
    EmailPage,
    ExecutionOut,
    ProposalOut,
    ProposalPage,
)

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
        .join(Email)
        .where(
            ActionProposal.status == ProposalStatus.PENDING,
            Email.en_papelera.is_(False),
        )
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


@app.post("/proposals/{proposal_id}/rectify", response_model=ProposalOut, tags=["propuestas"])
def rectify_proposal(
    proposal_id: int,
    decision: DecisionIn,
    session: Session = Depends(get_session),
) -> ActionProposal:
    """
    Cambia una decisión ya tomada. Para arreglar un clic equivocado.

    Endpoint aparte a propósito, no un `approve` que ignore el 409: aquel
    conflicto existe para que dos pestañas abiertas no se pisen sin avisar, y
    esto es una corrección deliberada. Si se relajara el otro, el 409 dejaría
    de proteger nada.

    `category` (lo que dijo el modelo) sigue intacto. Sin categoría en el
    cuerpo, la propuesta queda descartada.
    """
    try:
        return rectificar_decision(session, proposal_id, decision.category)
    except LookupError:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada")
    except PropuestaNoDecidida as error:
        raise HTTPException(status_code=409, detail=str(error))


# ---------------------------------------------------------------------------
# Acciones sobre Gmail (Fase 9)
#
# Estos son los primeros endpoints que pueden acabar cambiando algo en la
# cuenta. Van en dos pasos a propósito: pedir y ejecutar. Entre uno y otro se
# puede ver qué está a punto de pasar, y eso es lo que hace revisable una
# acción destructiva.
# ---------------------------------------------------------------------------


@app.post("/emails/{email_id}/trash", response_model=ActionOut, tags=["acciones"])
def request_trash(
    email_id: int, session: Session = Depends(get_session)
) -> dict:
    """
    Pide mover un correo a la papelera. NO lo mueve todavía.

    Es un eje distinto del de la categoría (ADR 002): la usuaria tira muchos
    correos de `promociones` y `otros` que están bien clasificados, y tirar no
    dice nada sobre si la etiqueta era correcta.

    La papelera de Gmail es reversible 30 días. El borrado permanente no
    existe en este proyecto y el scope que pedimos tampoco lo permitiría.
    """
    if session.get(Email, email_id) is None:
        raise HTTPException(status_code=404, detail="Correo no encontrado")

    accion = encolar_accion(session, email_id, GmailActionType.MOVE_TO_TRASH)

    # Ya estaba pedida: no es un error, es un segundo clic.
    return {"pedida": accion is not None, "accion": "move_to_trash"}


@app.post("/actions/sync-trash", response_model=SyncOut, tags=["acciones"])
def sync_trash(session: Session = Depends(get_session)) -> dict:
    """
    Pregunta a Gmail qué hay en la papelera y pone al día la base de datos.

    Hace falta porque un correo tirado a mano en Gmail desaparece de la
    ingestión: `messages.list` excluye la papelera por defecto, así que su fila
    se queda con datos viejos y nunca vuelve a pasar por el upsert.

    Es una LECTURA de Gmail: no mueve ni etiqueta nada. Va en las dos
    direcciones, así que rescatar un correo desde Gmail también se refleja.
    """
    return sincronizar_papelera(session, ids_en_papelera(get_service()))


@app.post("/actions/backfill", response_model=BackfillOut, tags=["acciones"])
def backfill_labels(session: Session = Depends(get_session)) -> dict:
    """
    Encola la etiqueta de todo lo que ya decidiste antes de que existiera la
    Fase 9. NO toca Gmail: solo deja las acciones pedidas.

    Sin esto habría que volver a pulsar correo por correo algo ya decidido.
    Sigue haciendo falta pulsar "Aplicar en Gmail" después: encolar y ejecutar
    siguen siendo dos pasos.
    """
    return {"encoladas": encolar_etiquetas_atrasadas(session)}


@app.post("/actions/execute", response_model=ExecutionOut, tags=["acciones"])
def execute_actions(
    limit: int = Query(25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    """
    Ejecuta contra Gmail las acciones ya pedidas. AQUÍ SÍ CAMBIA TU CUENTA.

    Solo toca filas en estado `pending`, y esas filas solo existen porque
    alguien las pidió: no hay forma de que este endpoint invente trabajo.

    El tope de 100 no es decorativo. Cada acción es una llamada de red a
    Gmail, así que sin límite una petición podría tardar minutos y morir a la
    mitad. Como cada acción se guarda al terminar, cortar por lo sano deja el
    estado consistente y basta con volver a llamar.
    """
    pendientes = acciones_pendientes(session, limit=limit)
    if not pendientes:
        return {"ejecutadas": 0, "fallidas": 0, "pendientes": 0}

    service = get_service()
    cache = gmail_actions.etiquetas_existentes(service)

    ejecutadas = fallidas = 0
    for accion in pendientes:
        resultado = gmail_actions.ejecutar(session, service, accion, cache)
        if resultado.status is GmailActionStatus.EXECUTED:
            ejecutadas += 1
        else:
            fallidas += 1

    return {
        "ejecutadas": ejecutadas,
        "fallidas": fallidas,
        "pendientes": len(acciones_pendientes(session, limit=10_000)),
    }
