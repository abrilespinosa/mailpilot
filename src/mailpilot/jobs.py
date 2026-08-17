"""
El trabajo largo que hay detrás del botón "Cargar correos".

POR QUÉ ESTO NO ES UN ENDPOINT NORMAL
-------------------------------------
Traer correos de Gmail tarda segundos. Clasificarlos tarda ~10 s CADA UNO. Un
lote de 100 son casi veinte minutos: ninguna petición HTTP sobrevive a eso, y
el navegador se rendiría mucho antes con un error que no explica nada.

Así que la petición solo ARRANCA el trabajo y contesta al instante. El trabajo
sigue por su cuenta y va dejando su progreso aquí, donde el dashboard lo
consulta cada pocos segundos para pintar la barra.

POR QUÉ EN MEMORIA Y NO EN LA BASE DE DATOS
-------------------------------------------
El progreso vive en un diccionario de módulo. Es single-tenant, un proceso, y
lo que se pierde al reiniciar es solo la barra: los correos y las
clasificaciones se van guardando en PostgreSQL sobre la marcha, con commit por
correo. Reiniciar a mitad no pierde trabajo, solo la cuenta de cuánto llevaba.

Una tabla `jobs` sería lo correcto si hubiera varios procesos o hiciera falta
histórico. No los hay, y el ADR de este proyecto pide no meter maquinaria antes
de tener el problema.
"""

import threading
import time
from datetime import datetime, timezone

from mailpilot.classifier import OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData, fetch_messages, list_message_ids
from mailpilot.repository import (
    emails_sin_clasificar,
    generar_propuestas,
    save_classification,
    upsert_emails,
)

# Tope por pulsación. No es una limitación técnica: es que un lote más grande
# tarda tanto que deja de ser "cargar correos" y pasa a ser un proceso nocturno,
# y para eso ya está `scripts/classify.py`.
MAXIMO_POR_TANDA = 100

# El estado del único trabajo que puede haber a la vez.
#
# `_candado` protege las escrituras: el trabajo corre en un hilo aparte y el
# endpoint de estado lee desde el hilo de la petición.
_candado = threading.Lock()
_estado: dict = {
    "estado": "inactivo",  # inactivo | trayendo | clasificando | terminado | error
    "hechos": 0,
    "total": 0,
    "nuevos": 0,
    "mensaje": "",
    "empezado": None,
    "segundos_por_correo": None,
}


def estado_actual() -> dict:
    """
    Una foto del progreso, con el porcentaje y lo que queda ya calculado.

    Se calculan aquí y no en el navegador para que la cuenta viva en un solo
    sitio: si el cálculo estuviera en JavaScript, cualquier cambio habría que
    hacerlo dos veces.
    """
    with _candado:
        foto = dict(_estado)

    total = foto["total"]
    hechos = foto["hechos"]
    foto["porcentaje"] = round(100 * hechos / total) if total else 0

    ritmo = foto.get("segundos_por_correo")
    if ritmo and total > hechos:
        foto["segundos_restantes"] = round((total - hechos) * ritmo)
    else:
        foto["segundos_restantes"] = None

    foto["ocupado"] = foto["estado"] in ("trayendo", "clasificando")
    return foto


def _actualizar(**campos) -> None:
    with _candado:
        _estado.update(campos)


def hay_uno_corriendo() -> bool:
    with _candado:
        return _estado["estado"] in ("trayendo", "clasificando")


def cargar_y_clasificar(service, limite: int = MAXIMO_POR_TANDA) -> None:
    """
    Trae los correos nuevos de Gmail, los clasifica y deja las propuestas.

    `service` llega ya construido desde la petición A PROPÓSITO. Construirlo
    aquí dentro significaría pedir credenciales dentro del hilo de fondo, donde
    un fallo de token no tiene a quién contárselo: la petición ya habría
    contestado 200 y la usuaria vería la barra congelada sin explicación.
    Pidiéndolo antes, el token caducado sale como un 503 con instrucciones.
    """
    try:
        _actualizar(
            estado="trayendo",
            hechos=0,
            total=0,
            nuevos=0,
            mensaje="Preguntando a Gmail…",
            empezado=datetime.now(timezone.utc).isoformat(),
            segundos_por_correo=None,
        )

        ids = list_message_ids(service, limit=limite)
        correos = fetch_messages(service, ids)

        with SessionLocal() as session:
            nuevos, _ = upsert_emails(session, correos)

        _actualizar(nuevos=nuevos, mensaje=f"{nuevos} correos nuevos. Clasificando…")

        # Se clasifica lo que esté sin clasificar, no solo lo recién traído. Si
        # una tanda anterior se cortó a medias, esta la termina en vez de dejar
        # correos huérfanos que no aparecen en ninguna pantalla.
        cliente = OllamaClient()
        with SessionLocal() as session:
            pendientes = emails_sin_clasificar(session, limit=limite)
            _actualizar(estado="clasificando", total=len(pendientes))

            tiempos: list[float] = []
            for correo in pendientes:
                inicio = time.perf_counter()
                try:
                    resultado = classify_email(cliente, _a_emaildata(correo))
                except Exception:
                    # Un correo que el modelo no digiere no puede parar la
                    # tanda. Se salta y se cuenta como hecho: si no, la barra
                    # se quedaría clavada para siempre.
                    _actualizar(hechos=_estado["hechos"] + 1)
                    continue

                save_classification(
                    session,
                    email_id=correo.id,
                    category=resultado.category,
                    confidence=resultado.confidence,
                    reasoning=resultado.reasoning,
                    model_used=cliente.model,
                )

                tiempos.append(time.perf_counter() - inicio)
                _actualizar(
                    hechos=_estado["hechos"] + 1,
                    # La media de los últimos diez, no de todos: el primero
                    # incluye cargar el modelo en memoria y desvía la
                    # estimación durante el resto de la tanda.
                    segundos_por_correo=sum(tiempos[-10:]) / len(tiempos[-10:]),
                )

            creadas = generar_propuestas(session, limit=limite)

        _actualizar(
            estado="terminado",
            mensaje=f"{creadas} correos listos para revisar.",
            segundos_por_correo=None,
        )

    except Exception as error:  # noqa: BLE001
        # Cualquier fallo tiene que llegar a la pantalla. Un hilo de fondo que
        # muere en silencio deja la barra girando para siempre, que es la peor
        # forma de fallar: no puedes ni saber que ha fallado.
        _actualizar(
            estado="error",
            mensaje=f"{type(error).__name__}: {error}",
            segundos_por_correo=None,
        )


def _a_emaildata(correo) -> EmailData:
    """El clasificador trabaja con EmailData, no con el modelo de SQLAlchemy."""
    return EmailData(
        gmail_message_id=correo.gmail_message_id,
        gmail_thread_id=correo.gmail_thread_id,
        subject=correo.subject,
        sender=correo.sender,
        snippet=correo.snippet,
        received_at=correo.received_at,
        raw_labels=correo.raw_labels,
    )
