"""
Trae el CUERPO de los correos del conjunto, para probar si mejora al modelo.

POR QUÉ HACE FALTA
------------------
Hay una frontera que con asunto y remitente es invisible:

    "Confirma tu cuenta en Club·by"   -> seguridad
    "Welcome to Supabase"             -> avisos

Las palabras se solapan —cuenta, confirmar, activar, welcome— y por fuera son
casi el mismo correo. Por dentro no: uno lleva un enlace de verificación, el
otro lleva consejos de uso. La señal existe, pero está en el cuerpo.

DÓNDE SE GUARDA, Y DÓNDE NO
---------------------------
En `entrenamiento/cuerpos.json`, y en ningún otro sitio.

- **NO va a PostgreSQL.** La ingestión sigue pidiendo `format="metadata"`, y
  esa decisión no cambia: el contenido de los correos no se persiste en la base
  de datos del proyecto.
- **NO va a git.** La carpeta entera está ignorada salvo el README.
- Es un archivo de trabajo, borrable: si desaparece, se vuelve a pedir.

Existe solo para no repetir 559 llamadas a Gmail en cada reentrenamiento. Si
prefieres que no quede nada en disco, bórralo al terminar:

    rm entrenamiento/cuerpos.json

PROMPT INJECTION: AQUÍ NO APLICA
--------------------------------
Darle el cuerpo al LLM abriría una superficie de ataque, porque un correo
podría intentar darle instrucciones. Al modelo entrenado no le puede pasar:
TF-IDF no lee instrucciones, cuenta palabras. Un correo que diga "ignora tus
reglas" solo aporta las palabras «ignora» y «reglas» a un vector.

Uso:
    python scripts/traer_cuerpos.py
    python scripts/traer_cuerpos.py --force   # vuelve a pedirlos todos
"""

import argparse
import json
from pathlib import Path

from mailpilot.db import SessionLocal
from mailpilot.gmail import fetch_body, get_service
from mailpilot.models import Email

RAIZ = Path(__file__).resolve().parents[1]
DATASET = RAIZ / "entrenamiento" / "dataset.json"
CUERPOS = RAIZ / "entrenamiento" / "cuerpos.json"

# Los primeros caracteres bastan y sobran. El principio del correo es donde
# está lo que decide —"haz clic para verificar" frente a "esto es lo que puedes
# hacer"—, y el resto son pies de página y avisos legales iguales en todos.
MAXIMO = 1500


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="vuelve a pedirlos todos")
    args = parser.parse_args()

    if not DATASET.exists():
        raise SystemExit("Falta el conjunto. Ejecuta: python scripts/construir_dataset.py")

    datos = json.loads(DATASET.read_text(encoding="utf-8"))
    ids = [f["email_id"] for f in datos["train"] + datos["test"]]

    cache: dict[str, str] = {}
    if CUERPOS.exists() and not args.force:
        cache = json.loads(CUERPOS.read_text(encoding="utf-8"))
        print(f"Ya había {len(cache)} cuerpos guardados.")

    faltan = [i for i in ids if str(i) not in cache]
    if not faltan:
        print("No falta ninguno.")
        return

    print(f"Pidiendo {len(faltan)} cuerpos a Gmail…")
    servicio = get_service()
    fallos = 0

    with SessionLocal() as session:
        for numero, email_id in enumerate(faltan, start=1):
            correo = session.get(Email, email_id)
            if correo is None:
                continue
            try:
                cache[str(email_id)] = fetch_body(servicio, correo.gmail_message_id)[:MAXIMO]
            except Exception:
                # Un correo borrado a mano en Gmail ya no está. Se guarda vacío
                # para no volver a pedirlo en cada ejecución.
                cache[str(email_id)] = ""
                fallos += 1

            if numero % 50 == 0:
                print(f"  {numero}/{len(faltan)}…")

            # Guardado incremental: si se corta a la mitad, no se pierde lo
            # traído. Son 559 llamadas de red y cortarse es normal.
            if numero % 100 == 0:
                CUERPOS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    CUERPOS.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    vacios = sum(1 for v in cache.values() if not v)
    print(f"\nGuardados {len(cache)} cuerpos en {CUERPOS.name}")
    print(f"  vacíos o no recuperables: {vacios}   fallos: {fallos}")
    print(f"  media de caracteres: {sum(len(v) for v in cache.values()) // max(len(cache),1)}")
    print("\nNo se ha escrito nada en PostgreSQL. El archivo está gitignored.")


if __name__ == "__main__":
    main()
