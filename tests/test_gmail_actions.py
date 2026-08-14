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


def test_las_etiquetas_cuelgan_de_un_padre_propio(session, service):
    """
    `MailPilot/promociones`, no `promociones` a secas.

    Así no pueden chocar con una etiqueta que la usuaria ya tenga con ese
    nombre, y borrar la etiqueta padre se lleva las siete de golpe.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    assert service.labels().create_calls[0]["body"]["name"] == "MailPilot/promociones"
    assert accion.detail == {"etiqueta": "MailPilot/promociones"}


def test_no_recrea_una_etiqueta_que_ya_existe(session):
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)
    service = FakeWritableService(
        FakeWritableMessages(), FakeLabels({"MailPilot/promociones": "Label_7"})
    )

    gmail_actions.ejecutar(session, service, accion)

    assert service.labels().create_calls == []
    assert service.messages().modify_calls[0]["body"] == {"addLabelIds": ["Label_7"]}


def test_solo_anade_etiquetas_nunca_quita(session, service):
    """
    Solo `addLabelIds`. Si apareciera `removeLabelIds`, MailPilot podría
    deshacer la organización que la usuaria ya tenía, y eso está fuera del
    alcance del proyecto.
    """
    email, propuesta = preparar_decidida(session)
    accion = encolar(session, email, GmailActionType.APPLY_LABEL, propuesta)

    gmail_actions.ejecutar(session, service, accion)

    cuerpo = service.messages().modify_calls[0]["body"]
    assert "removeLabelIds" not in cuerpo
    assert list(cuerpo) == ["addLabelIds"]


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

    assert accion.detail == {"etiqueta": "MailPilot/trabajo"}


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
        FakeLabels({"MailPilot/promociones": "Label_1"}),
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
        "etiqueta": "MailPilot/promociones",
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
