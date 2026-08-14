"""
Dashboard: la pantalla donde la usuaria revisa y decide.

DECISIÓN DE DISEÑO: este módulo NO escribe nada.

Solo sirve HTML. Las decisiones las manda el navegador a los endpoints JSON que
ya existían antes de que hubiera pantalla (POST /proposals/{id}/approve, etc.).
El dashboard es un cliente más de la API, no un camino privilegiado.

El motivo es que las reglas del proyecto -- una decisión por propuesta, nunca
sobrescribir lo que dijo el modelo, dejar registro de auditoría -- viven en un
único sitio, `repository.decidir_propuesta`. Si el dashboard escribiera por su
cuenta, habría dos caminos que mantener sincronizados y uno de ellos acabaría
olvidándose de una regla.

Que aquí no haya ningún POST no es casualidad ni pereza: es la propiedad que
comprueba `test_solo_escriben_los_endpoints_de_decision` en tests/test_api.py.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mailpilot.db import get_session
from mailpilot.models import ActionProposal, Category, Email, ProposalStatus
from mailpilot.repository import (
    contar_acciones,
    decisiones_sin_aplicar,
    emails_en_papelera,
    estadisticas,
    propuestas_clasificadas,
    propuestas_pendientes,
)

router = APIRouter(tags=["dashboard"])

# Jinja2Templates activa el autoescapado para .html. Es la razón por la que el
# asunto de un correo que contenga <script> se pinta como texto y no se
# ejecuta. Ver la nota larga en templates/dashboard.html.
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

STATIC_DIR = Path(__file__).parent / "static"

# Por orden de preferencia: un SVG se ve nítido en cualquier pantalla.
EXTENSIONES = ("svg", "png", "webp", "jpg", "jpeg", "gif")


def buscar_asset(nombre: str) -> str | None:
    """
    Devuelve la URL de `static/<nombre>.<ext>` si existe, o None.

    Se consulta en cada petición, no una vez al arrancar. Cuesta un `stat` por
    imagen (nada) y a cambio basta con recargar la página tras añadir un
    archivo: `uvicorn --reload` solo vigila los `.py`, así que si esto se
    calculara al importar el módulo habría que reiniciar el servidor para ver
    un logo nuevo.

    Devolver None en vez de una ruta fija evita el icono de imagen rota: la
    plantilla simplemente no pinta la etiqueta.
    """
    for extension in EXTENSIONES:
        if (STATIC_DIR / f"{nombre}.{extension}").is_file():
            return f"/static/{nombre}.{extension}"
    return None


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    ciego: bool = Query(
        False,
        description="Oculta lo que propuso el modelo. Para etiquetar sin anclaje.",
    ),
    session: Session = Depends(get_session),
):
    """
    La bandeja de revisión: propuestas pendientes, de la más reciente abajo.

    Se renderiza en el servidor en vez de dejar que el navegador pida los datos
    por su cuenta. Así la página llega ya con contenido: no hay parpadeo de
    lista vacía, y si el JavaScript fallara la lista se seguiría viendo (solo
    dejarían de funcionar los botones).

    MODO CIEGO (`?ciego=1`): oculta la categoría propuesta y la explicación del
    modelo, dejando solo el correo y los siete botones.

    No es una florituras de interfaz, es un instrumento de medida. Ver "el
    modelo dice: promociones" ANTES de pensar la respuesta sesga hacia darle la
    razón: aprobar es un clic y llevarle la contraria cuesta. Etiquetas así
    sacadas inflan el acierto medido, que es exactamente el error que costó 18,7
    puntos en la Fase 6.

    En modo ciego cada clic es una etiqueta sin anclar, comparable con las que
    salieron de `build_labels.py`. La decisión se guarda igual (`category`
    conserva lo que dijo el modelo, `final_category` lo que elegiste), así que
    la comparación se puede hacer después sin haberla visto antes.
    """
    pendientes = propuestas_pendientes(session, limit=limit)
    total = session.execute(
        select(func.count())
        .select_from(ActionProposal)
        .join(Email)
        .where(
            ActionProposal.status == ProposalStatus.PENDING,
            Email.en_papelera.is_(False),
        )
    ).scalar_one()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "propuestas": pendientes,
            "total": total,
            "mostrando": len(pendientes),
            "offset": 0,
            "limit": limit,
            "categorias": list(Category),
            "stats": estadisticas(session),
            "acciones": contar_acciones(session),
            "sin_aplicar": decisiones_sin_aplicar(session),
            "ciego": ciego,
            "vista": "pendientes",
            "logo": buscar_asset("logo"),
            # El logo horizontal lleva "MailPilot" en marino sobre fondo
            # transparente: sobre el fondo oscuro sería invisible. Por eso hay
            # una variante y la plantilla las cambia con <picture>.
            "logo_oscuro": buscar_asset("logo-oscuro"),
            "favicon": buscar_asset("favicon"),
        },
    )


@router.get("/clasificados", response_class=HTMLResponse, include_in_schema=False)
def clasificados(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    """
    Lo que ya está organizado, y sigue en la bandeja.

    Se enseñan las dos cosas a la vez: lo que propuso el modelo y lo que
    elegiste tú. No hay riesgo de anclaje porque ya decidiste; lo que hace
    falta ahora es justo lo contrario, ver en qué os diferenciasteis, y poder
    arreglar un clic equivocado.

    Lo que está en la papelera NO sale aquí: tiene su propia pestaña. Su
    clasificación no se borra y sigue contando para medir al modelo (ADR 002),
    simplemente no es algo que estés organizando.

    Tiene paginación de verdad, a diferencia de la lista de pendientes: estas
    no desaparecen al tocarlas, así que la lista solo crece.
    """
    total = session.execute(
        select(func.count())
        .select_from(ActionProposal)
        .join(Email)
        .where(
            ActionProposal.status != ProposalStatus.PENDING,
            Email.en_papelera.is_(False),
        )
    ).scalar_one()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "propuestas": propuestas_clasificadas(session, limit=limit, offset=offset),
            "total": total,
            "offset": offset,
            "limit": limit,
            "categorias": list(Category),
            "stats": estadisticas(session),
            "acciones": contar_acciones(session),
            "sin_aplicar": decisiones_sin_aplicar(session),
            "ciego": False,
            "vista": "clasificados",
            "logo": buscar_asset("logo"),
            "logo_oscuro": buscar_asset("logo-oscuro"),
            "favicon": buscar_asset("favicon"),
        },
    )


@router.get("/papelera", response_class=HTMLResponse, include_in_schema=False)
def papelera(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    """
    Lo que está hoy en la papelera de Gmail, lo tirara quien lo tirara.

    Da igual si lo mandó MailPilot o si la usuaria lo arrastró a la papelera
    desde Gmail: lo que se enseña es el ESTADO ACTUAL de la cuenta, no lo que
    hicimos nosotros. Por eso se lee de `Email.en_papelera` y no de las
    acciones ejecutadas.

    Un correo tirado a mano en Gmail desaparece de la ingestión —la API excluye
    la papelera por defecto—, así que su fila se queda con datos viejos y solo
    la sincronización se entera. De ahí el botón.
    """
    total = session.execute(
        select(func.count()).select_from(Email).where(Email.en_papelera.is_(True))
    ).scalar_one()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "propuestas": [],
            "papelera": emails_en_papelera(session, limit=limit, offset=offset),
            "total": total,
            "mostrando": limit,
            "offset": offset,
            "limit": limit,
            "categorias": list(Category),
            "stats": estadisticas(session),
            "acciones": contar_acciones(session),
            "sin_aplicar": 0,
            "ciego": False,
            "vista": "papelera",
            "logo": buscar_asset("logo"),
            "logo_oscuro": buscar_asset("logo-oscuro"),
            "favicon": buscar_asset("favicon"),
        },
    )
