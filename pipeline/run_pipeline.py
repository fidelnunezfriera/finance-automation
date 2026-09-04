#!/usr/bin/env python3
"""
Trade Republic full export pipeline.

Dos modos de ejecución:

  full        (por defecto) — los 4 pasos, incluida la exportación desde Trade
              Republic. Requiere intervención humana: pytr pide un OTP enviado
              al móvil en cada autenticación.

  unattended  (--unattended) — pasos 2 a 4 sobre el CSV ya presente en disco.
              No requiere interacción, y es el modo que ejecuta la tarea
              programada (ver pipeline/schedule_pipeline.py). Reaplica las
              reglas vigentes y refresca ledger y posiciones.

Run from project root with the venv Python:
    .venv\\Scripts\\python pipeline\\run_pipeline.py                (Windows)
    .venv/bin/python pipeline/run_pipeline.py --unattended         (Mac/Linux)
"""

import argparse
import os
import subprocess
import sys
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path

PROJ_DIR = Path(__file__).parent.parent.resolve()
PYTHON   = sys.executable
PYTR     = Path(sys.executable).parent / ("pytr.exe" if sys.platform == "win32" else "pytr")

sys.path.insert(0, str(PROJ_DIR))
from pipeline.logger import get_logger
from pipeline.metrics import consolidate


class StepFailed(Exception):
    """Un paso del pipeline devolvió un código de salida distinto de cero."""

    def __init__(self, step_key: str):
        self.step_key = step_key
        super().__init__(step_key)


def _load_cfg() -> dict:
    with open(PROJ_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Fallos de login de Trade Republic, traducidos.
#
# pytr no distingue entre «PIN mal» y «no hay red»: en los dos casos suelta la
# traza de la excepción y ya. Aquí se busca la firma de cada caso en su salida
# de error para poder decir qué ha pasado en una línea, con la traza completa
# guardada en el log para quien la necesite.
#
# El orden importa: lo más específico primero, porque una traza puede contener
# varias de estas cadenas.
DIAGNOSTICOS_LOGIN = [
    (("401", "Unauthorized"),
     "Teléfono o PIN incorrectos.",
     "Revisa el número (con prefijo, +34...) y el PIN de 4 cifras de la app."),
    (("wrong_code", "INVALID_CODE", "login/{}/", "404 Client Error"),
     "El código de verificación no es válido o ha caducado.",
     "Vuelve a lanzarlo y usa el código nuevo que te llegue al móvil."),
    (("TOO_MANY_REQUESTS", "429"),
     "Trade Republic ha bloqueado temporalmente los intentos.",
     "Espera unos minutos antes de volver a probar."),
    (("Failed to get AWS WAF token",),
     "Trade Republic ha rechazado la conexión por su sistema antibot.",
     "Suele resolverse reintentando en un rato."),
    (("getaddrinfo failed", "Max retries exceeded", "ConnectionError",
      "NewConnectionError", "Temporary failure in name resolution"),
     "No hay conexión con Trade Republic.",
     "Comprueba tu conexión a internet."),
    (("phone_no and pin must be specified",),
     "Faltan las credenciales de Trade Republic.",
     "Vuelve a ejecutarlo e introdúcelas cuando las pida."),
]


def _diagnostico_login(salida: str) -> tuple[str, str] | None:
    """Qué ha fallado en el login, si se reconoce."""
    for firmas, que_pasa, que_hacer in DIAGNOSTICOS_LOGIN:
        if any(f in salida for f in firmas):
            return que_pasa, que_hacer
    return None


def _explicar_fallo_login(salida: str, log) -> None:
    """Un mensaje legible en pantalla; la traza completa, al log."""
    diagnostico = _diagnostico_login(salida)

    print()
    print("=" * 64)
    if diagnostico:
        que_pasa, que_hacer = diagnostico
        print(f"  {que_pasa}")
        print(f"  {que_hacer}")
    else:
        print("  No se ha podido exportar de Trade Republic.")
        print("  El detalle está en el log; abajo van las últimas líneas.")
    print("=" * 64)

    if not diagnostico:
        # Sin diagnóstico no hay nada mejor que enseñar que el final del error,
        # que es donde suele estar la causa. Ocultarlo del todo dejaría al
        # usuario sin nada.
        for linea in [l for l in salida.strip().splitlines() if l.strip()][-6:]:
            print(f"  {linea}")
        print("=" * 64)

    print(f"  Salida completa en logs/")
    print()
    log.error("Trade Republic export failed. pytr stderr:\n%s", salida.strip())


def run(cmd: list, step: str, log, timings: dict, key: str,
        capturar_errores: bool = False) -> None:
    """Ejecuta un paso. Con `capturar_errores`, la salida de error se recoge
    en vez de imprimirse, para poder traducirla.

    Sólo stderr se captura: los prompts de pytr —teléfono, PIN, código— van por
    stdout, y capturarlos dejaría al usuario mirando una pantalla en blanco sin
    saber que le están preguntando algo.
    """
    log.info("Step %s starting", step)
    t0 = time.time()
    result = subprocess.run(
        [str(c) for c in cmd], cwd=PROJ_DIR,
        stderr=subprocess.PIPE if capturar_errores else None,
        text=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.time() - t0
    timings[key] = round(elapsed, 1)

    if result.returncode != 0:
        log.error("Step %s FAILED (exit %d) after %.1fs", step, result.returncode, elapsed)
        if capturar_errores:
            _explicar_fallo_login(result.stderr or "", log)
        raise StepFailed(key)

    if capturar_errores and result.stderr:
        # El paso ha ido bien: la salida de pytr no se enseña, pero se guarda.
        log.debug("pytr stderr:\n%s", result.stderr.strip())
    log.info("Step %s done in %.1fs", step, elapsed)


def newest_file(pattern: str) -> Path | None:
    matches = sorted(PROJ_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--unattended", action="store_true",
        help="salta la exportación desde Trade Republic (paso 1, requiere OTP) "
             "y procesa el CSV ya presente en disco",
    )
    args = ap.parse_args()

    cfg    = _load_cfg()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    mode   = "unattended" if args.unattended else "full"

    # Propagate run_id to all child processes so they share the same log file
    # and emit their metrics into the same run.
    os.environ["PIPELINE_RUN_ID"] = run_id

    log = get_logger("run_pipeline", cfg)

    source     = cfg["pipeline"]["source_file"]
    output_dir = cfg["pipeline"]["output_dir"]

    timings: dict = {}
    started_at    = datetime.now(timezone.utc).isoformat()
    t_start       = time.time()

    log.info("=" * 60)
    log.info("Pipeline started  run_id=%s  mode=%s", run_id, mode)
    log.info("=" * 60)

    total = 3 if args.unattended else 4
    n     = 0 if args.unattended else 1

    try:
        if args.unattended:
            log.info("Unattended mode -- skipping Trade Republic export (needs OTP)")
        else:
            print()
            print("Conectando con Trade Republic. Te pedirá el teléfono, el PIN")
            print("y el código que te llegue al móvil.")
            print()
            run([PYTR, "export_transactions"],
                f"1/{total} Export from Trade Republic", log, timings, "export_s",
                capturar_errores=True)

        if not (PROJ_DIR / source).exists():
            if args.unattended:
                log.error("%s not present. Unattended mode cannot export it -- "
                          "run the full pipeline once to generate it.", source)
            else:
                log.error("%s not generated", source)
            raise StepFailed("export_s")

        run([PYTHON, "pipeline/convert_pytr_to_clean.py"],
            f"{n + 1}/{total} Clean transactions", log, timings, "clean_s")

        ledger = newest_file(f"{output_dir}/transactions_clean_*.csv")
        if not ledger:
            log.error("No transactions_clean_*.csv found in %s/", output_dir)
            raise StepFailed("clean_s")
        log.info("Ledger: %s", ledger.name)

        run([PYTHON, "sheets/push_to_sheets.py", str(ledger)],
            f"{n + 2}/{total} Push to Google Sheets", log, timings, "push_s")

        run([PYTHON, "pipeline/derive_positions.py"],
            f"{n + 3}/{total} Derive positions", log, timings, "positions_s")

        if not (PROJ_DIR / output_dir / "derived_positions_latest.csv").exists():
            log.error("derived_positions_latest.csv not generated")
            raise StepFailed("positions_s")

    except StepFailed as e:
        elapsed = time.time() - t_start
        log.error("Pipeline aborted after %.1fs  run_id=%s", elapsed, run_id)
        consolidate(cfg, run_id,
                    started_at  = started_at,
                    finished_at = datetime.now(timezone.utc).isoformat(),
                    duration_s  = round(elapsed, 1),
                    mode        = mode,
                    status      = "error",
                    failed_step = e.step_key,
                    **timings)
        sys.exit(1)

    elapsed  = time.time() - t_start
    runs_csv = consolidate(cfg, run_id,
                           started_at  = started_at,
                           finished_at = datetime.now(timezone.utc).isoformat(),
                           duration_s  = round(elapsed, 1),
                           mode        = mode,
                           status      = "ok",
                           **timings)

    log.info("=" * 60)
    log.info("Pipeline completed in %.1fs  run_id=%s  mode=%s", elapsed, run_id, mode)
    if runs_csv:
        log.info("Run metrics appended to %s", runs_csv.relative_to(PROJ_DIR))
    log.info("=" * 60)


if __name__ == "__main__":
    main()
