"""
La regla del árbitro y la caché de predicciones del LLM.

Se prueban las dos formas en que esto puede corromper un experimento sin
fallar:

- **Degradar mal.** Si falta la opinión de qwen3 —no se pidió, o falló— el
  árbitro tiene que quedarse con el modelo entrenado. Cualquier otra cosa
  (saltarse el correo, dejarlo vacío) haría que la ausencia de árbitro
  empeorase el sistema que ya funcionaba.
- **Mezclar compañeros.** Las predicciones guardadas solo valen para el modelo
  y el prompt con los que se hicieron. Mezclar dos daría un árbitro afinado
  contra un compañero que no existe, y daría un número perfectamente creíble.

`sklearn` vive en el grupo opcional `[entrenamiento]` y CI instala solo
`[dev]`, así que sin él estos tests se saltan en vez de romper la suite.
"""

import json

import pytest

pytest.importorskip("sklearn", reason="scripts/arbitro.py necesita [entrenamiento]")

import scripts.arbitro as ar  # noqa: E402


# ---------------------------------------------------------------------------
# La regla
# ---------------------------------------------------------------------------


def test_por_encima_del_umbral_manda_el_entrenado():
    salida = ar.arbitrar(["avisos"], [0.9], ["compras"], umbral=0.5)
    assert list(salida) == ["avisos"]


def test_por_debajo_del_umbral_cede_al_llm():
    salida = ar.arbitrar(["avisos"], [0.2], ["compras"], umbral=0.5)
    assert list(salida) == ["compras"]


def test_justo_en_el_umbral_no_cede():
    """
    `>=` y no `>`: la frontera tiene que estar escrita en algún sitio, y un
    umbral que se comporta distinto según el redondeo del float es la clase de
    detalle que después nadie consigue reproducir.
    """
    assert list(ar.arbitrar(["avisos"], [0.5], ["compras"], umbral=0.5)) == ["avisos"]


@pytest.mark.parametrize("sin_opinion", [None, ""])
def test_sin_opinion_del_llm_se_queda_el_entrenado(sin_opinion):
    """
    LA DEGRADACIÓN CORRECTA. Un correo por el que no se preguntó, o para el que
    qwen3 falló, no puede quedarse sin categoría: el sistema sin árbitro es el
    que ya había, no uno peor.
    """
    salida = ar.arbitrar(["avisos"], [0.1], [sin_opinion], umbral=0.5)
    assert list(salida) == ["avisos"]


def test_un_umbral_de_cero_desactiva_el_arbitro():
    """
    Con umbral 0 nadie cede nunca, así que el resultado tiene que ser
    exactamente el del modelo entrenado. Es el control del propio árbitro: si
    esto no se cumple, la regla está haciendo algo que no dice.
    """
    entrenado = ["avisos", "compras", "personal"]
    salida = ar.arbitrar(entrenado, [0.0, 0.3, 0.9], ["otros"] * 3, umbral=0.0)
    assert list(salida) == entrenado


def test_el_acierto_con_umbral_cuenta_lo_que_arbitra():
    verdad = ["avisos", "compras"]
    # El entrenado acierta el primero con confianza alta; el segundo lo falla
    # con confianza baja y el LLM lo salva.
    acierto = ar.acierto_con_umbral(
        ["avisos", "otros"], [0.9, 0.1], ["personal", "compras"], verdad, umbral=0.5
    )
    assert acierto == 1.0


# ---------------------------------------------------------------------------
# La caché: que no mezcle compañeros
# ---------------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    destino = tmp_path / "predicciones_llm.json"
    monkeypatch.setattr(ar, "PREDICCIONES_LLM", destino)
    return destino


def test_la_cache_se_lee_si_coincide_modelo_y_prompt(cache):
    ar.guardar_predicciones_llm("qwen3:8b", "v8", {"1": "avisos"})

    assert ar.cargar_predicciones_llm("qwen3:8b", "v8") == {"1": "avisos"}


@pytest.mark.parametrize(
    "modelo,prompt", [("llama3.1:8b", "v8"), ("qwen3:8b", "v9")]
)
def test_la_cache_se_niega_a_mezclar_modelos_o_prompts(cache, modelo, prompt):
    """
    Cambiar de modelo o de prompt cambia al compañero del árbitro. Si esto
    dejara pasar, el árbitro se afinaría contra una mezcla de dos y el número
    saldría creíble.
    """
    ar.guardar_predicciones_llm("qwen3:8b", "v8", {"1": "avisos"})

    with pytest.raises(SystemExit, match="Mezclarlas"):
        ar.cargar_predicciones_llm(modelo, prompt)


def test_sin_cache_no_falla_devuelve_vacio(cache):
    """La primera ejecución no tiene caché, y eso no es un error."""
    assert ar.cargar_predicciones_llm("qwen3:8b", "v8") == {}


def test_la_cache_guarda_con_que_se_hizo(cache):
    ar.guardar_predicciones_llm("qwen3:8b", "v8", {"1": "avisos"})

    datos = json.loads(cache.read_text(encoding="utf-8"))
    assert datos["modelo"] == "qwen3:8b"
    assert datos["prompt"] == "v8"


# ---------------------------------------------------------------------------
# La regla que sí funciona: ceder por categoría
# ---------------------------------------------------------------------------


def test_cede_cuando_qwen3_dice_una_categoria_de_la_lista():
    salida = ar.arbitrar_por_categoria(["avisos"], ["seguridad"], {"seguridad"})
    assert list(salida) == ["seguridad"]


def test_no_cede_en_una_categoria_fuera_de_la_lista():
    salida = ar.arbitrar_por_categoria(["avisos"], ["promociones"], {"seguridad"})
    assert list(salida) == ["avisos"]


def test_la_lista_de_cesion_es_exactamente_esta():
    """
    Guardarraíl deliberado, igual que el de `GmailActionType`. Cada categoría
    añadida aquí es una forma nueva de que qwen3 meta un error donde el modelo
    entrenado acertaba: ceder en `avisos` arreglaría 7 y rompería 24.

    `tramites` (10-4, p=0,18) es la candidata obvia y está FUERA a propósito
    hasta que la generación 3 la juzgue. Si alguien la mete, que sea viniendo
    a cambiar esto a mano.
    """
    assert ar.CEDER_A_QWEN3 == frozenset({"seguridad"})


def test_una_lista_vacia_devuelve_al_entrenado_intacto():
    """El control de la regla: sin categorías, el árbitro no puede hacer nada."""
    entrenado = ["avisos", "compras", "personal"]
    salida = ar.arbitrar_por_categoria(entrenado, ["seguridad"] * 3, set())
    assert list(salida) == entrenado


def test_sin_opinion_del_llm_la_regla_por_categoria_tambien_degrada_bien():
    assert list(ar.arbitrar_por_categoria(["avisos"], [None], {"seguridad"})) == ["avisos"]


# ---------------------------------------------------------------------------
# El test pareado
# ---------------------------------------------------------------------------


def test_el_pareado_cuenta_arreglados_y_rotos():
    import numpy as np

    base = np.array([True, False, False, True])
    nuevo = np.array([True, True, False, False])

    arregla, rompe, _ = ar.test_pareado(base, nuevo)
    assert (arregla, rompe) == (1, 1)


def test_diez_de_diez_es_significativo_y_diez_de_catorce_no():
    """
    Los dos casos reales de `train`. Es la diferencia entre `seguridad`, que
    entra, y `tramites`, que se queda fuera esperando a la generación 3.
    """
    import numpy as np

    _, _, p_limpio = ar.test_pareado(np.array([False] * 10), np.array([True] * 10))
    _, _, p_sucio = ar.test_pareado(
        np.array([False] * 10 + [True] * 4), np.array([True] * 10 + [False] * 4)
    )

    assert p_limpio < 0.01
    assert p_sucio > 0.05


def test_sin_ningun_cambio_no_hay_nada_que_probar():
    """
    Cero cambios no es "significativamente igual", es que no hay datos. Devolver
    p=1 y no dividir entre cero es lo correcto.
    """
    import numpy as np

    base = np.array([True, False])
    assert ar.test_pareado(base, base) == (0, 0, 1.0)
