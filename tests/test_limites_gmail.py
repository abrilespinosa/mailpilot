"""
Los límites de lo que MailPilot puede hacerle a Gmail.

Estos tests no comprueban que algo funcione: comprueban que algo NO EXISTE.
Son la clase de test que se escribe una vez y protege durante años, porque
avisan cuando alguien —yo dentro de seis meses, o cualquiera— añade una
capacidad que se decidió no tener.

Dos reglas, y no son igual de fuertes (ver ADR 003):

- NUNCA borrado permanente: lo impide Google. Pedimos `gmail.modify`, y
  `users.messages.delete` exige el scope completo. Un bug no puede saltárselo.
- NUNCA enviar correo: NO lo impide Google. `gmail.modify` permite enviar. Solo
  lo impide nuestro código, y por eso este archivo existe.
"""

import re
from pathlib import Path

import pytest

from mailpilot import auth
from mailpilot.models import GmailActionType

CODIGO = Path(__file__).resolve().parents[1] / "src" / "mailpilot"


def modulos():
    return sorted(CODIGO.rglob("*.py"))


# ---------------------------------------------------------------------------
# El enum: no se puede ni nombrar
# ---------------------------------------------------------------------------


def test_solo_existen_dos_acciones(f=None):
    """
    Enum cerrado, la misma defensa que se usa contra la prompt injection.

    No se trata de vigilar que nadie pida enviar o borrar: es que no hay
    ninguna forma de expresarlo. Añadir un valor aquí es una decisión
    deliberada que rompe este test.
    """
    assert {a.value for a in GmailActionType} == {"apply_label", "move_to_trash"}


@pytest.mark.parametrize("prohibido", ["send", "delete", "forward", "reply", "draft"])
def test_el_enum_no_contiene_acciones_prohibidas(prohibido):
    for accion in GmailActionType:
        assert prohibido not in accion.value


# ---------------------------------------------------------------------------
# El scope: lo que Google nos deja hacer
# ---------------------------------------------------------------------------


def test_no_pedimos_el_scope_que_permite_borrar_para_siempre():
    """
    `https://mail.google.com/` es el único scope que habilita
    `users.messages.delete`. No pedirlo convierte "nunca borrado permanente"
    en una garantía externa: la impone Google, no nuestra disciplina.
    """
    assert "https://mail.google.com/" not in auth.SCOPES


def test_no_pedimos_scopes_de_envio():
    """
    Defensa en profundidad. `gmail.modify` ya permite enviar, así que quitar
    esto no bastaría; pero pedir además `gmail.send` o `gmail.compose` sería
    declarar una intención que el proyecto no tiene.
    """
    for scope in auth.SCOPES:
        assert "gmail.send" not in scope
        assert "gmail.compose" not in scope


# ---------------------------------------------------------------------------
# El código: rastrear llamadas prohibidas
# ---------------------------------------------------------------------------


# Llamadas de la Gmail API que este proyecto no debe contener nunca. Se buscan
# como texto en el código fuente: es tosco, pero es lo que detecta el caso que
# importa, alguien escribiendo la llamada en cualquier módulo.
PROHIBIDAS = {
    r"\.send\s*\(": "enviar correo",
    r"\.drafts\s*\(": "crear borradores",
    r"messages\(\)\s*\.\s*delete": "borrado permanente",
    r"\.batchDelete": "borrado permanente masivo",
}


def test_ningun_modulo_llama_a_enviar_ni_a_borrar():
    """
    EL TEST QUE SOSTIENE LA REGLA "NUNCA ENVIAR".

    Las otras defensas —el enum de dos valores, un solo módulo que escribe— son
    disciplina, y la disciplina se olvida. Esto no.

    Si algún día hace falta enviar correo de verdad, el camino no es borrar
    este test: es una decisión de producto que se discute, se escribe en un ADR
    y entonces se cambia aquí a propósito.
    """
    encontrados = []

    for modulo in modulos():
        fuente = modulo.read_text()
        for patron, que_es in PROHIBIDAS.items():
            for linea_n, linea in enumerate(fuente.splitlines(), 1):
                # Los comentarios y docstrings hablan de estas llamadas
                # precisamente para explicar por qué no están.
                if linea.lstrip().startswith("#"):
                    continue
                if re.search(patron, linea):
                    encontrados.append(f"{modulo.name}:{linea_n} {que_es}: {linea.strip()}")

    assert not encontrados, "Llamadas prohibidas a Gmail:\n" + "\n".join(encontrados)


# Llamadas que SÍ existen, pero solo pueden vivir en un módulo.
ESCRITURAS = {
    r"\.modify\s*\(": "modificar etiquetas de un mensaje",
    r"\.trash\s*\(": "mover a papelera",
    r"labels\(\)\s*\.\s*create": "crear una etiqueta",
}

MODULO_AUTORIZADO = "gmail_actions.py"


def test_solo_un_modulo_escribe_en_gmail():
    """
    La propiedad que hace revisable el proyecto entero.

    Con las escrituras concentradas en un archivo, auditar qué puede hacerle
    MailPilot a tu cuenta es leer 150 líneas. Repartidas por seis módulos,
    nadie las revisa nunca y cualquiera añade una capacidad sin querer.

    Es la misma idea que hace que `auth.py` sea el único que toca
    `credentials/`: una frontera estrecha y vigilada vale más que muchas
    reglas repartidas.
    """
    intrusos = []

    for modulo in modulos():
        if modulo.name == MODULO_AUTORIZADO:
            continue
        for linea_n, linea in enumerate(modulo.read_text().splitlines(), 1):
            if linea.lstrip().startswith("#"):
                continue
            for patron, que_es in ESCRITURAS.items():
                if re.search(patron, linea):
                    intrusos.append(f"{modulo.name}:{linea_n} {que_es}")

    assert not intrusos, (
        f"Solo {MODULO_AUTORIZADO} puede escribir en Gmail:\n" + "\n".join(intrusos)
    )
