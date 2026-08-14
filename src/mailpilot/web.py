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
from mailpilot.models import ActionProposal, Category, ProposalStatus
from mailpilot.repository import estadisticas, propuestas_pendientes

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
    total_pendientes = session.execute(
        select(func.count())
        .select_from(ActionProposal)
        .where(ActionProposal.status == ProposalStatus.PENDING)
    ).scalar_one()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "propuestas": pendientes,
            "total_pendientes": total_pendientes,
            "mostrando": len(pendientes),
            "categorias": list(Category),
            "stats": estadisticas(session),
            "ciego": ciego,
            "logo": buscar_asset("logo"),
            "favicon": buscar_asset("favicon"),
        },
    )
