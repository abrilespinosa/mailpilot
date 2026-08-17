"""
Siembra correos INVENTADOS para poder probar MailPilot sin una cuenta de Gmail.

Para qué sirve
--------------
1. Capturas y vídeo del dashboard sin enseñar correo real de nadie.
2. Que quien clone el repo pueda ver MailPilot funcionando en un minuto, sin
   credenciales de Google, sin OAuth y sin Ollama descargado.

Lo segundo es lo que convierte un repositorio que se lee en uno que se prueba.

Nunca toca la base de datos real
--------------------------------
Escribe en `<tu_base>_demo`, derivada de DATABASE_URL, igual que
tests/conftest.py deriva `<tu_base>_test`. La protección es estructural: no hay
ningún camino por el que este script escriba en `mailpilot`, ni equivocándose
de argumento, porque el nombre no se pasa por argumento.

Uso
---
    python scripts/seed_demo.py

El propio script imprime el comando para arrancar el dashboard contra la demo.

Ese comando NUNCA lleva la cadena de conexión escrita dentro: la lee de .env
sobre la marcha. Este script existe para hacer capturas y vídeo, así que todo
lo que imprime acaba tarde o temprano en una pantalla compartida.
"""

import argparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from mailpilot.db import get_database_url
from mailpilot.models import (
    ActionProposal,
    Base,
    Category,
    Classification,
    Email,
    ProposalStatus,
    ProposedAction,
)

# Correos de mentira, escritos para que la demo enseñe algo, no para rellenar.
#
# `propuesta` es lo que "diría el modelo". Dos de ellas están MAL a propósito:
# una demo en la que la IA acierta siempre no enseña para qué existe el paso
# humano, que es de lo que va el proyecto entero.
#
# `motivo` es la explicación que acompañaría a la propuesta. NO sale de un
# modelo: está escrita a mano, como todo lo demás de este archivo. Antes decía
# literalmente "clasificación de demostración, no sale de un modelo", que era
# honesto pero dejaba en blanco la línea más visible de cada tarjeta justo en
# una demo de un clasificador. Quien mire el dashboard tiene que poder juzgar
# el razonamiento, que es la única forma de decidir si la propuesta es buena.
# La honestidad la sostienen `model_used="demo"` en la base de datos y el
# encabezado de este archivo, no una frase repetida diez veces.
#
#   (remitente, asunto, extracto, propuesta_del_modelo, confianza, motivo)
CORREOS = [
    (
        "citas@centromedico.example",
        "Recordatorio: cita con Dermatología el 21 de agosto",
        "Le recordamos su cita el jueves 21 a las 10:15. Puede confirmar o "
        "anular desde el enlace.",
        # MAL a propósito: es `personal`. Una cita médica se parece muchísimo a
        # una reserva —fecha, hora, sitio, "confirme"— y el modelo lee la forma,
        # no el fondo. Es el error real que motivó la regla 0 del prompt v7.
        Category.COMPRAS,
        0.95,
        # El motivo delata el error: el razonamiento es impecable y la
        # conclusión es falsa. Eso es lo que hace falta ver para entender por
        # qué la confianza no sirve de umbral.
        "Confirmación con fecha, hora y enlace para gestionarla: tiene la "
        "forma de una reserva.",
    ),
    (
        "lucia.moreno@example.org",
        "Fotos del finde 📷",
        "Te paso la carpeta con las fotos del sábado, hay algunas muy buenas.",
        Category.PERSONAL,
        0.98,
        "Alguien escribe en primera persona y comparte archivos. No hay "
        "remitente automático ni marca detrás.",
    ),
    (
        "seleccion@estudiodiseno.example",
        "Propuesta de colaboración remunerada",
        "Nos gustaría contar contigo para un proyecto de dos semanas. "
        "Adjuntamos condiciones y tarifa.",
        Category.EMPLEO,
        0.95,
        "Ofrecen un encargo pagado con condiciones y tarifa concretas.",
    ),
    (
        "no-reply@sede.administracion.example",
        "Su solicitud de beca ha sido registrada",
        "Número de expediente 2026/4471. Puede consultar el estado en la sede "
        "electrónica.",
        Category.TRAMITES,
        0.95,
        "Organismo público, número de expediente y sede electrónica: papeleo.",
    ),
    (
        "pedidos@libreria.example",
        "Tu pedido #88213 va en camino",
        "Hemos enviado tu pedido. Llegará el martes. Aquí tienes el número de "
        "seguimiento.",
        Category.COMPRAS,
        0.99,
        "Envío de un pedido ya hecho, con número de seguimiento.",
    ),
    (
        "security@example.com",
        "Nuevo inicio de sesión en tu cuenta",
        "Hemos detectado un acceso desde un dispositivo nuevo. Si no has sido "
        "tú, cambia tu contraseña.",
        Category.AVISOS,
        0.95,
        "Notificación automática de seguridad. No vende nada ni pide "
        "gestiones, solo informa.",
    ),
    (
        "ofertas@tiendaropa.example",
        "-40 % en TODA la web, solo hoy 🔥",
        "Últimas horas. Envío gratis a partir de 30 €.",
        Category.PROMOCIONES,
        0.95,
        "Descuento con caducidad y envío gratis: correo comercial de una "
        "tienda.",
    ),
    (
        "newsletter@revistatech.example",
        "Boletín semanal: 5 lecturas sobre bases de datos",
        "Esta semana: índices parciales, aislamiento de transacciones y por qué "
        "tu ORM te miente.",
        Category.OTROS,
        0.95,
        "Boletín periódico de una suscripción. Ni vende ni exige nada.",
    ),
    (
        "info@asociacionvecinal.example",
        "Convocatoria de la asamblea de septiembre",
        "El orden del día incluye la renovación de la junta y las cuentas del "
        "ejercicio.",
        # MAL a propósito: es `avisos`. La frontera `otros`/`avisos` es la que
        # más errores acumula en las mediciones reales (4 de 14 en test6).
        Category.OTROS,
        0.95,
        # El motivo enseña el fallo de fondo documentado en la Fase 6: `otros`
        # significa "boletín al que me suscribí" y "no sé" a la vez, así que el
        # modelo lo usa de cajón de sastre. No lo arregla otro prompt.
        "Comunicación de una asociación a sus socios. No termina de encajar en "
        "ninguna categoría.",
    ),
    (
        # LA DEFENSA CONTRA XSS, EN VIVO.
        #
        # El asunto y el remitente los escribe quien manda el correo, así que
        # son texto de origen no fiable pintado en una página. Jinja2 los
        # escapa, así que esto sale como texto literal en el dashboard en vez
        # de ejecutarse. Está aquí para que se vea en la captura.
        "<img src=x onerror=alert('xss')>@atacante.example",
        "<script>alert('MailPilot')</script> Enhorabuena, has ganado",
        "Pincha aquí para reclamar tu premio antes de que caduque.",
        Category.PROMOCIONES,
        0.99,
        "Premio no solicitado y enlace con prisa: patrón comercial de siempre.",
    ),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    base = make_url(get_database_url())
    demo = base.set(database=f"{base.database}_demo")

    # CREATE DATABASE no puede ir dentro de una transacción, y hay que lanzarlo
    # conectado a otra base distinta de la que se crea.
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conexion:
        existe = conexion.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :nombre"),
            {"nombre": demo.database},
        ).scalar()
        if not existe:
            conexion.execute(text(f'CREATE DATABASE "{demo.database}"'))
            print(f"Creada la base de datos {demo.database}")
    admin.dispose()

    engine = create_engine(demo, future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc)

    with Session(engine) as sesion:
        for numero, (
            remitente,
            asunto,
            extracto,
            propuesta,
            confianza,
            motivo,
        ) in enumerate(CORREOS):
            correo = Email(
                gmail_message_id=f"demo-{numero:03d}",
                gmail_thread_id=f"demo-hilo-{numero:03d}",
                subject=asunto,
                sender=remitente,
                snippet=extracto,
                received_at=ahora - timedelta(hours=numero * 3),
                raw_labels=["INBOX", "UNREAD"],
                en_papelera=False,
            )
            sesion.add(correo)
            sesion.flush()

            sesion.add(
                Classification(
                    email_id=correo.id,
                    category=propuesta,
                    confidence=confianza,
                    reasoning=motivo,
                    # La marca de que esto no sale de un modelo vive AQUÍ, en
                    # un campo que se guarda con cada fila, no en un texto que
                    # hay que repetir en la pantalla.
                    model_used="demo",
                )
            )
            sesion.add(
                ActionProposal(
                    email_id=correo.id,
                    proposed_action=ProposedAction.CATEGORIZE,
                    category=propuesta,
                    reason=motivo,
                    confidence=confianza,
                    status=ProposalStatus.PENDING,
                )
            )
        sesion.commit()

    print(f"\n{len(CORREOS)} correos inventados sembrados en {demo.database}.")
    print("Ninguno es real: se pueden enseñar en capturas y vídeo sin problema.\n")
    print("Arranca el dashboard contra la demo:\n")
    # El comando NO puede llevar la cadena de conexión dentro: este script
    # existe para hacer capturas y vídeo, así que su salida acaba en una
    # pantalla compartida. Se construye leyendo .env, que está gitignored.
    #
    # Añadir `_demo` al final funciona porque el nombre de la base es el último
    # tramo de la URL.
    print("  DATABASE_URL=\"$(grep -m1 '^DATABASE_URL' .env | cut -d= -f2-)_demo\" \\")
    print("      uvicorn mailpilot.api:app\n")
    print("  http://localhost:8000/   (modo ciego, el que viene por defecto)")
    print("  http://localhost:8000/?ciego=0   enseña lo que propuso el modelo\n")
    print("Dos propuestas están MAL a propósito: la cita médica y la")
    print("convocatoria vecinal. Sin errores no se ve para qué está la persona.")


if __name__ == "__main__":
    main()
