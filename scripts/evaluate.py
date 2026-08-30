"""
Mide la calidad del clasificador contra el conjunto etiquetado a mano.

QUÉ MIDE ESTO Y QUÉ NO, A DÍA DE HOY
------------------------------------
Lee `evaluation/labels.json`, que está congelado en la taxonomía de SIETE
categorías anterior al ADR 006 (240 correos: dev, test y test2). Sus números
NO son comparables con nada medido después del 2026-08-17.

Las mediciones vigentes de las diez categorías no salen de aquí:

    modelo entrenado contra qwen3   ->  python -m scripts.comparar
    partición congelada             ->  entrenamiento/dataset.json
    etiquetas de uso real           ->  tabla action_proposals

Este script sigue en pie porque `--rescore` es la única forma de releer las
ejecuciones guardadas en evaluation/runs/, y porque qwen3 es el CONTROL del
experimento: un modelo que no aprende, midiendo al lado del que sí, es lo
único que distingue "mi modelo ha empeorado" de "el examen es más difícil".
Si algún día labels.json se migra a las diez, esto vuelve a servir tal cual.

Reclasifica los correos de labels.json y compara con la etiqueta correcta. NO
guarda nada en la tabla classifications: son experimentos, y mezclarlos con
las clasificaciones reales ensuciaría el histórico.

Guarda cada ejecución en evaluation/runs/<nombre>.json para poder comparar
un prompt o un modelo con otro.

    python scripts/evaluate.py --name baseline
    python scripts/evaluate.py --name prompt-v2 --limit 20

Si corriges etiquetas después de una ejecución, no hace falta repetirla: las
predicciones ya están guardadas y se pueden volver a puntuar.

    python scripts/evaluate.py --rescore baseline
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select  # noqa: F401  (usado por SessionLocal más abajo)

from mailpilot.classifier import PROMPT_VERSION, OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData
from mailpilot.models import Category, Email

RAIZ = Path(__file__).resolve().parents[1] / "evaluation"
LABELS = RAIZ / "labels.json"
RUNS = RAIZ / "runs"

# DERIVADA DEL ENUM, no escrita a mano. Estuvo escrita a mano con las siete
# categorías viejas y se quedó atrás cuando el ADR 006 las subió a diez: como
# `matriz_de_confusion` filtraba las filas por esta lista, una fila de
# `seguridad`, `boletines`, `social` o `empleo` desaparecía de la matriz sin
# decir nada, mientras el porcentaje de acierto sí las contaba. Un informe al
# que le faltan cuatro categorías y no lo dice es peor que no tener informe.
CATEGORIAS = [c.value for c in Category]


def to_email_data(email: Email) -> EmailData:
    return EmailData(
        gmail_message_id=email.gmail_message_id,
        gmail_thread_id=email.gmail_thread_id,
        subject=email.subject,
        sender=email.sender,
        snippet=email.snippet,
        received_at=email.received_at,
        raw_labels=email.raw_labels,
    )


def matriz_de_confusion(resultados: list[dict]) -> None:
    """
    Filas = categoría correcta, columnas = lo que dijo el modelo.

    La diagonal son los aciertos. Lo de fuera enseña QUÉ confunde con qué, que
    es mucho más útil que un porcentaje suelto para saber dónde tocar.
    """
    conteo = defaultdict(Counter)
    for r in resultados:
        conteo[r["expected"]][r["predicted"]] += 1

    esperadas = {r["expected"] for r in resultados}
    predichas = {r["predicted"] for r in resultados}
    columnas = [c for c in CATEGORIAS if c in predichas] + [
        c for c in sorted(predichas) if c not in CATEGORIAS
    ]
    # Las desconocidas se añaden al final en vez de descartarse. Antes se
    # filtraba y punto, así que una etiqueta de una taxonomía que ya no existe
    # (o de una que todavía no) se caía del informe en silencio.
    filas = [c for c in CATEGORIAS if c in esperadas] + [
        c for c in sorted(esperadas) if c not in CATEGORIAS
    ]

    print("\n  correcta \\ predicha")
    print(" " * 15 + "".join(f"{c[:6]:>8}" for c in columnas))
    for correcta in filas:
        celdas = "".join(f"{conteo[correcta][p] or '·':>8}" for p in columnas)
        print(f"  {correcta:13}{celdas}")


def por_categoria(resultados: list[dict]) -> None:
    """
    Precisión Y recall por categoría. Hasta ahora solo se veía el recall.

    Son dos preguntas distintas y la diferencia importa más de lo que parece:

        recall     de los que SON `otros`, ¿cuántos pilló?
        precisión  de los que LLAMÓ `otros`, ¿cuántos acertó?

    Un modelo que contesta `otros` a todo tiene recall perfecto en `otros` y
    precisión pésima. La matriz de confusión responde la primera leyendo por
    filas, pero la segunda hay que sacarla por columnas y nadie lo hace de
    cabeza. Este proyecto ya se topó con las dos versiones del mismo número
    dando lecturas opuestas de `otros` (ver el análisis de las 72 correcciones
    reales): la Fase 6 medía recall, las correcciones medían precisión, y
    parecían contradecirse sin hacerlo.

    `son` a cero significa que el conjunto no dice NADA de esa categoría, que
    es distinto de que el modelo lo haga mal.
    """
    son = Counter(r["expected"] for r in resultados)
    dijo = Counter(r["predicted"] for r in resultados)
    acerto = Counter(r["expected"] for r in resultados if r["correct"])

    # Primero las del enum, en su orden; después cualquier categoría ajena que
    # haya aparecido (una taxonomía vieja en el conjunto, un "ERROR" del
    # modelo). Ninguna se descarta: si sale en el acierto global, sale aquí.
    aparecidas = set(son) | set(dijo)
    vistas = [c for c in CATEGORIAS if c in aparecidas]
    vistas += sorted(aparecidas - set(CATEGORIAS))

    def pct(numerador: int, denominador: int) -> str:
        return f"{numerador / denominador:.1%}" if denominador else "·"

    print(f"\n  {'categoría':13} {'son':>4} {'recall':>9}   {'dijo':>4} {'precisión':>9}")
    for c in vistas:
        print(
            f"  {c:13} {son[c]:>4} {pct(acerto[c], son[c]):>9}   "
            f"{dijo[c]:>4} {pct(acerto[c], dijo[c]):>9}"
        )

    flojas = [c for c in vistas if 0 < son[c] < 5]
    if flojas:
        print(f"\n  (con menos de 5 ejemplos no se puede afirmar nada de: "
              f"{', '.join(flojas)})")


def avisar_si_taxonomia_vieja(etiquetas: list[dict]) -> None:
    """
    Grita si el conjunto está escrito en una taxonomía que ya no existe.

    `evaluation/labels.json` se construyó con las SIETE categorías de antes del
    ADR 006 y nunca se migró. Sus números no son comparables con nada medido
    después: `trabajo` se llamó `empleo` y se estrechó, y `seguridad`,
    `boletines` y `social` salieron de partir `avisos` y `otros`.

    Sin este aviso el script imprime un porcentaje perfectamente creíble sobre
    un examen de otra asignatura, que es la peor forma de equivocarse.
    """
    actuales = {c.value for c in Category}
    fantasmas = sorted({e["expected"] for e in etiquetas} - actuales)
    if not fantasmas:
        return

    print("  " + "!" * 60)
    print(f"  AVISO: este conjunto usa categorías que ya no existen: "
          f"{', '.join(fantasmas)}.")
    print("  Es la taxonomía de SIETE, anterior al ADR 006. El acierto que salga")
    print("  NO es comparable con ninguna medición posterior al 2026-08-17.")
    print("  Para medir con las diez de hoy: python -m scripts.comparar")
    print("  " + "!" * 60)


def informe(resultados: list[dict], fallos_validacion: int = 0) -> float:
    aciertos = sum(1 for r in resultados if r["correct"])
    total = len(resultados)
    precision = aciertos / total if total else 0.0

    print(f"\n{'=' * 62}")
    print(f"  ACIERTO: {aciertos}/{total} = {precision:.1%}")
    if fallos_validacion:
        print(f"  fallos de validación: {fallos_validacion}")

    tiempos = [r["seconds"] for r in resultados if r.get("seconds")]
    if tiempos:
        print(f"  tiempo medio: {sum(tiempos) / len(tiempos):.1f}s por correo")

    # Calibración: si la confianza sirviera de algo, los aciertos deberían
    # tener confianza más alta que los fallos. Si son iguales, no informa.
    conf_ok = [r["confidence"] for r in resultados if r["correct"] and r["confidence"]]
    conf_mal = [
        r["confidence"] for r in resultados if not r["correct"] and r["confidence"]
    ]
    if conf_ok and conf_mal:
        media_ok = sum(conf_ok) / len(conf_ok)
        media_mal = sum(conf_mal) / len(conf_mal)
        print(f"\n  confianza media en aciertos: {media_ok:.3f}")
        print(f"  confianza media en fallos:   {media_mal:.3f}")
        print(f"  diferencia:                  {media_ok - media_mal:+.3f}")
        print("  (cerca de 0 = la confianza no sirve como umbral)")

    matriz_de_confusion(resultados)
    por_categoria(resultados)

    fallos = [r for r in resultados if not r["correct"]]
    if fallos:
        print(f"\n  Fallos ({len(fallos)}):")
        for r in fallos:
            print(f"    {r['expected']:12} -> {r['predicted']:12} {r['subject'][:44]}")

    return precision


def splits_disponibles() -> list[str]:
    """Los conjuntos que existen de verdad en labels.json, no una lista escrita a mano."""
    return sorted({e["split"] for e in json.loads(LABELS.read_text()) if e.get("split")})


def cargar_etiquetas(split: str | None = None) -> list[dict]:
    etiquetas = json.loads(LABELS.read_text())
    if split:
        etiquetas = [e for e in etiquetas if e.get("split") == split]
        # Sin esto, un conjunto inexistente devolvía una lista vacía y el script
        # seguía tan tranquilo hasta imprimir un acierto sobre cero correos. Un
        # conjunto que no existe es un error de quien llama, no un resultado.
        if not etiquetas:
            disponibles = ", ".join(splits_disponibles()) or "ninguno"
            raise SystemExit(
                f"El conjunto '{split}' no existe en {LABELS.name}.\n"
                f"Disponibles: {disponibles}.\n"
                "Ojo: `test3` y `test6` NO están aquí. Se midieron por el dashboard, "
                "y su fuente de verdad es la tabla action_proposals (ver CLAUDE.md)."
            )
    return etiquetas


def guardar(nombre: str, modelo: str, resultados: list[dict], precision: float) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    destino = RUNS / f"{nombre}.json"
    destino.write_text(
        json.dumps(
            {
                "name": nombre,
                "model": modelo,
                "prompt_version": PROMPT_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "accuracy": precision,
                "correct": sum(1 for r in resultados if r["correct"]),
                "total": len(resultados),
                "results": resultados,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return destino


def rescore(nombre: str) -> None:
    """
    Vuelve a puntuar una ejecución guardada con las etiquetas actuales.

    Las predicciones del modelo no cambian: lo que cambia es contra qué se
    comparan. Evita repetir 15 minutos de inferencia cada vez que se corrige
    una etiqueta.
    """
    origen = RUNS / f"{nombre}.json"
    datos = json.loads(origen.read_text())

    etiquetas = cargar_etiquetas()
    avisar_si_taxonomia_vieja(etiquetas)
    esperado_por_id = {e["email_id"]: e["expected"] for e in etiquetas}

    cambiados = 0
    for r in datos["results"]:
        nuevo = esperado_por_id.get(r["email_id"])
        if nuevo and nuevo != r["expected"]:
            r["expected"] = nuevo
            cambiados += 1
        r["correct"] = r["predicted"] == r["expected"]

    print(f"Repuntuando '{nombre}' ({datos['model']})")
    print(f"Etiquetas cambiadas desde la ejecución: {cambiados}")

    antes = datos["accuracy"]
    precision = informe(datos["results"])
    print(f"\n  antes de corregir etiquetas: {antes:.1%}")

    guardar(nombre, datos["model"], datos["results"], precision)
    print(f"  actualizado {origen}")


def ejecutar(nombre: str, limite: int | None, split: str | None, modelo: str | None) -> None:
    etiquetas = cargar_etiquetas(split)
    if limite:
        etiquetas = etiquetas[:limite]

    # Antes de gastar quince minutos de inferencia, no después.
    avisar_si_taxonomia_vieja(etiquetas)

    client = OllamaClient(model=modelo) if modelo else OllamaClient()
    print(f"Ejecución: {nombre}")
    print(f"Modelo:    {client.model}")
    print(f"Prompt:    {PROMPT_VERSION}")
    print(f"Conjunto:  {split or 'todos'} ({len(etiquetas)} correos)\n")

    resultados = []
    fallos_validacion = 0

    with SessionLocal() as session:
        for n, etiqueta in enumerate(etiquetas, start=1):
            email = session.get(Email, etiqueta["email_id"])
            if email is None:
                continue

            inicio = time.perf_counter()
            try:
                salida = classify_email(client, to_email_data(email))
                predicha = salida.category.value
                confianza = salida.confidence
            except Exception as error:
                fallos_validacion += 1
                predicha = "ERROR"
                confianza = None
                print(f"  [{n:3}] FALLO: {type(error).__name__}")
            segundos = time.perf_counter() - inicio

            acierto = predicha == etiqueta["expected"]
            resultados.append(
                {
                    "email_id": etiqueta["email_id"],
                    "subject": etiqueta["subject"],
                    "expected": etiqueta["expected"],
                    "predicted": predicha,
                    "confidence": confianza,
                    "correct": acierto,
                    "seconds": round(segundos, 2),
                }
            )

            marca = "ok " if acierto else "MAL"
            print(
                f"  [{n:3}] {marca} {etiqueta['expected']:12} -> {predicha:12} "
                f"{segundos:5.1f}s  {etiqueta['subject'][:38]}",
                flush=True,
            )

    precision = informe(resultados, fallos_validacion)
    destino = guardar(nombre, client.model, resultados, precision)
    print(f"\n  guardado en {destino}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="nombre de esta ejecución")
    parser.add_argument("--limit", type=int, help="usar solo los N primeros")
    # Sin `choices=`: la lista vivía escrita a mano y se quedó atrás en cuanto
    # aparecieron conjuntos nuevos. Ahora los conjuntos válidos son los que haya
    # en labels.json, y `cargar_etiquetas` avisa con la lista real si no está.
    # Tampoco puede ir en `choices` porque labels.json está gitignored: quien
    # clone el repo no lo tiene, y argparse lo leería solo con pedir --help.
    parser.add_argument(
        "--split",
        help="dev para afinar; test y test2 quemados (se escribieron prompts viéndolos)",
    )
    parser.add_argument("--model", help="modelo de Ollama (por defecto, el de .env)")
    parser.add_argument(
        "--rescore", help="repuntuar una ejecución guardada con las etiquetas actuales"
    )
    args = parser.parse_args()

    if args.rescore:
        rescore(args.rescore)
    elif args.name:
        ejecutar(args.name, args.limit, args.split, args.model)
    else:
        parser.error("hace falta --name o --rescore")


if __name__ == "__main__":
    main()
