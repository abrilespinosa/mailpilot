"""
El árbitro: cuándo creer al modelo entrenado y cuándo a qwen3.

DE DÓNDE SALE ESTO
------------------
Los dos modelos aciertan por debajo del 61 %, pero se equivocan en correos
DISTINTOS. Sobre el test de la generación 2 (199 correos, ya gastado):

    de acuerdo        82 (41 %)   y ahí aciertan el 85,4 %
    en desacuerdo    117 (59 %)   solo el entrenado 50, solo qwen3 38,
                                  ninguno de los dos 29

Un árbitro perfecto llegaría al 79,4 % frente al 60,3 % del mejor por
separado. Ese 79,4 % es un TECHO teórico, no una expectativa: supone saber
siempre a quién creer, que es precisamente el problema.

EL UMBRAL DE CONFIANZA NO FUNCIONA. LA CATEGORÍA SÍ.
-----------------------------------------------------
Primera hipótesis, y era buena: la confianza del modelo entrenado SÍ está
calibrada, al contrario que la de qwen3.

    separación entre aciertos y fallos, qwen3        +0,004 a +0,019
    separación del modelo entrenado (fuera de fold)  +0,266

Y sube monótona: 35,6 % de acierto por debajo de 0,3 y 99,4 % por encima de
0,8. Parecía que bastaba con ceder el correo dudoso a qwen3. **Falso**, y lo
mató un solo número:

    en el correo donde el entrenado duda (conf < 0,60, n=264)
        modelo entrenado  58,3 %
        qwen3:8b          50,4 %

qwen3 no es mejor en el correo difícil: es PEOR. No hay bolsa que ceder, y la
validación anidada del umbral lo confirma (76,6 % ± 5,0 frente al 77,8 % de no
hacer nada). El 54,3 % global de qwen3 nunca fue su nota en el correo duro.

Lo que sí funciona es condicionar por LO QUE DICE qwen3, no por lo seguro que
esté el entrenado. Test pareado sobre los 559 de `train`, fuera de partición:

    cuando qwen3 dice `seguridad` y el entrenado dice otra cosa
        arregla 10, rompe 0   (p = 0,002; 0,02 tras corregir por 10 categorías)

DIEZ DE DIEZ. Y no es casualidad estadística, tiene mecanismo: `seguridad` se
define por "¿va de acceder a una cuenta mía?", que es una pregunta sobre lo que
el correo te PIDE hacer. TF-IDF cuenta palabras, y "confirma tu cuenta"
comparte casi todo el vocabulario con "bienvenido a". Es exactamente la
frontera donde meter el cuerpo del correo dio la mayor mejora al modelo
entrenado (+0,11 en `seguridad`, ADR 007). Dos señales independientes apuntando
al mismo sitio pesan más que un p-valor.

`tramites` es candidata pero NO entra: 10-4 por su cuenta (p = 0,18) es
sugerente y nada más. Que lo resuelva la generación 3.

TODAS LAS DECISIONES SE TOMAN CON `train` Y VALIDACIÓN CRUZADA
--------------------------------------------------------------
El umbral se elige mirando `train` fuera de partición. El `test` de la
generación 2 está gastado —se le miraron los desacuerdos por categoría para
diseñar esto—, así que no puede juzgar su propio diseño. Medir el árbitro
honestamente exige una generación 3.

    python -m scripts.arbitro      # OJO: como módulo, no por ruta
"""

import json
from math import comb
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from scripts.entrenar import cargar, construir_modelo

RAIZ = Path(__file__).resolve().parents[1]
PREDICCIONES_LLM = RAIZ / "entrenamiento" / "predicciones_llm.json"
DATASET = RAIZ / "entrenamiento" / "dataset.json"

SEMILLA = 20260826

# Por encima de esto el modelo entrenado acierta el 87,7 % y sube. Ningún
# árbitro sensato le lleva la contraria ahí, así que tampoco hace falta gastar
# 6,3 s de qwen3 en pedirle una segunda opinión que no se va a usar.
UMBRAL_MAXIMO_UTIL = 0.60


def confianzas_fuera_de_particion(X, y):
    """
    Predicción y confianza de cada correo de `train`, SIN que el modelo lo haya
    visto al entrenar.

    ESTO ES LO QUE HACE HONESTO AL ÁRBITRO, y es fácil de hacer mal. Si se
    entrena con todo `train` y luego se le piden sus predicciones sobre `train`,
    salen infladas (el modelo acierta el 98 % de lo que ha memorizado). El
    árbitro aprendería que al entrenado hay que creerle casi siempre, que es
    falso en cuanto llega un correo nuevo.

    `cross_val_predict` da, para cada correo, la predicción del modelo
    entrenado en los otros cuatro quintos. Es la única versión comparable con
    lo que pasará en producción.

    qwen3 no tiene este problema: no aprende nada de `train`, así que sus
    predicciones ya son "fuera de partición" por construcción. Esa asimetría es
    justo lo que lo hace útil como control.
    """
    particiones = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    proba = cross_val_predict(
        construir_modelo(), X, y, cv=particiones, method="predict_proba"
    )
    clases = construir_modelo().fit(X, y).named_steps["clasificar"].classes_
    return clases[proba.argmax(axis=1)], proba.max(axis=1)


def ids_de_train() -> list[int]:
    """Los email_id de `train`, en el mismo orden que devuelve `cargar()`."""
    datos = json.loads(DATASET.read_text(encoding="utf-8"))
    return [f["email_id"] for f in datos["train"]]


def cargar_predicciones_llm(modelo: str, prompt: str) -> dict[str, str]:
    """
    La caché de predicciones de qwen3, comprobando que son de ESTE modelo.

    Mezclar predicciones de dos modelos, o de dos versiones de prompt, daría un
    árbitro entrenado sobre un compañero que no existe. No fallaría: daría un
    número. Por eso la caché guarda con qué se hizo y esto se niega a leerla si
    no coincide.
    """
    if not PREDICCIONES_LLM.exists():
        return {}

    datos = json.loads(PREDICCIONES_LLM.read_text(encoding="utf-8"))
    if datos.get("modelo") != modelo or datos.get("prompt") != prompt:
        raise SystemExit(
            f"La caché es de {datos.get('modelo')} / prompt {datos.get('prompt')}, "
            f"y ahora se pide {modelo} / {prompt}.\n"
            f"Mezclarlas daría un árbitro entrenado sobre un compañero que no "
            f"existe. Borra {PREDICCIONES_LLM.name} y vuelve a generarla."
        )
    return datos.get("predicciones", {})


def guardar_predicciones_llm(modelo: str, prompt: str, predicciones: dict) -> None:
    PREDICCIONES_LLM.parent.mkdir(exist_ok=True)
    PREDICCIONES_LLM.write_text(
        json.dumps(
            {"modelo": modelo, "prompt": prompt, "predicciones": predicciones},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# Las categorías en las que se cede a qwen3 cuando discrepa. Lista CERRADA y
# corta a propósito: cada una que se añade es una forma de que el LLM meta un
# error donde el modelo entrenado acertaba.
#
# Solo `seguridad`, que arregla 10 y rompe 0. `tramites` (10-4, p=0,18) está
# fuera hasta que la generación 3 diga algo: añadir un componente que no se
# sostiene solo, apoyado en uno que sí, es cómo se cuela el ruido.
CEDER_A_QWEN3 = frozenset({"seguridad"})


def arbitrar_por_categoria(pred_entrenado, pred_llm, categorias=CEDER_A_QWEN3):
    """
    La regla que funciona: se cede por lo que DICE qwen3, no por lo dudoso que
    sea el correo.

    Nótese que no mira la confianza para nada. Es contraintuitivo —lo natural
    es pensar "si dudo, pregunto"— pero medido: qwen3 acierta menos que el
    entrenado justo donde el entrenado duda. Lo que sabe hacer mejor es una
    cosa concreta, y la hace bien esté el otro seguro o no.
    """
    return np.array(
        [
            llm if (llm in categorias) else pred
            for pred, llm in zip(pred_entrenado, pred_llm)
        ]
    )


def test_pareado(base_ok, nuevo_ok) -> tuple[int, int, float]:
    """
    De los correos que CAMBIAN de resultado, ¿arreglados y rotos se distinguen
    de una moneda? (McNemar exacto.)

    Comparar dos porcentajes globales aquí sería tirar información: los dos
    sistemas ven los MISMOS correos y coinciden en casi todos, así que la
    diferencia vive en un puñado de filas. Un test pareado mira solo esas, y
    por eso detecta un efecto que la comparación de medias se traga: la
    validación anidada daba 80,0 % ± 5,6 —invisible— y esto da p = 0,002.

    Sin scipy: es una binomial exacta de dos colas con p=0,5, cuatro líneas.
    """
    arregla = int((~base_ok & nuevo_ok).sum())
    rompe = int((base_ok & ~nuevo_ok).sum())
    n = arregla + rompe
    if n == 0:
        return arregla, rompe, 1.0

    k = min(arregla, rompe)
    cola = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return arregla, rompe, min(1.0, 2 * cola)


def arbitrar(pred_entrenado, confianza, pred_llm, umbral: float):
    """
    La regla, entera. Si el entrenado va seguro se le cree; si no, cede.

    `pred_llm` puede traer None: un correo para el que no se pidió segunda
    opinión (estaba por encima de UMBRAL_MAXIMO_UTIL) o para el que qwen3
    falló. En los dos casos se queda lo que dijo el entrenado, que es la
    degradación correcta: sin árbitro, el sistema es lo que ya había.
    """
    return np.array(
        [
            llm if (conf < umbral and llm) else pred
            for pred, conf, llm in zip(pred_entrenado, confianza, pred_llm)
        ]
    )


def acierto_con_umbral(pred_ent, conf, llm, y, umbral: float) -> float:
    return float((arbitrar(pred_ent, conf, llm, umbral) == y).mean())


def umbral_por_validacion_anidada(pred_ent, conf, llm, y, rejilla):
    """
    Cuánto vale EL PROCEDIMIENTO, no el mejor umbral de la tabla.

    Barrer umbrales y quedarse con el que más acierta es elegir mirando los
    mismos datos con los que luego se presume: es la Fase 6 otra vez, en
    pequeño. Con un solo parámetro el sobreajuste es leve, pero "leve" no es
    "cero" y aquí ya se pagó por suponerlo.

    Esto parte `train` en cinco, elige el umbral en cuatro quintos y lo aplica
    al quinto que no miró. La media es lo que cabe esperar de repetir el
    procedimiento entero con datos nuevos. Si el umbral elegido baila mucho
    entre particiones, es que la curva es plana y el número exacto no importa;
    si sale siempre parecido, hay señal de verdad.

    Aviso honesto: `conf` y `pred_ent` vienen de una validación cruzada
    anterior con OTRO reparto de particiones, así que queda una filtración
    pequeña entre las dos. Corregirla del todo exigiría anidar también el
    entrenamiento, y con 559 correos costaría más ruido del que quita.
    """
    particiones = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA + 1)
    aciertos, elegidos = [], []

    for dentro, fuera in particiones.split(np.zeros(len(y)), y):
        mejor = max(
            rejilla,
            key=lambda u: acierto_con_umbral(
                pred_ent[dentro], conf[dentro], [llm[i] for i in dentro], y[dentro], u
            ),
        )
        elegidos.append(mejor)
        aciertos.append(
            acierto_con_umbral(
                pred_ent[fuera], conf[fuera], [llm[i] for i in fuera], y[fuera], mejor
            )
        )

    return float(np.mean(aciertos)), float(np.std(aciertos)), elegidos


def main() -> None:
    from mailpilot.classifier import PROMPT_VERSION, OllamaClient

    (X, y), _ = cargar()
    ids = ids_de_train()
    pred_ent, conf = confianzas_fuera_de_particion(X, y)

    cliente = OllamaClient()
    cache = cargar_predicciones_llm(cliente.model, PROMPT_VERSION)
    llm = [cache.get(str(i)) for i in ids]

    con_opinion = [i for i, v in enumerate(llm) if v]
    print(f"train: {len(y)} correos, {len(con_opinion)} con opinión de "
          f"{cliente.model} (prompt {PROMPT_VERSION})")
    if not con_opinion:
        raise SystemExit(
            "No hay predicciones del LLM. Ejecuta: python -m scripts.predecir_llm"
        )

    base_ok = pred_ent == y
    solo_ent = float(base_ok.mean())

    # --- Por qué el umbral no vale -----------------------------------------
    solo = np.array(con_opinion)
    print("\nEN EL CORREO DUDOSO (la hipótesis que se cayó):")
    print(f"  modelo entrenado: {(pred_ent[solo] == y[solo]).mean():.1%}")
    print(f"  {cliente.model:16} {np.mean([llm[i] == y[i] for i in con_opinion]):.1%}")
    print("  qwen3 no es mejor donde el entrenado duda, así que no hay bolsa que ceder.")

    print(f"\nDe referencia, sobre los {len(y)} de train, fuera de partición:")
    print(f"  creer siempre al entrenado:   {solo_ent:.1%}")
    techo = float(np.mean([
        y[i] in {pred_ent[i], llm[i]} if llm[i] else pred_ent[i] == y[i]
        for i in range(len(y))
    ]))
    print(f"  techo de un árbitro perfecto: {techo:.1%}")

    # --- La regla que sí vale ----------------------------------------------
    print("\nCEDER POR CATEGORÍA — cuando qwen3 dice X y el entrenado discrepa:")
    print(f"  {'qwen3 dice':13} {'n':>3} {'arregla':>8} {'rompe':>6} {'p':>8}")
    for cat in sorted({v for v in llm if v}):
        candidata = arbitrar_por_categoria(pred_ent, llm, {cat})
        arregla, rompe, pv = test_pareado(base_ok, candidata == y)
        marca = "  <-- entra" if cat in CEDER_A_QWEN3 else ""
        print(f"  {cat:13} {arregla + rompe:3} {arregla:8} {rompe:6} {pv:8.3f}{marca}")

    arbitro = arbitrar_por_categoria(pred_ent, llm)
    arregla, rompe, pv = test_pareado(base_ok, arbitro == y)
    acierto = float((arbitro == y).mean())

    print(f"\nÁRBITRO ({', '.join(sorted(CEDER_A_QWEN3))}):")
    print(f"  acierto: {acierto:.1%}   ({acierto - solo_ent:+.1%} sobre creer al entrenado)")
    print(f"  arregla {arregla}, rompe {rompe}, p = {pv:.3f}")

    print("\nEsto sirve para ELEGIR la regla, no para presumir del árbitro: las")
    print("categorías se eligieron mirando estos mismos datos. La medición")
    print("honesta necesita el test de la generación 3.")


if __name__ == "__main__":
    main()
