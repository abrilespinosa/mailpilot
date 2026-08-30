"""
Los guardarraíles de la partición train/test.

Estos tests protegen el activo más caro del proyecto: 758 etiquetas puestas a
mano, a ciegas, una por una. El código que las reparte puede romperlas de tres
maneras, y ninguna de las tres da error al ejecutarse:

- **Filtrar el examen**: que un correo acabe en `train` Y en `test`. El modelo
  estudia las respuestas y el acierto sale inflado sin que nada avise.
- **Perder trabajo humano**: que el test jubilado se tire en vez de reciclarse
  a `train`. Está quemado para MEDIR, no para APRENDER.
- **Colar una etiqueta anclada**: una decidida viendo la propuesta del modelo.
  Enseñaría al modelo nuevo a copiar los sesgos del viejo, sin forma de saber
  cuánto (el anclaje está medido: `otros` sube del 65,4 % al 87,5 %).

Los tres fallos se descubrirían semanas después, al no cuadrar un número, y
para entonces no habría manera de saber cuál de los experimentos guardados está
contaminado. Por eso se prueban aquí y no a ojo.

No tocan PostgreSQL: `extraer` se sustituye por un doble. Lo que se prueba es
el reparto, no la consulta.
"""

import json

import pytest

import scripts.construir_dataset as cd


# ---------------------------------------------------------------------------
# Andamiaje
# ---------------------------------------------------------------------------


def fila(email_id: int, categoria: str = "avisos", a_ciegas: bool = True) -> dict:
    """Una etiqueta con la forma que devuelve `extraer`, sin base de datos."""
    return {
        "email_id": email_id,
        "remitente": f"quien{email_id}@ejemplo.com",
        "dominio": "ejemplo.com",
        "asunto": f"asunto {email_id}",
        "snippet": "",
        "categoria": categoria,
        "a_ciegas": a_ciegas,
    }


class SesionFalsa:
    """Doble de SessionLocal: no conecta a nada, solo hace de gestor de contexto."""

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def conjunto(tmp_path, monkeypatch):
    """
    Redirige el script a un dataset.json de usar y tirar.

    Sin esto los tests escribirían sobre `entrenamiento/dataset.json`, que es
    justo el archivo cuya integridad se está probando.
    """
    destino = tmp_path / "dataset.json"
    monkeypatch.setattr(cd, "DESTINO", destino)
    monkeypatch.setattr(cd, "SessionLocal", SesionFalsa())

    def escribir(train: list[dict], test: list[dict], **extra) -> None:
        destino.write_text(
            json.dumps(
                {
                    "semilla": cd.SEMILLA,
                    "proporcion_test": cd.PROPORCION_TEST,
                    "solo_a_ciegas": True,
                    "total": len(train) + len(test),
                    "train": train,
                    "test": test,
                    **extra,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def leer() -> dict:
        return json.loads(destino.read_text(encoding="utf-8"))

    escribir.leer = leer
    escribir.ruta = destino
    return escribir


def con_etiquetas_en_la_base(monkeypatch, filas: list[dict]) -> None:
    monkeypatch.setattr(cd, "extraer", lambda _session: filas)


# ---------------------------------------------------------------------------
# --nuevo-test: jubilar el examen gastado
# ---------------------------------------------------------------------------


def test_el_test_jubilado_pasa_entero_a_train(conjunto, monkeypatch):
    """
    Ni una etiqueta se pierde al jubilar.

    Mirar un test lo inutiliza para medir, no para aprender: las etiquetas
    siguen siendo correctas y humanas. Tirarlas sería tirar trabajo
    irrepetible.
    """
    viejas_train = [fila(i) for i in range(1, 11)]
    viejas_test = [fila(i) for i in range(100, 105)]
    nuevas = [fila(i) for i in range(200, 260)]
    conjunto(viejas_train, viejas_test)
    con_etiquetas_en_la_base(monkeypatch, viejas_train + viejas_test + nuevas)

    cd.nuevo_test()

    datos = conjunto.leer()
    en_train = {f["email_id"] for f in datos["train"]}
    assert {f["email_id"] for f in viejas_test} <= en_train
    assert {f["email_id"] for f in viejas_train} <= en_train
    assert len(datos["train"]) == len(viejas_train) + len(viejas_test)


def test_el_test_nuevo_son_solo_correos_nunca_vistos(conjunto, monkeypatch):
    viejas_train = [fila(i) for i in range(1, 11)]
    viejas_test = [fila(i) for i in range(100, 105)]
    nuevas = [fila(i) for i in range(200, 260)]
    conjunto(viejas_train, viejas_test)
    con_etiquetas_en_la_base(monkeypatch, viejas_train + viejas_test + nuevas)

    cd.nuevo_test()

    datos = conjunto.leer()
    assert {f["email_id"] for f in datos["test"]} == {f["email_id"] for f in nuevas}


def test_ningun_correo_queda_en_las_dos_mitades(conjunto, monkeypatch):
    """
    EL invariante. Un correo en `train` y en `test` a la vez es el modelo
    estudiando las respuestas del examen, y el acierto sale inflado en
    silencio.
    """
    viejas_train = [fila(i) for i in range(1, 11)]
    viejas_test = [fila(i) for i in range(100, 105)]
    nuevas = [fila(i) for i in range(200, 260)]
    conjunto(viejas_train, viejas_test)
    con_etiquetas_en_la_base(monkeypatch, viejas_train + viejas_test + nuevas)

    cd.nuevo_test()

    datos = conjunto.leer()
    en_train = {f["email_id"] for f in datos["train"]}
    en_test = {f["email_id"] for f in datos["test"]}
    assert not (en_train & en_test)
    assert datos["total"] == len(datos["train"]) + len(datos["test"])


def test_una_etiqueta_anclada_no_puede_entrar_en_el_test(conjunto, monkeypatch):
    """
    Segunda línea de defensa. `extraer` ya filtra por `decidido_a_ciegas`, pero
    si esa consulta cambiara, el `assert` del script es lo único que queda
    entre una etiqueta contaminada y el conjunto de medir.
    """
    conjunto([fila(i) for i in range(1, 11)], [fila(i) for i in range(100, 105)])
    nuevas = [fila(i) for i in range(200, 260)]
    nuevas[7]["a_ciegas"] = False
    con_etiquetas_en_la_base(monkeypatch, nuevas)

    with pytest.raises(AssertionError, match="anclada"):
        cd.nuevo_test()


def test_se_planta_por_debajo_del_minimo_y_no_toca_nada(conjunto, monkeypatch):
    """
    Un test de 20 correos mide con ±19 puntos: es tanto como no medir. Y
    gastaría el examen viejo a cambio de nada, que es lo irreversible.
    """
    viejas_test = [fila(i) for i in range(100, 191)]
    conjunto([fila(i) for i in range(1, 11)], viejas_test)
    con_etiquetas_en_la_base(monkeypatch, [fila(i) for i in range(200, 220)])
    antes = conjunto.ruta.read_text(encoding="utf-8")

    cd.nuevo_test()

    assert conjunto.ruta.read_text(encoding="utf-8") == antes


def test_force_salta_el_minimo_a_sabiendas(conjunto, monkeypatch):
    conjunto([fila(i) for i in range(1, 11)], [fila(i) for i in range(100, 105)])
    nuevas = [fila(i) for i in range(200, 220)]
    con_etiquetas_en_la_base(monkeypatch, nuevas)

    cd.nuevo_test(forzar=True)

    assert len(conjunto.leer()["test"]) == len(nuevas)


def test_sin_etiquetas_nuevas_no_se_jubila_nada(conjunto, monkeypatch):
    """
    El peor momento para quedarse sin examen es cuando no hay con qué
    sustituirlo.
    """
    viejas_test = [fila(i) for i in range(100, 105)]
    conjunto([fila(i) for i in range(1, 11)], viejas_test)
    con_etiquetas_en_la_base(monkeypatch, [fila(i) for i in range(1, 11)] + viejas_test)
    antes = conjunto.ruta.read_text(encoding="utf-8")

    cd.nuevo_test()

    assert conjunto.ruta.read_text(encoding="utf-8") == antes


def test_la_generacion_sube_y_queda_registrada(conjunto, monkeypatch):
    """
    Sin el número de generación no hay forma de saber, meses después, a qué
    examen se refiere un acierto guardado. Y los aciertos de dos generaciones
    NO son comparables.
    """
    viejas_test = [fila(i) for i in range(100, 105)]
    conjunto([fila(i) for i in range(1, 11)], viejas_test)
    con_etiquetas_en_la_base(monkeypatch, [fila(i) for i in range(200, 260)])

    cd.nuevo_test()

    datos = conjunto.leer()
    assert datos["generacion_test"] == 2
    assert datos["tests_retirados"] == [
        {"generacion": 1, "n": len(viejas_test), "destino": "train"}
    ]


# ---------------------------------------------------------------------------
# --ampliar: la operación contraria
# ---------------------------------------------------------------------------


def test_ampliar_no_toca_el_test(conjunto, monkeypatch):
    """
    Es lo que hace comparable el resultado con el anterior: mismo examen, más
    material de estudio. Si se moviera el test, no se sabría si el modelo
    mejoró o si el examen se puso más fácil.
    """
    viejas_test = [fila(i) for i in range(100, 105)]
    conjunto([fila(i) for i in range(1, 11)], viejas_test)
    con_etiquetas_en_la_base(monkeypatch, [fila(i) for i in range(200, 230)])

    cd.ampliar_solo_train()

    datos = conjunto.leer()
    assert datos["test"] == viejas_test
    assert len(datos["train"]) == 10 + 30


def test_ampliar_nunca_mueve_un_correo_de_test_a_train(conjunto, monkeypatch):
    """
    El doble devuelve una fila de `test` disfrazada de nueva. Si el filtro por
    `email_id` fallara, el modelo entrenaría con las respuestas del examen.
    """
    viejas_test = [fila(i) for i in range(100, 105)]
    conjunto([fila(i) for i in range(1, 11)], viejas_test)
    con_etiquetas_en_la_base(monkeypatch, viejas_test + [fila(200)])

    cd.ampliar_solo_train()

    datos = conjunto.leer()
    en_train = {f["email_id"] for f in datos["train"]}
    assert not (en_train & {f["email_id"] for f in viejas_test})
    assert 200 in en_train


def test_ampliar_y_nuevo_test_se_rechazan_juntos(conjunto, monkeypatch):
    """
    Se contradicen: uno manda las etiquetas nuevas a `train`, el otro las
    reserva para medir. Ejecutar los dos gastaría el examen sin ponerle
    sustituto, y no hay forma de deshacerlo.
    """
    monkeypatch.setattr(
        "sys.argv", ["construir_dataset.py", "--ampliar", "--nuevo-test"]
    )

    with pytest.raises(SystemExit, match="Elige"):
        cd.main()


# ---------------------------------------------------------------------------
# La partición inicial
# ---------------------------------------------------------------------------


def test_partir_deja_cada_categoria_en_las_dos_mitades():
    """
    Estratificar existe para esto: una categoría con cero ejemplos en `test` es
    una categoría sobre la que no se puede afirmar nada, y con cero en `train`
    es una que el modelo no aprende.
    """
    filas = [fila(i, "avisos") for i in range(100)]
    filas += [fila(500 + i, "social") for i in range(8)]
    filas += [fila(700 + i, "personal") for i in range(20)]

    train, test = partir_ok(filas)

    for categoria in {"avisos", "social", "personal"}:
        assert any(f["categoria"] == categoria for f in train), categoria
        assert any(f["categoria"] == categoria for f in test), categoria


def test_partir_no_repite_ni_pierde_correos():
    filas = [fila(i, "avisos") for i in range(50)] + [
        fila(500 + i, "social") for i in range(9)
    ]

    train, test = partir_ok(filas)

    ids_train = {f["email_id"] for f in train}
    ids_test = {f["email_id"] for f in test}
    assert not (ids_train & ids_test)
    assert ids_train | ids_test == {f["email_id"] for f in filas}


def test_partir_es_determinista():
    """
    La semilla fija es lo que permite reproducir un experimento. Si la
    partición cambiara entre ejecuciones, el `test` de hoy tendría correos que
    ayer estaban en `train`.
    """
    filas = [fila(i, "avisos") for i in range(40)]

    assert partir_ok(filas) == partir_ok(filas)


def partir_ok(filas: list[dict]):
    return cd.partir([dict(f) for f in filas])


# ---------------------------------------------------------------------------
# El margen de error
# ---------------------------------------------------------------------------


def test_el_margen_de_error_baja_al_crecer_la_muestra():
    assert cd.margen_de_error(50) > cd.margen_de_error(200) > cd.margen_de_error(800)


def test_los_margenes_citados_en_la_documentacion_son_los_que_salen():
    """
    Las decisiones de cuántos correos etiquetar se tomaron con estos números
    (91 -> ±9, 200 -> ±6). Si la fórmula cambia, el razonamiento guardado deja
    de sostenerse y hay que enterarse aquí.
    """
    assert round(cd.margen_de_error(91)) == 9
    assert round(cd.margen_de_error(200)) == 6
    assert cd.margen_de_error(0) == 0.0
