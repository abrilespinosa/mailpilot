# ADR 005 — MailPilot pasa a ser un paquete instalable

Fecha: 2026-08-14
Estado: aceptado

## Contexto

`src/mailpilot` no estaba instalado, así que cada sitio se apañaba para
encontrarlo por su cuenta:

```python
# repetido en los SIETE scripts de scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

```ini
# pytest.ini hacía lo mismo, en limpio
pythonpath = src .
```

Funcionaba porque todo se lanzaba desde la raíz del proyecto y siempre en la
misma máquina. Es una deuda que ya estaba anotada como pendiente desde la
Fase 2.

El detonante es CI: una máquina limpia de GitHub Actions no sabe qué es
`mailpilot`, y el arreglo correcto no es replicar el apaño en el YAML.

## Decisión

**`pyproject.toml` con setuptools, e instalación editable.**

```bash
pip install -e ".[dev]"
```

`-e` (editable) no copia el código: deja un enlace a `src/`. Editar un archivo
tiene efecto inmediato, sin reinstalar. A cambio, `import mailpilot` funciona
desde cualquier directorio.

### Las dependencias se mudan a `pyproject.toml`

`requirements.txt` se borra. **Tener las dos listas era el problema**: hay que
acordarse de sincronizarlas, y cuando se separan el fallo no aparece donde se
cometió sino días después en otra máquina —o en CI, o en el ordenador de quien
clone el repo.

Las versiones siguen clavadas con `==`. Esto es una aplicación, no una
librería: nadie va a instalarla junto a otras cosas, así que reproducir el
entorno exacto vale más que ser flexible con los rangos.

`pytest` va a `[project.optional-dependencies].dev`, porque hace falta para
desarrollar y no para usar MailPilot.

### `src` sale de `pythonpath`

Y esto importa más de lo que parece. Dejarlo sería peor que inútil: los tests
pasarían **aunque la instalación estuviera rota**, porque encontrarían el
código por el camino viejo. El fallo saldría más tarde y en otro sitio.

Quitarlo convierte a los tests en una comprobación real de que el paquete está
bien instalado.

La raíz (`.`) sí se queda, por un motivo distinto: unos tests importan helpers
de otros (`from tests.test_repository import make_email`), y `tests/` no es un
paquete instalable ni debe serlo.

### Las plantillas y los logos son datos del paquete

`[tool.setuptools.package-data]` incluye `templates/*.html` y `static/*`. Sin
eso, una instalación limpia se queda sin dashboard: Jinja2 no encontraría el
HTML y la página reventaría en tiempo de ejecución, no al instalar.

## Alternativas descartadas

- **Dejarlo como estaba y replicar el `sys.path` en el YAML de CI**: rechazada.
  Convierte un apaño local en un apaño distribuido, y CI dejaría de parecerse
  a una instalación de verdad, que es justo lo que tiene que verificar.

- **Mover el código de `src/` a la raíz**: rechazada. El layout `src/` existe
  precisamente para que `import mailpilot` NO funcione por accidente al estar
  parada en la carpeta del proyecto. Quitarlo haría que los tests pasaran sin
  que el paquete estuviera instalado, que es el problema que este ADR resuelve.

- **Hatch, Poetry o uv**: rechazadas por ahora. setuptools ya está y no añade
  nada que aprender. Cambiar de gestor es una decisión con su propio coste y no
  hay ningún problema que hoy lo justifique.

## Consecuencias

- El comando de instalación cambia: `pip install -e ".[dev]"` en lugar de
  `pip install -r requirements.txt`. Hay que actualizarlo en README y CLAUDE.md.
- Los siete scripts pierden su preámbulo. Se pueden lanzar desde cualquier
  carpeta.
- **`auth.py` sigue resolviendo `credentials/` con `Path(__file__).parents[2]`**,
  que apunta a la raíz del repo. Con instalación editable es correcto. Con una
  instalación normal (copiando a site-packages) apuntaría a otro sitio y
  fallaría. Hoy no importa porque MailPilot solo se instala en editable, pero es
  lo primero que hay que arreglar si algún día se empaqueta de verdad.
- Añadir una dependencia pasa a ser: editarla en `pyproject.toml` y reinstalar.
