"""
Paso 1 del entrenamiento: sacar el conjunto de la base y CONGELAR la partición.

POR QUÉ SE GUARDA EN UN ARCHIVO EN VEZ DE LEER LA BASE CADA VEZ
---------------------------------------------------------------
La base cambia: cada correo que se etiqueta añade una fila. Si el conjunto se
leyera al vuelo, dos entrenamientos del mismo código darían resultados
distintos y no habría forma de saber si la diferencia la trajo el cambio que
hiciste o los datos nuevos. Un experimento necesita datos quietos.

POR QUÉ LA PARTICIÓN SE CONGELA
-------------------------------
El `test` solo mide mientras no se mire. En cuanto ajustas algo viendo sus
fallos, deja de ser una medición y pasa a ser una expectativa. Este proyecto ya
pagó por aprenderlo: en la Fase 6 el `test` decía 92,5 % y el número honesto
era 73,8 %, porque los prompts se habían escrito mirando sus errores.

Guardar la partición en disco es lo que hace que ese error sea difícil de
cometer sin querer: para cambiarla hay que borrar el archivo a propósito.

QUÉ ETIQUETAS ENTRAN
--------------------
Solo las decididas A CIEGAS. Ver la propuesta del modelo antes de decidir
empuja a darle la razón, así que una etiqueta anclada sirve para afinar pero no
para entrenar ni para medir: enseñaría al modelo nuevo a copiar los sesgos del
viejo, sin forma de saber cuánto.

Uso:
    python scripts/construir_dataset.py            # crea el conjunto
    python scripts/construir_dataset.py --force    # lo regenera desde cero
"""

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from mailpilot.db import SessionLocal
from mailpilot.models import ActionProposal, Email

DESTINO = Path(__file__).resolve().parents[1] / "entrenamiento" / "dataset.json"

# Proporción que se aparta para medir. 20 % de 450 son 90 correos: pocos, pero
# la alternativa es apartar más y quedarse sin con qué entrenar.
PROPORCION_TEST = 0.2

# Semilla fija. Sin ella la partición cambiaría en cada ejecución y el `test`
# de hoy contendría correos que ayer estaban en `train`: el modelo habría visto
# lo que se le pide predecir, y el acierto saldría inflado sin que se note.
SEMILLA = 20260826


def extraer(session) -> list[dict]:
    """
    Las etiquetas utilizables, con el texto que las tiene que predecir.

    El remitente se guarda entero Y troceado en dominio: el dominio es la señal
    más fuerte que hay —`goodreads.com` decide la categoría casi solo— y
    dejarlo enterrado dentro de una cadena obliga al vectorizador a
    redescubrirlo palabra a palabra.
    """
    filas = session.execute(
        select(ActionProposal, Email)
        .join(Email, Email.id == ActionProposal.email_id)
        .where(
            ActionProposal.final_category.is_not(None),
            ActionProposal.decidido_a_ciegas.is_(True),
        )
        .order_by(ActionProposal.id)
    ).all()

    conjunto = []
    for propuesta, correo in filas:
        remitente = correo.sender or ""
        dominio = ""
        if "@" in remitente:
            dominio = remitente.split("@")[-1].strip().rstrip(">").lower()

        conjunto.append(
            {
                "email_id": correo.id,
                "remitente": remitente,
                "dominio": dominio,
                "asunto": correo.subject or "",
                "snippet": correo.snippet or "",
                "categoria": propuesta.final_category.value,
                # Se guarda para poder demostrar después que ninguna etiqueta
                # anclada se coló en el conjunto.
                "a_ciegas": propuesta.decidido_a_ciegas,
            }
        )

    return conjunto


def partir(conjunto: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Parte en train/test ESTRATIFICADO: cada categoría se reparte por separado.

    Con una partición al azar, `social` (15 ejemplos) podría quedarse con cero
    en `test` y entonces no se sabría nada de esa categoría; o con cero en
    `train`, y el modelo no la aprendería en absoluto. Estratificar garantiza
    que las dos mitades se parezcan al conjunto entero.
    """
    azar = random.Random(SEMILLA)
    por_categoria: dict[str, list[dict]] = defaultdict(list)
    for fila in conjunto:
        por_categoria[fila["categoria"]].append(fila)

    train: list[dict] = []
    test: list[dict] = []

    for categoria in sorted(por_categoria):
        filas = por_categoria[categoria][:]
        azar.shuffle(filas)

        # `round` y no `int`: truncando, una categoría de 15 ejemplos daría
        # 3 al test, y redondeando da 3 también, pero una de 14 daría 2 en vez
        # de 3. Con clases pequeñas cada ejemplo del test cuenta.
        cuantos = round(len(filas) * PROPORCION_TEST)
        # Al menos uno en cada lado, siempre que haya de sobra: una categoría
        # sin representación en test es una categoría sobre la que no se puede
        # afirmar nada.
        cuantos = max(1, min(cuantos, len(filas) - 1)) if len(filas) > 1 else 0

        test.extend(filas[:cuantos])
        train.extend(filas[cuantos:])

    azar.shuffle(train)
    azar.shuffle(test)
    return train, test


def resumen(nombre: str, filas: list[dict]) -> None:
    cuenta = Counter(f["categoria"] for f in filas)
    print(f"\n  {nombre} ({len(filas)})")
    for categoria, n in cuenta.most_common():
        print(f"    {categoria:14} {n:3}")


def ampliar_solo_train() -> None:
    """
    Mete las etiquetas nuevas en `train` y NO TOCA `test`.

    POR QUÉ ESTO Y NO REHACER LA PARTICIÓN
    --------------------------------------
    Rehacerla movería correos entre las dos mitades, y entonces el resultado
    nuevo no se podría comparar con el anterior: no se sabría si cambió por
    tener más datos o por haber cambiado el examen.

    Dejando el `test` clavado, la comparación responde una sola pregunta:
    **¿mejora el modelo con más ejemplos de entrenamiento?** Mismo examen,
    mismos 91 correos, más material para estudiar.

    Un correo que ya esté en `test` NUNCA pasa a `train`, aunque se hubiera
    reetiquetado. Eso sería enseñarle al modelo las respuestas del examen.
    """
    if not DESTINO.exists():
        raise SystemExit("No hay conjunto que ampliar. Ejecuta el script sin --ampliar.")

    datos = json.loads(DESTINO.read_text(encoding="utf-8"))
    en_test = {f["email_id"] for f in datos["test"]}
    en_train = {f["email_id"] for f in datos["train"]}
    ya_estan = en_test | en_train

    with SessionLocal() as session:
        todas = extraer(session)

    nuevas = [f for f in todas if f["email_id"] not in ya_estan]

    # La red de seguridad: si por lo que sea una fila de test se colara como
    # "nueva", el modelo entrenaría con las respuestas del examen.
    coladas = [f for f in nuevas if f["email_id"] in en_test]
    assert not coladas, f"{len(coladas)} correos de test intentaron entrar en train"

    if not nuevas:
        print("No hay etiquetas nuevas desde la última vez.")
        return

    antes = len(datos["train"])
    datos["train"].extend(nuevas)
    datos["total"] = len(datos["train"]) + len(datos["test"])
    datos["ampliaciones"] = datos.get("ampliaciones", []) + [
        {"añadidas": len(nuevas), "train_resultante": len(datos["train"])}
    ]

    DESTINO.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"train: {antes} -> {len(datos['train'])}  (+{len(nuevas)})")
    print(f"test:  {len(datos['test'])}  SIN TOCAR")
    resumen("train (ampliado)", datos["train"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ampliar",
        action="store_true",
        help="añade las etiquetas nuevas SOLO a train, dejando el test intacto",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenera la partición aunque ya exista (INVALIDA las medidas anteriores)",
    )
    args = parser.parse_args()

    if args.ampliar:
        ampliar_solo_train()
        return

    if DESTINO.exists() and not args.force:
        print(f"Ya existe {DESTINO.relative_to(DESTINO.parents[1])}.")
        print("La partición está congelada a propósito: rehacerla movería correos")
        print("de `train` a `test` y las medidas anteriores dejarían de valer.")
        print("Si de verdad quieres rehacerla: --force")
        return

    with SessionLocal() as session:
        conjunto = extraer(session)

    if not conjunto:
        print("No hay ninguna etiqueta decidida a ciegas. Nada que hacer.")
        return

    assert all(f["a_ciegas"] for f in conjunto), "se coló una etiqueta anclada"

    train, test = partir(conjunto)

    DESTINO.parent.mkdir(exist_ok=True)
    DESTINO.write_text(
        json.dumps(
            {
                "semilla": SEMILLA,
                "proporcion_test": PROPORCION_TEST,
                "solo_a_ciegas": True,
                "total": len(conjunto),
                "train": train,
                "test": test,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Conjunto guardado en {DESTINO}")
    print(f"Total: {len(conjunto)} etiquetas, todas decididas a ciegas.")
    resumen("train", train)
    resumen("test", test)

    print("\n  A partir de aquí, el `test` NO se mira hasta el paso 6.")


if __name__ == "__main__":
    main()
