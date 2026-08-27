"""
Pasos 2 a 5: baseline, vectorizar, entrenar y evaluar.

CÓMO ESTÁ ORDENADO Y POR QUÉ
----------------------------
El `test` se toca UNA SOLA VEZ, al final. Todo lo que sirve para decidir algo
—si el modelo va bien, si sobreajusta, si un ajuste ayuda— sale de validación
cruzada sobre `train`, que no gasta el conjunto de prueba.

Es la disciplina que le faltó a la Fase 6: allí el `test` decía 92,5 % y el
número honesto era 73,8 %, porque se habían escrito prompts mirando sus fallos.
Con un modelo que entrena en menos de un segundo el error es más fácil todavía.

QUÉ SE MIDE CONTRA QUÉ
----------------------
Contra dos referencias, y hacen falta las dos:

    baseline tonto   contestar siempre la categoría más frecuente
    qwen3:8b         ~82 %, lo que ya funciona hoy

Sin el suelo, un 60 % no dice nada. Sin el listón, un 70 % parece un éxito
cuando sería un retroceso.

Uso:
    python scripts/entrenar.py
    python scripts/entrenar.py --guardar   # deja el modelo en disco
"""

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline

RAIZ = Path(__file__).resolve().parents[1]
DATASET = RAIZ / "entrenamiento" / "dataset.json"
MODELO = RAIZ / "entrenamiento" / "modelo.joblib"
CUERPOS = RAIZ / "entrenamiento" / "cuerpos.json"

# Columnas del array que se le pasa al ColumnTransformer.
DOMINIO, REMITENTE, TEXTO, CUERPO = 0, 1, 2, 3


def cargar():
    if not DATASET.exists():
        raise SystemExit("Falta el conjunto. Ejecuta: python scripts/construir_dataset.py")

    datos = json.loads(DATASET.read_text(encoding="utf-8"))

    # El cuerpo es OPCIONAL: si no se ha traído, la columna va vacía y el
    # modelo funciona igual que antes. Así el experimento se puede encender y
    # apagar borrando un archivo, sin tocar código.
    cuerpos = {}
    if CUERPOS.exists():
        cuerpos = json.loads(CUERPOS.read_text(encoding="utf-8"))

    def a_matriz(filas):
        # El asunto y el snippet se juntan: son el mismo tipo de señal (texto
        # que escribió el remitente) y separarlos solo multiplicaría columnas
        # con 359 ejemplos, que es justo lo que no conviene.
        X = np.array(
            [
                [
                    f["dominio"],
                    f["remitente"],
                    f"{f['asunto']} {f['snippet']}",
                    cuerpos.get(str(f["email_id"]), ""),
                ]
                for f in filas
            ],
            dtype=object,
        )
        y = np.array([f["categoria"] for f in filas])
        return X, y

    return a_matriz(datos["train"]), a_matriz(datos["test"])


def construir_modelo() -> Pipeline:
    """
    TF-IDF sobre tres campos + regresión logística.

    EL DOMINIO VA APARTE, y es la decisión que más puede pesar. `goodreads.com`
    decide la categoría casi solo, pero dentro de la cadena del remitente queda
    troceado en palabras sueltas y el vectorizador tiene que redescubrirlo. Con
    `analyzer="char_wb"` sobre el dominio se capturan además parecidos entre
    subdominios del mismo servicio.

    `class_weight="balanced"` compensa que `seguridad` tenga 77 ejemplos y
    `social` 12: sin eso, el modelo aprende que contestar `seguridad` sale
    barato y las categorías pequeñas desaparecen.
    """
    vectorizador = ColumnTransformer(
        [
            (
                "dominio",
                TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1),
                DOMINIO,
            ),
            (
                "remitente",
                TfidfVectorizer(lowercase=True, min_df=1, ngram_range=(1, 2)),
                REMITENTE,
            ),
            (
                "texto",
                # min_df=2: una palabra que aparece en un solo correo de 359 no
                # puede generalizar, solo memorizar ese correo.
                TfidfVectorizer(lowercase=True, min_df=2, ngram_range=(1, 2)),
                TEXTO,
            ),
            (
                # EL CUERPO. Es lo único que separa "confirma tu cuenta" de
                # "bienvenido": por fuera son el mismo correo, por dentro uno
                # lleva un enlace de verificación y el otro consejos de uso.
                #
                # min_df=3 y max_df=0.5, más estrictos que en el asunto: el
                # cuerpo trae mucha morralla repetida (pies de página, avisos
                # legales) y mucha palabra única. Sin filtrar, esas dos cosas
                # ahogarían la señal.
                "cuerpo",
                TfidfVectorizer(
                    lowercase=True, min_df=3, max_df=0.5, ngram_range=(1, 2)
                ),
                CUERPO,
            ),
        ]
    )

    return Pipeline(
        [
            ("vectorizar", vectorizador),
            (
                "clasificar",
                LogisticRegression(
                    max_iter=2000,
                    class_weight="balanced",
                    random_state=20260826,
                ),
            ),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guardar", action="store_true", help="guarda el modelo entrenado")
    args = parser.parse_args()

    (X_train, y_train), (X_test, y_test) = cargar()
    print(f"train: {len(y_train)}   test: {len(y_test)}\n")

    # --- PASO 2: el baseline tonto -----------------------------------------
    tonto = DummyClassifier(strategy="most_frequent").fit(X_train, y_train)
    base = accuracy_score(y_test, tonto.predict(X_test))
    print("PASO 2 — baseline")
    print(f"  contestar siempre '{tonto.classes_[tonto.class_prior_.argmax()]}': {base:.1%}")

    # --- PASOS 3 y 4: vectorizar y entrenar --------------------------------
    modelo = construir_modelo()

    # Validación cruzada SOBRE TRAIN. Es la estimación honesta que se puede
    # mirar tantas veces como haga falta sin gastar el `test`.
    particiones = StratifiedKFold(n_splits=5, shuffle=True, random_state=20260826)
    cv = cross_val_score(modelo, X_train, y_train, cv=particiones, scoring="accuracy")
    print("\nPASOS 3-4 — entrenamiento")
    print(f"  validación cruzada (5 partes, solo train): {cv.mean():.1%} ± {cv.std():.1%}")

    modelo.fit(X_train, y_train)
    en_train = accuracy_score(y_train, modelo.predict(X_train))
    print(f"  acierto sobre el propio train:             {en_train:.1%}")

    # --- PASO 5: el test, una sola vez -------------------------------------
    print("\nPASO 5 — test (se mira UNA vez)")
    predicho = modelo.predict(X_test)
    en_test = accuracy_score(y_test, predicho)
    print(f"  acierto:  {en_test:.1%}   (baseline {base:.1%})")
    print(f"  distancia train-test: {en_train - en_test:.1%}  <- sobreajuste")

    print("\n  Por categoría:")
    print(
        classification_report(
            y_test, predicho, zero_division=0, digits=2, target_names=None
        )
    )

    etiquetas = sorted(set(y_test) | set(predicho))
    matriz = confusion_matrix(y_test, predicho, labels=etiquetas)
    ancho = max(len(e) for e in etiquetas)
    print("  Matriz de confusión (filas = verdad, columnas = predicción):")
    print(" " * (ancho + 4) + " ".join(f"{e[:4]:>4}" for e in etiquetas))
    for nombre, fila in zip(etiquetas, matriz):
        print(f"    {nombre:>{ancho}} " + " ".join(f"{n:>4}" for n in fila))

    if args.guardar:
        import joblib

        joblib.dump(modelo, MODELO)
        print(f"\nModelo guardado en {MODELO}")


if __name__ == "__main__":
    main()
