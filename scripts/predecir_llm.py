"""
Pide a qwen3 su opinión sobre los correos de `train` donde el entrenado duda.

POR QUÉ SOLO LOS DUDOSOS
------------------------
`train` son 559 correos y qwen3 tarda ~6,3 s con cada uno: casi una hora. Pero
por encima de 0,60 de confianza el modelo entrenado acierta el 87,7 % y
subiendo, así que ningún árbitro le va a llevar la contraria ahí. Pedir esa
segunda opinión sería pagarla y tirarla.

Cortando en 0,60 quedan ~264 correos: media hora en vez de una.

POR QUÉ SE GUARDA EN DISCO Y ES REANUDABLE
------------------------------------------
Media hora es tiempo de sobra para que se caiga Ollama, se cierre el portátil o
se vaya la luz. Cada correo se escribe en cuanto se resuelve, así que volver a
lanzarlo sigue donde lo dejó en vez de empezar de cero.

La caché vale además para no repetir la inferencia cada vez que se toque el
árbitro, igual que `cuerpos.json` evita repetir 559 llamadas a Gmail. Es un
archivo de trabajo borrable, y está gitignored: contiene email_id de correo
real.

QUÉ NO HACE
-----------
No toca el `test`. Las predicciones de qwen3 sobre el test de la generación 2
ya están en `comparacion.json` y ese conjunto está gastado.

    python -m scripts.predecir_llm              # los de confianza < 0,60
    python -m scripts.predecir_llm --umbral 0.5 # menos correos, más rápido
    python -m scripts.predecir_llm --todos      # los 559, ~1 h
"""

import argparse
import time

from mailpilot.classifier import PROMPT_VERSION, OllamaClient, classify_email
from mailpilot.db import SessionLocal
from mailpilot.gmail import EmailData
from mailpilot.models import Email

from scripts.arbitro import (
    UMBRAL_MAXIMO_UTIL,
    cargar_predicciones_llm,
    confianzas_fuera_de_particion,
    guardar_predicciones_llm,
    ids_de_train,
)
from scripts.entrenar import cargar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--umbral",
        type=float,
        default=UMBRAL_MAXIMO_UTIL,
        help=f"pedir opinión solo por debajo de esta confianza (defecto {UMBRAL_MAXIMO_UTIL})",
    )
    parser.add_argument(
        "--todos", action="store_true", help="los 559 de train, sin filtrar (~1 h)"
    )
    args = parser.parse_args()

    (X, y), _ = cargar()
    ids = ids_de_train()
    assert len(ids) == len(y), "dataset.json y cargar() no coinciden en orden"

    print(f"train: {len(y)} correos. Calculando confianzas fuera de partición…")
    _, confianza = confianzas_fuera_de_particion(X, y)

    cliente = OllamaClient()
    ya = cargar_predicciones_llm(cliente.model, PROMPT_VERSION)

    if args.todos:
        quiero = list(range(len(ids)))
    else:
        quiero = [i for i in range(len(ids)) if confianza[i] < args.umbral]

    faltan = [i for i in quiero if str(ids[i]) not in ya]

    print(f"\nmodelo {cliente.model}, prompt {PROMPT_VERSION}")
    print(f"  candidatos (confianza < {args.umbral}): {len(quiero)}")
    print(f"  ya en caché:                            {len(quiero) - len(faltan)}")
    print(f"  por preguntar:                          {len(faltan)}")
    if not faltan:
        print("\nNada que hacer.")
        return
    print(f"  estimado:                               ~{len(faltan) * 6.3 / 60:.0f} min\n")

    inicio = time.perf_counter()
    with SessionLocal() as session:
        for hechos, i in enumerate(faltan, start=1):
            correo = session.get(Email, ids[i])
            if correo is None:
                continue

            try:
                salida = classify_email(
                    cliente,
                    EmailData(
                        gmail_message_id=correo.gmail_message_id,
                        gmail_thread_id=correo.gmail_thread_id,
                        subject=correo.subject,
                        sender=correo.sender,
                        snippet=correo.snippet,
                        received_at=correo.received_at,
                        raw_labels=correo.raw_labels,
                    ),
                )
                ya[str(ids[i])] = salida.category.value
            except Exception as error:
                # Un fallo se GUARDA como fallo, no se descarta. Descartar los
                # correos que el modelo no digiere lo dejaría mejor de lo que
                # es, y el árbitro necesita saber que ahí no hay ayuda.
                ya[str(ids[i])] = "(fallo)"
                print(f"  [{hechos}] fallo: {type(error).__name__}")

            # Escribir en cada correo, no al final: media hora es tiempo de
            # sobra para que algo se caiga, y perderla entera sería absurdo.
            guardar_predicciones_llm(cliente.model, PROMPT_VERSION, ya)

            if hechos % 10 == 0 or hechos == len(faltan):
                transcurrido = time.perf_counter() - inicio
                queda = transcurrido / hechos * (len(faltan) - hechos)
                print(
                    f"  {hechos}/{len(faltan)}  "
                    f"({transcurrido / hechos:.1f} s/correo, "
                    f"quedan ~{queda / 60:.0f} min)",
                    flush=True,
                )

    print(f"\nGuardado en {len(ya)} predicciones. Siguiente: python -m scripts.arbitro")


if __name__ == "__main__":
    main()
