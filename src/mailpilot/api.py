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

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from googleapiclient.errors import HttpError

from mailpilot import gmail_actions, jobs, web
from mailpilot.auth import NecesitaReautenticacion
from mailpilot.db import get_session
from mailpilot.gmail import fetch_body, get_service, ids_en_papelera
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
    cancelar_papelera,
    encolar_accion,
    pedir_recuperacion,
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
# Métodos que pueden cambiar algo. GET queda fuera a propósito: leer no
# modifica nada, y el dashboard se sirve por GET.
METODOS_DE_ESCRITURA = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@app.middleware("http")
async def solo_desde_esta_pagina(request, call_next):
    """
    Rechaza las escrituras que vienen de otra página web. Esto es CSRF.

    EL ATAQUE QUE IMPIDE
    --------------------
    La API no tiene autenticación —es de un solo usuario y escucha en
    localhost—, pero "localhost" no protege de nada frente al navegador: si
    tienes el dashboard abierto y visitas una web maliciosa, esa web puede
    lanzar peticiones a http://localhost:8000 desde TU navegador.

        for (let id = 1; id < 3000; id++)
          fetch(`http://localhost:8000/emails/${id}/trash`, {method:'POST', mode:'no-cors'});
        fetch('http://localhost:8000/actions/execute', {method:'POST', mode:'no-cors'});

    La política de mismo origen impide LEER la respuesta, no ENVIAR la
    petición. Y aquí el daño está en el envío: eso mandaría la bandeja entera
    a la papelera sin un clic de la usuaria. Contradice de frente el principio
    del proyecto, que dice que solo se ejecuta lo autorizado.

    POR QUÉ MIRAR `Origin` BASTA
    ----------------------------
    El navegador pone `Origin` en toda petición de escritura y **la página no
    puede falsearlo**: lo escribe el navegador, no el JavaScript. Así que si
    viene y no es esta misma página, no lo ha pedido la usuaria.

    Cuando NO viene es porque no hay navegador detrás —curl, los scripts de
    `scripts/`, los tests—, y ahí no hay sesión que robar: quien ejecuta un
    comando en la terminal ya tiene la máquina. Por eso ausente se permite y
    presente-y-distinto se rechaza.

    No hace falta un token CSRF: sin cookies ni sesión, comparar el origen
    cubre el mismo hueco con una décima parte del código.
    """
    if request.method in METODOS_DE_ESCRITURA:
        origen = request.headers.get("origin")
        if origen is not None:
            propio = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origen != propio:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "Petición rechazada: viene de otra página. "
                            "MailPilot solo acepta escrituras desde su propio dashboard."
                        )
                    },
                )

    return await call_next(request)


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
    a_ciegas: bool | None = None,
) -> ActionProposal:
    """
    Traduce los errores del repositorio a códigos HTTP.

    404 si no existe, 409 si ya se decidió. El 409 (Conflict) es el correcto:
    la petición es válida, pero choca con el estado actual del recurso. Con dos
    pestañas abiertas, la segunda recibe 409 en vez de pisar la decisión de la
    primera en silencio.
    """
    try:
        return decidir_propuesta(session, proposal_id, decision, categoria, a_ciegas)
    except LookupError:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada")
    except PropuestaYaDecidida as error:
        raise HTTPException(status_code=409, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.post("/proposals/{proposal_id}/approve", response_model=ProposalOut, tags=["propuestas"])
def approve_proposal(
    proposal_id: int,
    decision: DecisionIn | None = None,
    session: Session = Depends(get_session),
) -> ActionProposal:
    """
    Acepta la categoría propuesta. final_category = category.

    El cuerpo es OPCIONAL: aprobar no necesita categoría. Solo se lee de él
    `decidido_a_ciegas`, para que aprobar registre el modo igual que corregir.
    Sin esto, la mitad de las decisiones se quedarían sin procedencia y el
    conjunto volvería a ser una mezcla imposible de separar.
    """
    return _decidir(
        session,
        proposal_id,
        ProposalStatus.APPROVED,
        None,
        decision.decidido_a_ciegas if decision else None,
    )


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
    return _decidir(
        session,
        proposal_id,
        ProposalStatus.MODIFIED,
        decision.category,
        decision.decidido_a_ciegas,
    )


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


@app.post("/emails/{email_id}/trash/cancel", response_model=ActionOut, tags=["acciones"])
def cancel_trash(email_id: int, session: Session = Depends(get_session)) -> dict:
    """
    Retira una petición de papelera que aún no se ha aplicado en Gmail.

    Es el deshacer del botón de papelera, y es barato precisamente porque nada
    ha pasado todavía: se borra la fila pendiente y ya está. Recuperar un
    correo que YA está en la papelera es otra cosa y va por `/restore`.

    Devuelve `pedida: false` para decir "ya no está pedida". Cancelar algo que
    no estaba pedido no es un error: es un segundo clic, y contestar 200 deja
    la pantalla y la base diciendo lo mismo.
    """
    if session.get(Email, email_id) is None:
        raise HTTPException(status_code=404, detail="Correo no encontrado")

    cancelar_papelera(session, email_id)
    return {"pedida": False, "accion": "move_to_trash"}


@app.post("/emails/{email_id}/restore", response_model=ActionOut, tags=["acciones"])
def request_restore(email_id: int, session: Session = Depends(get_session)) -> dict:
    """
    Pide sacar un correo de la papelera y devolverle su categoría.

    Como todo lo demás, solo deja la petición: Gmail no cambia hasta pulsar
    "Aplicar en Gmail". Es la única acción del proyecto que no quita nada.
    """
    if session.get(Email, email_id) is None:
        raise HTTPException(status_code=404, detail="Correo no encontrado")

    return {
        "pedida": pedir_recuperacion(session, email_id),
        "accion": "restore_from_trash",
    }


def _servicio_gmail():
    """
    El ÚNICO sitio donde la API construye un cliente de Gmail.

    `interactivo=False` es lo que impide que una petición HTTP acabe dentro de
    `flow.run_local_server()`, que se queda esperando un callback del navegador
    que aquí nunca llega: la petición se colgaría para siempre y el botón del
    dashboard giraría sin decir nada.

    En su lugar sale un 503 con la instrucción exacta. Con el OAuth en modo
    Testing el token caduca cada 7 días, así que este camino se recorre todas
    las semanas y tiene que explicarse solo.
    """
    try:
        return get_service(interactivo=False)
    except NecesitaReautenticacion as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/emails/{email_id}/body", tags=["correos"])
def leer_cuerpo(email_id: int, session: Session = Depends(get_session)) -> dict:
    """
    El cuerpo de un correo, traído de Gmail EN EL MOMENTO.

    Existe porque la ingestión guarda `format="metadata"`: en la base de datos
    solo hay asunto, remitente y un `snippet` de ~200 caracteres —y 146 correos
    lo tienen vacío—, que muchas veces no basta para decidir la categoría.

    Es una LECTURA y no persiste nada: el cuerpo va del navegador a la pantalla
    y se acaba ahí. Guardarlo habría exigido migración, reingerir 2.498 correos
    y dejar el contenido en disco para siempre.

    QUIEN LO PINTE DEBE USAR `textContent`, NUNCA `innerHTML`. Esto es el texto
    más hostil de todo el sistema: lo escribe cualquiera que sepa tu dirección.
    Jinja2 no puede protegerte aquí porque no pasa por Jinja2.
    """
    correo = session.get(Email, email_id)
    if correo is None:
        raise HTTPException(status_code=404, detail="Ese correo no existe.")

    servicio = _servicio_gmail()
    try:
        texto = fetch_body(servicio, correo.gmail_message_id)
    except HttpError as error:
        # Un correo borrado a mano en Gmail da 404 ahí y aquí sería un 500
        # sin explicación. Se traduce a algo que la pantalla pueda enseñar.
        raise HTTPException(
            status_code=502,
            detail=f"Gmail no devolvió el correo: {error}",
        ) from error

    return {"texto": texto}


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
    return sincronizar_papelera(session, ids_en_papelera(_servicio_gmail()))


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

    service = _servicio_gmail()
    cache = gmail_actions.etiquetas_existentes(service)

    ejecutadas = fallidas = 0
    frenado = False

    for accion in pendientes:
        try:
            resultado = gmail_actions.ejecutar(session, service, accion, cache)
        except gmail_actions.ErrorPasajero:
            # Gmail ha dicho "ahora no" (429, o algo caído de su lado). Se
            # CORTA la tanda: seguir pidiendo es lo que convierte un límite de
            # ritmo en cincuenta. La acción sigue pendiente, así que no se ha
            # perdido nada y la siguiente llamada la recoge.
            frenado = True
            break

        if resultado.status is GmailActionStatus.EXECUTED:
            ejecutadas += 1
        else:
            fallidas += 1

    return {
        "ejecutadas": ejecutadas,
        "fallidas": fallidas,
        "pendientes": len(acciones_pendientes(session, limit=10_000)),
        "frenado": frenado,
    }


# ---------------------------------------------------------------------------
# Cargar correos nuevos (el botón del dashboard)
#
# Traer de Gmail es rápido; clasificar son ~10 s por correo. Un lote de 100 se
# va a veinte minutos, así que la petición solo ARRANCA el trabajo y contesta.
# El progreso se consulta aparte. Ver `mailpilot/jobs.py`.
# ---------------------------------------------------------------------------


@app.post("/jobs/load", tags=["cargar"])
def cargar_correos(
    tareas: BackgroundTasks,
    limit: int = Query(jobs.MAXIMO_POR_TANDA, ge=1, le=jobs.MAXIMO_POR_TANDA),
) -> dict:
    """
    Trae correos nuevos de Gmail y los deja clasificados y listos para revisar.

    Contesta al instante: lo que devuelve es "he empezado", no "ya está".

    Las credenciales se piden AQUÍ, antes de soltar el trabajo al fondo. Si el
    token ha caducado —cada 7 días, con el OAuth en modo Testing—, sale un 503
    con la instrucción exacta en vez de una barra de progreso que se queda
    congelada sin decir por qué.

    Un solo trabajo a la vez. Dos tandas en paralelo se pisarían la cuenta del
    progreso y clasificarían los mismos correos dos veces, así que la segunda
    pulsación devuelve 409 en lugar de hacer daño en silencio.
    """
    if jobs.hay_uno_corriendo():
        raise HTTPException(
            status_code=409, detail="Ya hay una carga en marcha. Espera a que termine."
        )

    service = _servicio_gmail()
    tareas.add_task(jobs.cargar_y_clasificar, service, limit)
    return {"arrancado": True, "limite": limit}


@app.get("/jobs/status", tags=["cargar"])
def estado_carga() -> dict:
    """
    Cómo va la carga: porcentaje y segundos que quedan.

    El dashboard lo consulta cada pocos segundos. Es de solo lectura y no toca
    la base de datos, así que preguntarlo mucho no cuesta nada.
    """
    return jobs.estado_actual()
