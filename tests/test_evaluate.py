"""
El informe de evaluación: que no se calle nada.

Un informe de evaluación tiene un fallo peculiar y muy caro: cuando se
equivoca, sigue imprimiendo un porcentaje perfectamente creíble. No revienta,
no avisa, y el número se copia a la documentación y se cita durante meses. Este
proyecto ya pagó ese precio dos veces —el 92,5 % inflado de la Fase 6 y el 82 %
que se citó sin ser comparable con nada—, así que aquí se prueban las tres
formas en que este script puede mentir sin fallar:

- **Callarse una categoría.** `CATEGORIAS` estuvo escrita a mano con las siete
  de antes del ADR 006, y la matriz filtraba las filas por esa lista: una fila
  de `seguridad`, `boletines`, `social` o `empleo` desaparecía del informe
  mientras el acierto global sí las contaba.
- **Dar recall y llamarlo precisión.** Son preguntas distintas y pueden apuntar
  a lados opuestos.
- **Medir un examen de otra asignatura.** `labels.json` sigue en la taxonomía
  de siete, y su número no se puede comparar con nada posterior.

No llaman a Ollama ni a PostgreSQL: se les pasan resultados ya hechos.
"""

import pytest

import scripts.evaluate as ev
from mailpilot.models import Category


def resultado(esperada: str, predicha: str, confianza: float | None = None) -> dict:
    return {
        "email_id": 1,
        "subject": "asunto",
        "expected": esperada,
        "predicted": predicha,
        "confidence": confianza,
        "correct": esperada == predicha,
        "seconds": None,
    }


# ---------------------------------------------------------------------------
# Que no se calle una categoría
# ---------------------------------------------------------------------------


def test_las_categorias_salen_del_enum_y_no_de_una_lista_a_mano():
    """
    El guardarraíl del fallo original. Volver a escribirlas a mano funciona el
    día que se escribe y se pudre en el siguiente ADR que toque la taxonomía.
    """
    assert ev.CATEGORIAS == [c.value for c in Category]


@pytest.mark.parametrize("categoria", ["seguridad", "boletines", "social", "empleo"])
def test_las_categorias_nuevas_salen_en_la_matriz(capsys, categoria):
    """
    Las cuatro que llegaron con el ADR 006. Antes desaparecían de la matriz sin
    decir nada, que es justo el fallo que no se nota al leer el informe.
    """
    ev.matriz_de_confusion([resultado(categoria, "avisos")])

    filas = capsys.readouterr().out.splitlines()
    assert any(linea.strip().startswith(categoria) for linea in filas)


def test_una_categoria_ajena_tampoco_se_descarta(capsys):
    """
    `ERROR` es lo que se apunta cuando la salida del modelo no valida. Si se
    cayera del informe, un modelo que falla la mitad de las veces parecería
    tener una matriz limpia.
    """
    ev.matriz_de_confusion([resultado("avisos", "ERROR"), resultado("avisos", "avisos")])

    salida = capsys.readouterr().out
    assert "ERROR" in salida


def test_toda_categoria_contada_en_el_acierto_aparece_en_el_informe(capsys):
    """
    El invariante que resume a los anteriores: si un correo entra en el
    porcentaje, su categoría tiene que salir en el desglose. Un acierto global
    y un desglose que no cuadran es exactamente lo que no se detecta a ojo.
    """
    resultados = [
        resultado("seguridad", "seguridad"),
        resultado("boletines", "promociones"),
        resultado("social", "social"),
        resultado("empleo", "empleo"),
        resultado("otros", "avisos"),
    ]

    ev.por_categoria(resultados)

    salida = capsys.readouterr().out
    for r in resultados:
        assert r["expected"] in salida
        assert r["predicted"] in salida


# ---------------------------------------------------------------------------
# Precisión y recall no son lo mismo
# ---------------------------------------------------------------------------


def test_precision_y_recall_apuntan_a_lados_distintos(capsys):
    """
    Un modelo que contesta `otros` a todo: recall PERFECTO en `otros` y
    precisión pésima. Con solo el recall a la vista, ese modelo parece
    excelente en la categoría en la que es inútil.
    """
    resultados = [resultado("otros", "otros")] * 3
    resultados += [resultado("avisos", "otros")] * 7

    ev.por_categoria(resultados)

    linea = next(
        l for l in capsys.readouterr().out.splitlines() if l.strip().startswith("otros")
    )
    assert "100.0%" in linea  # recall: pilló los 3 que eran `otros`
    assert "30.0%" in linea  # precisión: de los 10 que llamó así, acertó 3


def test_una_categoria_sin_ejemplos_reales_no_finge_un_recall(capsys):
    """
    El modelo dice `social` dos veces y en el conjunto no hay ni un `social`
    de verdad. El recall no es 0 %, es que NO SE PUEDE CALCULAR: dividir entre
    cero ejemplos no da un cero, da una pregunta sin datos. Imprimir 0,0 %
    invitaría a "arreglar" una categoría que nadie ha medido.
    """
    ev.por_categoria([resultado("avisos", "social")] * 2 + [resultado("avisos", "avisos")])

    linea = next(
        l for l in capsys.readouterr().out.splitlines()
        if l.strip().startswith("social")
    )
    assert "·" in linea  # recall: sin ejemplos, no hay número
    assert "0.0%" in linea  # precisión: sí se puede, y es cero de dos


def test_avisa_de_las_categorias_con_pocos_ejemplos(capsys):
    ev.por_categoria([resultado("social", "social")] * 2 + [resultado("avisos", "avisos")] * 9)

    salida = capsys.readouterr().out
    assert "menos de 5 ejemplos" in salida
    assert "social" in salida.split("menos de 5 ejemplos")[1]


# ---------------------------------------------------------------------------
# Que no se mida un examen de otra asignatura
# ---------------------------------------------------------------------------


def test_avisa_si_el_conjunto_usa_la_taxonomia_de_siete(capsys):
    """
    `labels.json` sigue con `trabajo`, que dejó de existir el 2026-08-17. Sin
    aviso, el script imprime un porcentaje creíble sobre otra taxonomía.
    """
    ev.avisar_si_taxonomia_vieja([{"expected": "trabajo"}, {"expected": "avisos"}])

    salida = capsys.readouterr().out
    assert "trabajo" in salida
    assert "NO es comparable" in salida
    assert "scripts.comparar" in salida


def test_no_avisa_si_todas_las_categorias_son_actuales(capsys):
    ev.avisar_si_taxonomia_vieja([{"expected": c.value} for c in Category])

    assert capsys.readouterr().out == ""
