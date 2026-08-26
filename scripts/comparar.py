"""
Paso 6: el modelo entrenado contra qwen3, sobre el MISMO conjunto de prueba.

Es el único paso que responde a la pregunta que motivó todo esto. Las dos
condiciones que lo hacen justo:

  - los mismos 91 correos, que ninguno de los dos ha visto al aprender
  - las mismas etiquetas, todas decididas a ciegas

El 82 % que se venía citando NO valía para comparar: se midió sobre otros
conjuntos y con la taxonomía anterior. Un número medido en otro sitio no es una
referencia, es una anécdota.

Uso:
    python scripts/comparar.py
"""

import json
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, classification_report

from mailpilot.classifier import PROMPT_VERSION, OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData
from mailpilot.models import Email

RAIZ = Path(__file__).resolve().parents[1]
DATASET = RAIZ / "entrenamiento" / "dataset.json"
RESULTADO = RAIZ / "entrenamiento" / "comparacion.json"


def main() -> None:
    from scripts.entrenar import cargar, construir_modelo

    (X_train, y_train), (X_test, y_test) = cargar()
    datos = json.loads(DATASET.read_text(encoding="utf-8"))

    # --- el entrenado ------------------------------------------------------
    modelo = construir_modelo().fit(X_train, y_train)
    pred_clasico = modelo.predict(X_test)
    acierto_clasico = accuracy_score(y_test, pred_clasico)
    print(f"Modelo entrenado: {acierto_clasico:.1%}")

    # --- qwen3 -------------------------------------------------------------
    cliente = OllamaClient()
    print(f"\nClasificando los mismos {len(y_test)} correos con {cliente.model} "
          f"(prompt {PROMPT_VERSION})…")

    pred_llm: list[str] = []
    tiempos: list[float] = []

    with SessionLocal() as session:
        for numero, fila in enumerate(datos["test"], start=1):
            correo = session.get(Email, fila["email_id"])
            datos_correo = EmailData(
                gmail_message_id=correo.gmail_message_id,
                gmail_thread_id=correo.gmail_thread_id,
                subject=correo.subject,
                sender=correo.sender,
                snippet=correo.snippet,
                received_at=correo.received_at,
                raw_labels=correo.raw_labels,
            )

            inicio = time.perf_counter()
            try:
                resultado = classify_email(cliente, datos_correo)
                pred_llm.append(resultado.category.value)
            except Exception:
                # Un fallo del modelo cuenta como fallo, no se descarta: si se
                # tirasen los correos que no digiere, saldría favorecido.
                pred_llm.append("(fallo)")
            tiempos.append(time.perf_counter() - inicio)

            if numero % 20 == 0:
                print(f"  {numero}/{len(y_test)}…")

    pred_llm_arr = np.array(pred_llm)
    acierto_llm = accuracy_score(y_test, pred_llm_arr)

    # --- el veredicto ------------------------------------------------------
    print("\n" + "=" * 58)
    print(f"  modelo entrenado   {acierto_clasico:6.1%}   "
          f"~0.001 s por correo")
    print(f"  {cliente.model:18} {acierto_llm:6.1%}   "
          f"{sum(tiempos)/len(tiempos):.1f} s por correo")
    print("=" * 58)

    print("\nPor categoría (F1):")
    inf_c = classification_report(y_test, pred_clasico, zero_division=0, output_dict=True)
    inf_l = classification_report(y_test, pred_llm_arr, zero_division=0, output_dict=True)

    print(f"  {'categoría':14} {'entrenado':>10} {'qwen3':>8}   gana")
    for categoria in sorted(set(y_test)):
        f1c = inf_c.get(categoria, {}).get("f1-score", 0.0)
        f1l = inf_l.get(categoria, {}).get("f1-score", 0.0)
        if abs(f1c - f1l) < 0.05:
            gana = "empate"
        else:
            gana = "entrenado" if f1c > f1l else "qwen3"
        print(f"  {categoria:14} {f1c:>10.2f} {f1l:>8.2f}   {gana}")

    # --- dónde se equivocan A LA VEZ ---------------------------------------
    ambos_mal = sum(
        1 for v, c, l in zip(y_test, pred_clasico, pred_llm_arr) if v != c and v != l
    )
    solo_clasico = sum(
        1 for v, c, l in zip(y_test, pred_clasico, pred_llm_arr) if v != c and v == l
    )
    solo_llm = sum(
        1 for v, c, l in zip(y_test, pred_clasico, pred_llm_arr) if v == c and v != l
    )

    print(f"\n  fallan los dos:        {ambos_mal}")
    print(f"  solo falla el entrenado: {solo_clasico}")
    print(f"  solo falla qwen3:        {solo_llm}")
    print(
        "\n  Los que solo falla uno son los que justificarían combinarlos:\n"
        "  si fueran casi cero, los dos se equivocan en lo mismo y no hay nada que ganar."
    )

    RESULTADO.write_text(
        json.dumps(
            {
                "acierto_entrenado": acierto_clasico,
                "acierto_llm": acierto_llm,
                "modelo_llm": cliente.model,
                "prompt": PROMPT_VERSION,
                "n": len(y_test),
                "verdad": list(y_test),
                "entrenado": list(pred_clasico),
                "llm": list(pred_llm),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nGuardado en {RESULTADO}")


if __name__ == "__main__":
    main()
