"""
Tests del único módulo que escribe en Gmail.

Ninguno llama a Gmail de verdad. Los dobles de `fakes.py` no tienen `send()`
ni `delete()` a propósito: si el código intentara usarlos, el test reventaría
con AttributeError en vez de pasar en silencio.
"""

import pytest
from sqlalchemy import select

from mailpilot import gmail_actions
from mailpilot.models import (
    ActionProposal,
    AuditLog,
    Category,
    Email,
    GmailAction,
    GmailActionStatus,
    GmailActionType,
    ProposalStatus,
    ProposedAction,
)
from mailpilot.repository import (
    decidir_propuesta,
    generar_propuestas,
    save_classification,
    upsert_emails,
)
from tests.fakes import FakeLabels, FakeWritableMessages, FakeWritableService
from tests.test_repository import make_email

pytestmark = pytest.mark.db


@pytest.fixture
def service():
    return FakeWritableService(FakeWritableMessages(), FakeLabels())


def preparar_decidida(session, categoria=Category.PROMOCIONES, elegida=None):
    """Un correo clasificado, con propuesta ya decidida por la usuaria."""
    upsert_emails(session, [make_email("a")])
    email = session.execute(select(Email)).scalar_one()
    save_classification(
        session,
        email_id=email.id,
        category=categoria,
        confidence=0.9,
        reasoning="porque sí",
        model_used="qwen3:8b",
    )
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    if elegida and elegida != categoria:
        decidir_propuesta(session, propuesta.id, ProposalStatus.MODIFIED, elegida)
    else:
        decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    return email, propuesta


def encolar(session, email, accion, propuesta=None):
    """
    Devuelve la acción pendiente de ese tipo, creándola si no la hay.

    Decidir una propuesta YA encola su `apply_label`, así que en ese caso aquí
    solo hay que recogerla. Crear otra chocaría con el índice único parcial,
    que es justo lo que debe pasar.
    """
    ya = session.execute(
        select(GmailAction).where(
            GmailAction.email_id == email.id,
            GmailAction.action == accion,
            GmailAction.status == GmailActionStatus.PENDING,
        )
    ).scalar_one_or_none()
    if ya is not None:
        return ya

    fila = GmailAction(
        email_id=email.id,
        action=accion,
        action_proposal_id=propuesta.id if propuesta else None,
    )
    session.add(fila)
    session.commit()
    return fila


# ---------------------------------------------------------------------------
# Etiquetar
# ---------------------------------------------------------------------------


def test_las_etiquetas_se_llaman_como_las_ve_la_usuaria(session, service):
    """
    En Gmail se llaman "Trámites", con tilde y mayúscula, no `tramites`.

    El enum interno sigue sin tildes: los identificadores del código y lo
    que lee una persona son cosas distintas.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    assert service.labels().create_calls[0]["body"]["name"] == "Promociones"
    assert accion.detail == {"etiqueta": "Promociones"}


def test_no_recrea_una_etiqueta_que_ya_existe(session):
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        FakeWritableMessages(), FakeLabels({"Promociones": "Label_7"})
    )

    gmail_actions.ejecutar(session, service, accion)

    assert service.labels().create_calls == []
    assert service.messages().modify_calls[0]["body"] == {"addLabelIds": ["Label_7"]}


def test_nunca_quita_una_etiqueta_que_no_sea_suya(session):
    """
    LA REGLA QUE NO SE PUEDE ROMPER.

    MailPilot puede quitar sus propias etiquetas —las siete del enum— porque
    las decisiones cambian y un correo no puede acabar con dos. Lo que no puede
    tocar es nada más: ni las etiquetas de la usuaria ni las de Gmail.

    Al quitar el prefijo `MailPilot/`, el criterio de "cuáles son nuestras"
    pasó a ser este conjunto cerrado. Si se ampliara sin pensar, MailPilot
    podría empezar a quitar etiquetas ajenas.

    Quitar `INBOX` archivaría el correo. Es destructivo, nadie lo ha pedido, y
    se colaría con un simple descuido al construir la lista.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        FakeWritableMessages(),
        FakeLabels({
            "Trabajo": "Label_mp1",       # nuestra, sobra -> se quita
            "Promociones": "Label_mp2",   # nuestra, es la que toca
            "Universidad": "Label_suya",  # SUYA
            "INBOX": "INBOX",             # de Gmail
            "STARRED": "STARRED",
        }),
    )

    gmail_actions.ejecutar(session, service, accion)

    cuerpo = service.messages().modify_calls[0]["body"]
    assert cuerpo["addLabelIds"] == ["Label_mp2"]
    assert cuerpo["removeLabelIds"] == ["Label_mp1"]
    for intocable in ("Label_suya", "INBOX", "STARRED"):
        assert intocable not in cuerpo["removeLabelIds"]


def test_sin_otras_etiquetas_nuestras_no_manda_removeLabelIds(session, service):
    """Nada que quitar, ninguna clave de más en la petición."""
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    assert list(service.messages().modify_calls[0]["body"]) == ["addLabelIds"]


def test_etiqueta_lo_que_eligio_la_usuaria_no_lo_que_dijo_la_ia(session, service):
    """
    EL TEST CENTRAL DE LA FASE 9.

    El modelo dijo `promociones` y la usuaria lo corrigió a `trabajo`. En Gmail
    tiene que aparecer `trabajo`. Etiquetar con la propuesta de la IA sería
    ejecutar algo que la usuaria había rechazado, que es justo lo que el
    proyecto promete no hacer.
    """
    email, propuesta = preparar_decidida(
        session, categoria=Category.PROMOCIONES, elegida=Category.TRABAJO
    )
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    assert accion.detail == {"etiqueta": "Trabajo"}


def test_no_etiqueta_si_la_usuaria_no_ha_decidido(session, service):
    """Sin decisión humana no hay etiqueta: la acción falla, no adivina."""
    upsert_emails(session, [make_email("a")])
    email = session.execute(select(Email)).scalar_one()
    accion = encolar(session, email, GmailActionType.APPLY_LABEL)

    gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.FAILED
    assert "ValueError" in accion.detail["error"]
    assert service.messages().modify_calls == []


# ---------------------------------------------------------------------------
# Papelera
# ---------------------------------------------------------------------------


def test_mover_a_papelera_usa_trash_no_delete(session, service):
    """
    `messages.trash` es reversible 30 días. `messages.delete` no existe en este
    proyecto y además sería imposible: exige un scope que no pedimos.
    """
    email, _ = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.MOVE_TO_TRASH)

    gmail_actions.ejecutar(session, service, accion)

    assert service.messages().trash_calls[0]["id"] == email.gmail_message_id
    assert accion.status is GmailActionStatus.EXECUTED
    assert accion.detail["reversible_dias"] == 30


def test_la_papelera_no_necesita_propuesta(session, service):
    """
    Tirar y clasificar son ejes independientes (ADR 002). Mover a la papelera
    lo pide la usuaria directamente, sin que la IA haya propuesto nada.
    """
    upsert_emails(session, [make_email("a")])
    email = session.execute(select(Email)).scalar_one()
    accion = encolar(session, email, GmailActionType.MOVE_TO_TRASH)

    gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.EXECUTED


# ---------------------------------------------------------------------------
# Fallos
# ---------------------------------------------------------------------------


def test_un_fallo_de_gmail_no_se_traga_en_silencio(session):
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        FakeWritableMessages(fallar_con=RuntimeError("503 Service Unavailable")),
        FakeLabels({"Promociones": "Label_1"}),
    )

    gmail_actions.ejecutar(session, service, accion)

    assert accion.status is GmailActionStatus.FAILED
    assert "503" in accion.detail["error"]

    registro = session.execute(
        select(AuditLog).where(AuditLog.event_type == "gmail_action_failed")
    ).scalars().one()
    assert registro.detail["accion"] == "apply_label"


def test_no_reejecuta_lo_ya_ejecutado(session, service):
    """
    Corta antes de llamar a Gmail en vez de fiarse solo de que la API sea
    idempotente. Una llamada que no se hace no puede fallar.
    """
    email, _ = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.MOVE_TO_TRASH)

    gmail_actions.ejecutar(session, service, accion)
    gmail_actions.ejecutar(session, service, accion)

    assert len(service.messages().trash_calls) == 1


def test_cada_ejecucion_queda_en_el_audit_log(session, service):
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    registro = session.execute(
        select(AuditLog).where(AuditLog.event_type == "gmail_action_executed")
    ).scalars().one()
    assert registro.detail == {
        "accion": "apply_label",
        "etiqueta": "Promociones",
    }
    assert registro.email_id == email.id


# ---------------------------------------------------------------------------
# Lo que el módulo NO expone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prohibida", ["enviar", "send", "borrar", "delete", "responder"])
def test_el_modulo_no_expone_funciones_de_envio_ni_borrado(prohibida):
    """
    La superficie pública es la garantía: si no existe la función, nadie la
    llama por error desde otro módulo.
    """
    publicas = [n for n in dir(gmail_actions) if not n.startswith("_")]
    assert not [n for n in publicas if prohibida in n.lower()]


# ---------------------------------------------------------------------------
# El flujo entero, de punta a punta
# ---------------------------------------------------------------------------


def test_de_la_decision_a_gmail(session, service):
    """
    EL TEST QUE RESUME LA FASE 9.

    La IA dice `promociones`, la usuaria corrige a `trabajo` y además lo tira.
    Al final, en Gmail: etiqueta `MailPilot/trabajo` y el mensaje en la
    papelera. Y en la base de datos, todo el rastro de por qué.
    """
    email, propuesta = preparar_decidida(
        session, categoria=Category.PROMOCIONES, elegida=Category.TRABAJO
    )
    from mailpilot.repository import encolar_accion

    # Aprobar/corregir ya encoló la etiqueta; la papelera la pide ella aparte.
    encolar_accion(session, email.id, GmailActionType.MOVE_TO_TRASH)

    from mailpilot.repository import acciones_pendientes

    pendientes = acciones_pendientes(session)
    assert len(pendientes) == 2

    for accion in pendientes:
        gmail_actions.ejecutar(session, service, accion)

    assert service.labels().create_calls[0]["body"]["name"] == "Trabajo"
    assert service.messages().trash_calls[0]["id"] == email.gmail_message_id
    assert all(a.status is GmailActionStatus.EXECUTED for a in pendientes)

    # Lo tirado NO desaparece del cálculo del acierto: sigue siendo una
    # corrección del modelo, esté en la papelera o no (ADR 002).
    from mailpilot.repository import correcciones

    assert len(correcciones(session)) == 1


def test_tirar_un_correo_no_cambia_las_estadisticas_de_acierto(session, service):
    """
    EL CANARIO DEL ADR 002.

    Si algún día hay que tocar `estadisticas()` para tener en cuenta lo tirado,
    es que los dos ejes se han mezclado. Aquí se comprueba que tirar un correo
    deja el acierto exactamente igual.
    """
    from mailpilot.repository import encolar_accion, estadisticas

    email, _ = preparar_decidida(session)
    antes = estadisticas(session)

    accion = encolar(session, email, GmailActionType.MOVE_TO_TRASH)
    gmail_actions.ejecutar(session, service, accion)

    assert estadisticas(session) == antes


# ---------------------------------------------------------------------------
# Recuperar de la papelera
# ---------------------------------------------------------------------------


def test_recuperar_saca_de_la_papelera(session, service):
    email, _ = preparar_decidida(session)
    email.en_papelera = True
    session.commit()
    accion = encolar(session, email, GmailActionType.RESTORE_FROM_TRASH)

    gmail_actions.ejecutar(session, service, accion)

    assert service.messages().untrash_calls[0]["id"] == email.gmail_message_id
    assert email.en_papelera is False
    assert accion.detail["recuperado"] is True


def test_recuperar_devuelve_a_recibidos_solo_si_estaba_ahi(session, service):
    """
    `untrash` únicamente quita la etiqueta TRASH: NO devuelve el correo a
    Recibidos, porque Gmail le quitó INBOX al tirarlo. Sin añadirlo, el correo
    saldría de la papelera y quedaría archivado, imposible de encontrar.

    Se mira `raw_labels`, que es la foto de justo antes: un correo tirado
    desaparece de la ingestión, así que ese campo nunca se sobrescribió.
    """
    email, _ = preparar_decidida(session)          # make_email trae INBOX
    email.en_papelera = True
    session.commit()
    accion = encolar(session, email, GmailActionType.RESTORE_FROM_TRASH)

    gmail_actions.ejecutar(session, service, accion)

    assert service.messages().modify_calls[-1]["body"] == {"addLabelIds": ["INBOX"]}
    assert accion.detail["a_recibidos"] is True


def test_recuperar_algo_archivado_no_lo_desarchiva(session, service):
    """Hacer más de lo que se pidió también es un error."""
    upsert_emails(session, [make_email("a", raw_labels=["CATEGORY_PROMOTIONS"])])
    email = session.execute(select(Email)).scalar_one()
    email.en_papelera = True
    session.commit()
    accion = encolar(session, email, GmailActionType.RESTORE_FROM_TRASH)

    gmail_actions.ejecutar(session, service, accion)

    assert service.messages().untrash_calls
    assert service.messages().modify_calls == []
    assert accion.detail["a_recibidos"] is False


def test_recuperar_devuelve_tambien_su_categoria(session, service):
    """
    Lo que pidió la usuaria: al recuperar, el correo vuelve CON su categoría.

    Importa sobre todo en lo que se tiró a mano en Gmail antes de que se
    aplicara la etiqueta: sin esto reaparecería sin clasificar, aunque la
    decisión llevara semanas guardada.
    """
    from mailpilot.repository import acciones_pendientes, pedir_recuperacion

    email, propuesta = preparar_decidida(
        session, categoria=Category.PROMOCIONES, elegida=Category.TRABAJO
    )
    # Se tiró antes de aplicar nada: la etiqueta pendiente se descarta.
    for a in session.execute(select(GmailAction)).scalars().all():
        session.delete(a)
    email.en_papelera = True
    session.commit()

    assert pedir_recuperacion(session, email.id) is True

    tipos = {a.action for a in acciones_pendientes(session)}
    assert tipos == {
        GmailActionType.RESTORE_FROM_TRASH,
        GmailActionType.APPLY_LABEL,
    }

    for accion in acciones_pendientes(session):
        gmail_actions.ejecutar(session, service, accion)

    assert email.en_papelera is False
    assert service.labels().create_calls[0]["body"]["name"] == "Trabajo"


def test_pedir_recuperacion_dos_veces_no_duplica(session):
    from mailpilot.repository import pedir_recuperacion

    email, _ = preparar_decidida(session)
    email.en_papelera = True
    session.commit()

    assert pedir_recuperacion(session, email.id) is True
    assert pedir_recuperacion(session, email.id) is False
