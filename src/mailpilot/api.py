"""
API HTTP de MailPilot.

De momento SOLO LECTURA. No hay ningún endpoint que modifique nada, ni en la
base de datos ni en Gmail. Las acciones llegan en la Fase 7 y pasarán por
propuesta → aprobación explícita → ejecución → audit log.

Arrancar en desarrollo:
    uvicorn mailpilot.api:app --reload --app-dir src
"""

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mailpilot.db import SessionLocal
from mailpilot.models import Email
from mailpilot.schemas import EmailDetail, EmailPage

app = FastAPI(
    title="MailPilot",
    version="0.1.0",
    description="Capa de gestión inteligente sobre Gmail. Solo lectura por ahora.",
)


def get_session() -> Iterator[Session]:
    """
    Abre una sesión por petición y la cierra al terminar, pase lo que pase.

    Se inyecta con Depends en vez de crearla dentro de cada endpoint: así los
    tests pueden sustituirla por la sesión de prueba sin tocar los endpoints.
    """
    with SessionLocal() as session:
        yield session


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
