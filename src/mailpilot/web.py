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


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
):
    """
    La bandeja de revisión: propuestas pendientes, de la más reciente abajo.

    Se renderiza en el servidor en vez de dejar que el navegador pida los datos
    por su cuenta. Así la página llega ya con contenido: no hay parpadeo de
    lista vacía, y si el JavaScript fallara la lista se seguiría viendo (solo
    dejarían de funcionar los botones).
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
        },
    )
