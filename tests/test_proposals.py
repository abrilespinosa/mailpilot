"""
Tests del sistema de propuestas.

Aquí se prueba el principio central del proyecto: la IA propone, la usuaria
decide, y queda registro de en qué se equivocó el modelo.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from mailpilot.models import (
    ActionProposal,
    AuditLog,
    Category,
    Email,
    ProposalStatus,
    ProposedAction,
)
from mailpilot.repository import (
    PropuestaNoDecidida,
    PropuestaYaDecidida,
    correcciones,
    decidir_propuesta,
    generar_propuestas,
    propuestas_pendientes,
    rectificar_decision,
    save_classification,
    upsert_emails,
)
from tests.test_repository import make_email

pytestmark = pytest.mark.db


def preparar(session, message_id="a", categoria=Category.PROMOCIONES, confianza=0.9):
    """Deja un correo clasificado y listo para generarle una propuesta."""
    upsert_emails(session, [make_email(message_id)])
    email = session.execute(
        select(Email).where(Email.gmail_message_id == message_id)
    ).scalar_one()

    save_classification(
        session,
        email_id=email.id,
        category=categoria,
        confidence=confianza,
        reasoning="porque sí",
        model_used="qwen3:8b",
    )
    return email


# ---------------------------------------------------------------------------
# Generación
# ---------------------------------------------------------------------------


def test_genera_una_propuesta_por_correo_clasificado(session):
    preparar(session)

    assert generar_propuestas(session) == 1

    propuesta = session.execute(select(ActionProposal)).scalar_one()
    assert propuesta.status is ProposalStatus.PENDING
    assert propuesta.category is Category.PROMOCIONES
    assert propuesta.proposed_action is ProposedAction.CATEGORIZE
    assert propuesta.final_category is None
    assert propuesta.decided_at is None


def test_por_ahora_nunca_propone_papelera(session):
    """
    Decisión explícita: solo se proponen categorías.

    Proponer mover a papelera llega cuando exista la ejecución real (Fase 9) y
    se pueda probar de punta a punta. Si alguien lo adelanta, este test avisa.
    """
    preparar(session)
    generar_propuestas(session)

    propuestas = session.execute(select(ActionProposal)).scalars().all()
    assert all(p.proposed_action is ProposedAction.CATEGORIZE for p in propuestas)


def test_no_genera_dos_veces_para_el_mismo_correo(session):
    preparar(session)

    assert generar_propuestas(session) == 1
    assert generar_propuestas(session) == 0


def test_no_reabre_lo_que_la_usuaria_ya_decidio(session):
    """
    Regla de CLAUDE.md: si un correo ya tiene una propuesta decidida,
    reprocesarlo NO debe generar una nueva.

    Sin esto, volver a clasificar le preguntaría otra vez a la usuaria algo
    sobre lo que ya opinó.
    """
    email = preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    # Llega una clasificación nueva del mismo correo, p. ej. con otro modelo
    save_classification(
        session,
        email_id=email.id,
        category=Category.AVISOS,
        confidence=0.8,
        reasoning="otro modelo",
        model_used="llama3.1:8b",
    )

    assert generar_propuestas(session) == 0


def test_usa_la_clasificacion_mas_reciente(session):
    """
    Classification guarda histórico, así que "la categoría actual" es la última
    por fecha. Si se cogiera la primera, reclasificar no serviría de nada.
    """
    email = preparar(session, categoria=Category.PROMOCIONES)
    save_classification(
        session,
        email_id=email.id,
        category=Category.EMPLEO,
        confidence=0.95,
        reasoning="mejor prompt",
        model_used="qwen3:8b",
    )

    generar_propuestas(session)

    propuesta = session.execute(select(ActionProposal)).scalar_one()
    assert propuesta.category is Category.EMPLEO


# ---------------------------------------------------------------------------
# Decidir
# ---------------------------------------------------------------------------


def test_aprobar_copia_la_categoria_propuesta(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidida = decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    assert decidida.status is ProposalStatus.APPROVED
    assert decidida.final_category is Category.PROMOCIONES
    assert decidida.decided_at is not None


def test_modificar_conserva_lo_que_dijo_el_modelo(session):
    """
    EL TEST CENTRAL DE LA FASE 7.

    Al corregir, `category` sigue siendo lo que propuso la IA y `final_category`
    lo que eligió la usuaria. Si al corregir se machacara `category`, se
    perdería la única forma de saber en qué se equivoca el clasificador.
    """
    preparar(session, categoria=Category.PROMOCIONES)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidida = decidir_propuesta(
        session, propuesta.id, ProposalStatus.MODIFIED, Category.EMPLEO
    )

    assert decidida.category is Category.PROMOCIONES     # lo que dijo la IA
    assert decidida.final_category is Category.EMPLEO   # lo que dijo la usuaria
    assert decidida.status is ProposalStatus.MODIFIED


def test_rechazar_no_aplica_ninguna_categoria(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidida = decidir_propuesta(session, propuesta.id, ProposalStatus.REJECTED)

    assert decidida.status is ProposalStatus.REJECTED
    assert decidida.final_category is None


def test_modificar_sin_categoria_es_un_error(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    with pytest.raises(ValueError):
        decidir_propuesta(session, propuesta.id, ProposalStatus.MODIFIED)


def test_no_se_puede_decidir_dos_veces(session):
    """
    Con dos pestañas abiertas, la segunda no debe pisar la decisión de la
    primera en silencio. Falla de forma ruidosa.
    """
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    with pytest.raises(PropuestaYaDecidida):
        decidir_propuesta(session, propuesta.id, ProposalStatus.REJECTED)


def test_propuesta_inexistente(session):
    with pytest.raises(LookupError):
        decidir_propuesta(session, 99999, ProposalStatus.APPROVED)


# ---------------------------------------------------------------------------
# Pendientes y correcciones
# ---------------------------------------------------------------------------


def test_las_decididas_desaparecen_de_pendientes(session):
    preparar(session, "a")
    preparar(session, "b")
    generar_propuestas(session)
    assert len(propuestas_pendientes(session)) == 2

    primera = propuestas_pendientes(session)[0]
    decidir_propuesta(session, primera.id, ProposalStatus.APPROVED)

    assert len(propuestas_pendientes(session)) == 1


def test_las_correcciones_son_etiquetas_gratis(session):
    """
    Lo que hace valioso todo esto: cada corrección es una etiqueta correcta
    obtenida con uso real, sin etiquetar nada a mano.
    """
    preparar(session, "a", categoria=Category.PROMOCIONES)
    preparar(session, "b", categoria=Category.AVISOS)
    generar_propuestas(session)
    pendientes = propuestas_pendientes(session)

    decidir_propuesta(session, pendientes[0].id, ProposalStatus.APPROVED)
    decidir_propuesta(
        session, pendientes[1].id, ProposalStatus.MODIFIED, Category.EMPLEO
    )

    fallos = correcciones(session)

    assert len(fallos) == 1
    assert fallos[0].category is not fallos[0].final_category


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------


def test_cada_decision_queda_registrada(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidir_propuesta(
        session, propuesta.id, ProposalStatus.MODIFIED, Category.EMPLEO
    )

    registros = (
        session.execute(
            select(AuditLog).where(AuditLog.event_type == "proposal_modified")
        )
        .scalars()
        .all()
    )

    assert len(registros) == 1
    assert registros[0].detail == {
        "propuesta": "promociones",
        "elegida": "empleo",
        "acierto": False,
    }
    assert registros[0].action_proposal_id == propuesta.id


def test_el_audit_log_marca_los_aciertos(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    registro = (
        session.execute(
            select(AuditLog).where(AuditLog.event_type == "proposal_approved")
        )
        .scalars()
        .one()
    )
    assert registro.detail["acierto"] is True


def test_nada_de_esto_toca_gmail(session):
    """
    Recordatorio explícito: decidir una propuesta solo escribe en la base de
    datos. La ejecución contra Gmail llega en la Fase 9, y el scope de OAuth
    sigue siendo gmail.readonly, así que hoy sería imposible aunque se quisiera.
    """
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    decidida = decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    # No hay estado EXECUTED: aprobar no ejecuta.
    assert decidida.status is ProposalStatus.APPROVED
    assert decidida.status is not ProposalStatus.EXECUTED


# ---------------------------------------------------------------------------
# Rectificar: volver sobre una decisión ya tomada
# ---------------------------------------------------------------------------


def test_rectificar_cambia_la_eleccion_sin_tocar_lo_que_dijo_el_modelo(session):
    """
    El caso de uso real: clic equivocado.

    Aunque la decisión cambie tres veces, `category` sigue siendo lo que
    propuso la IA. Es el dato con el que se la mide y no se pierde nunca.
    """
    preparar(session, categoria=Category.PROMOCIONES)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    rectificada = rectificar_decision(session, propuesta.id, Category.EMPLEO)

    assert rectificada.category is Category.PROMOCIONES     # intacto
    assert rectificada.final_category is Category.EMPLEO
    assert rectificada.status is ProposalStatus.MODIFIED    # ya no coincide


def test_rectificar_hacia_la_categoria_del_modelo_vuelve_a_aprobada(session):
    """El estado se recalcula solo: si acaba coincidiendo, es una aprobación."""
    preparar(session, categoria=Category.PROMOCIONES)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.MODIFIED, Category.EMPLEO)

    rectificada = rectificar_decision(session, propuesta.id, Category.PROMOCIONES)

    assert rectificada.status is ProposalStatus.APPROVED
    assert rectificada.final_category is Category.PROMOCIONES
    assert correcciones(session) == []      # deja de contar como corrección


def test_rectificar_sin_categoria_descarta(session):
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    rectificada = rectificar_decision(session, propuesta.id, None)

    assert rectificada.status is ProposalStatus.REJECTED
    assert rectificada.final_category is None


def test_no_se_rectifica_lo_que_aun_no_se_ha_decidido(session):
    """
    Rectificar es para volver sobre algo ya mirado. Una propuesta pendiente se
    decide por el camino normal; si se pudieran hacer las dos cosas, el 409 que
    protege de dos pestañas abiertas dejaría de servir para nada.
    """
    preparar(session)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()

    with pytest.raises(PropuestaNoDecidida):
        rectificar_decision(session, propuesta.id, Category.EMPLEO)


def test_rectificar_algo_inexistente(session):
    with pytest.raises(LookupError):
        rectificar_decision(session, 99999, Category.EMPLEO)


def test_cada_rectificacion_deja_rastro_con_el_valor_anterior(session):
    """
    Sin esto, rectificar hoy haría irreproducible una medición de ayer: los
    números de evaluación salen de estas filas. El audit log guarda de qué a
    qué se cambió, así que siempre se puede reconstruir.
    """
    preparar(session, categoria=Category.PROMOCIONES)
    generar_propuestas(session)
    propuesta = session.execute(select(ActionProposal)).scalar_one()
    decidir_propuesta(session, propuesta.id, ProposalStatus.APPROVED)

    rectificar_decision(session, propuesta.id, Category.AVISOS)

    registro = session.execute(
        select(AuditLog).where(AuditLog.event_type == "proposal_rectified")
    ).scalars().one()

    assert registro.detail == {
        "propuesta": "promociones",   # lo que dijo el modelo
        "antes": "promociones",       # lo que habías elegido
        "ahora": "avisos",            # lo que eliges ahora
    }
