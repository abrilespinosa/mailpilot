"""
Mide la calidad del clasificador contra el conjunto etiquetado a mano.

Reclasifica los correos de evaluation/labels.json y compara con la etiqueta
correcta. NO guarda nada en la tabla classifications: son experimentos, y
mezclarlos con las clasificaciones reales ensuciaría el histórico.

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
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select  # noqa: F401  (usado por SessionLocal más abajo)

from mailpilot.classifier import OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData
from mailpilot.models import Email

RAIZ = Path(__file__).resolve().parents[1] / "evaluation"
LABELS = RAIZ / "labels.json"
RUNS = RAIZ / "runs"
CATEGORIAS = [
    "personal",
    "trabajo",
    "compras",
    "banco",
    "avisos",
    "promociones",
    "otros",
]


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
    filas = [c for c in CATEGORIAS if c in esperadas]

    print("\n  correcta \\ predicha")
    print(" " * 15 + "".join(f"{c[:6]:>8}" for c in columnas))
    for correcta in filas:
        celdas = "".join(f"{conteo[correcta][p] or '·':>8}" for p in columnas)
        print(f"  {correcta:13}{celdas}")


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

    fallos = [r for r in resultados if not r["correct"]]
    if fallos:
        print(f"\n  Fallos ({len(fallos)}):")
        for r in fallos:
            print(f"    {r['expected']:12} -> {r['predicted']:12} {r['subject'][:44]}")

    return precision


def cargar_etiquetas() -> list[dict]:
    return json.loads(LABELS.read_text())


def guardar(nombre: str, modelo: str, resultados: list[dict], precision: float) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    destino = RUNS / f"{nombre}.json"
    destino.write_text(
        json.dumps(
            {
                "name": nombre,
                "model": modelo,
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

    esperado_por_id = {e["email_id"]: e["expected"] for e in cargar_etiquetas()}

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


def ejecutar(nombre: str, limite: int | None) -> None:
    etiquetas = cargar_etiquetas()
    if limite:
        etiquetas = etiquetas[:limite]

    client = OllamaClient()
    print(f"Ejecución: {nombre}")
    print(f"Modelo:    {client.model}")
    print(f"Correos:   {len(etiquetas)}\n")

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
    parser.add_argument(
        "--rescore", help="repuntuar una ejecución guardada con las etiquetas actuales"
    )
    args = parser.parse_args()

    if args.rescore:
        rescore(args.rescore)
    elif args.name:
        ejecutar(args.name, args.limit)
    else:
        parser.error("hace falta --name o --rescore")


if __name__ == "__main__":
    main()
