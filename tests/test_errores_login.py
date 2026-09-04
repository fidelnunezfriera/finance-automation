"""Traducción de los fallos de login de Trade Republic.

pytr no distingue entre «PIN mal» y «no hay red»: en los dos casos suelta la
traza de la excepción. Estas firmas son lo que permite decirle al usuario qué
ha pasado en una línea en vez de soltarle veinte de traza.

Las salidas de ejemplo están tomadas de la forma real de los errores de pytr
—`raise_for_status()` sobre la respuesta de la API, excepciones de `requests`—
no inventadas.
"""

import importlib.util
import sys

import pytest

from conftest import ROOT


def _cargar():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "run_pipeline", ROOT / "pipeline" / "run_pipeline.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


rp = _cargar()


# ── Cada fallo, reconocido ────────────────────────────────────────────────────

def test_pin_incorrecto():
    salida = (
        "Traceback (most recent call last):\n"
        '  File "pytr/api.py", line 228, in initiate_weblogin\n'
        "    r.raise_for_status()\n"
        "requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: "
        "https://api.traderepublic.com/api/v1/auth/web/login\n"
    )
    que_pasa, que_hacer = rp._diagnostico_login(salida)

    assert "PIN" in que_pasa
    assert "PIN" in que_hacer or "prefijo" in que_hacer


def test_codigo_de_verificacion_invalido():
    salida = ("requests.exceptions.HTTPError: 404 Client Error: Not Found for "
              "url: https://api.traderepublic.com/api/v1/auth/web/login/abc/9999")
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "código" in que_pasa.lower()


def test_sin_conexion():
    salida = ("requests.exceptions.ConnectionError: HTTPSConnectionPool("
              "host='api.traderepublic.com', port=443): Max retries exceeded")
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "conexión" in que_pasa.lower()


def test_bloqueo_por_intentos():
    salida = "HTTPError: 429 Client Error: TOO_MANY_REQUESTS"
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "bloquea" in que_pasa.lower() or "bloqueado" in que_pasa.lower()


def test_antibot():
    salida = "ERROR pytr.api Failed to get AWS WAF token."
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "antibot" in que_pasa.lower()


def test_faltan_credenciales():
    salida = ("ValueError: phone_no and pin must be specified explicitly or via "
              "/home/x/.pytr/credentials")
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "credenciales" in que_pasa.lower()


# ── Lo que no se reconoce ─────────────────────────────────────────────────────

def test_un_error_desconocido_no_se_inventa_diagnostico():
    """Preferible enseñar el error crudo a etiquetarlo mal."""
    assert rp._diagnostico_login("MemoryError: out of memory") is None
    assert rp._diagnostico_login("") is None


# ── El orden de las firmas ────────────────────────────────────────────────────

def test_lo_especifico_gana_a_lo_generico():
    """Una traza puede contener varias firmas a la vez.

    Un 401 dentro de un mensaje que además menciona la conexión debe leerse
    como credenciales, que es la causa, no como un problema de red.
    """
    salida = ("HTTPSConnectionPool(host='api.traderepublic.com'): "
              "401 Client Error: Unauthorized")
    que_pasa, _ = rp._diagnostico_login(salida)
    assert "PIN" in que_pasa


# ── Lo que se imprime ─────────────────────────────────────────────────────────

class _LogFalso:
    def __init__(self):
        self.errores = []

    def error(self, msg, *args):
        self.errores.append(msg % args if args else msg)


def test_el_mensaje_reconocido_no_enseña_la_traza(capsys):
    log = _LogFalso()
    rp._explicar_fallo_login(
        "requests.exceptions.HTTPError: 401 Client Error: Unauthorized\n"
        "  File 'pytr/api.py', line 228, in initiate_weblogin\n", log)

    salida = capsys.readouterr().out
    assert "PIN" in salida
    assert "Traceback" not in salida and "File '" not in salida
    # Pero la traza no se pierde: va al log.
    assert any("401" in e for e in log.errores)


def test_un_error_desconocido_enseña_las_ultimas_lineas(capsys):
    """Sin diagnóstico, ocultarlo todo dejaría al usuario sin nada."""
    log = _LogFalso()
    lineas = "\n".join(f"linea {i}" for i in range(20))
    rp._explicar_fallo_login(lineas, log)

    salida = capsys.readouterr().out
    assert "linea 19" in salida
    assert "linea 0" not in salida, "solo el final, no las veinte"
