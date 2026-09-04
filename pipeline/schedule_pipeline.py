#!/usr/bin/env python3
"""
Instalador de la ejecución programada del pipeline.

Lee el bloque `schedule` de config.yaml y registra (o elimina) una tarea
periódica en el planificador del sistema operativo:

  - Windows: Programador de tareas, vía `schtasks`
  - Linux / macOS: crontab del usuario

La tarea invoca `run_pipeline.py --unattended`, es decir los pasos 2 a 4
(limpieza, carga en Google Sheets y derivación de posiciones). El paso 1
—exportación desde Trade Republic— queda deliberadamente fuera: pytr exige un
código OTP enviado al móvil en cada autenticación, así que no puede ejecutarse
sin una persona delante. Es una restricción del proveedor, no del diseño.

Limitación conocida en Windows (ver comentario en _win_install): la tarea
registrada no se recupera si el PC está apagado, dormido, o encendido pero
con batería a la hora programada -- ese día no se ejecuta y no queda ningún
error registrado, porque nunca llega a arrancar.

Uso:
    python pipeline/schedule_pipeline.py --install
    python pipeline/schedule_pipeline.py --status
    python pipeline/schedule_pipeline.py --remove
    python pipeline/schedule_pipeline.py --install --dry-run
"""

import argparse
import platform
import re
import subprocess
import sys
from pathlib import Path

import yaml

PROJ_DIR = Path(__file__).parent.parent.resolve()
PYTHON   = Path(sys.executable).resolve()

DAYS = {
    "monday": "MON", "tuesday": "TUE", "wednesday": "WED", "thursday": "THU",
    "friday": "FRI", "saturday": "SAT", "sunday": "SUN",
}
CRON_DOW = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 0,
}

CRON_MARKER = "# finance-automation-pipeline"


def _load_cfg() -> dict:
    with open(PROJ_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _schedule_cfg(cfg: dict) -> dict:
    sc = cfg.get("schedule")
    if not sc:
        sys.exit("config.yaml no tiene bloque 'schedule'. "
                 "Cópialo de config.example.yaml.")

    freq = str(sc.get("frequency", "daily")).lower()
    if freq not in ("hourly", "daily", "weekly"):
        sys.exit(f"frequency '{freq}' no válida. Usa: hourly, daily o weekly.")

    hhmm = str(sc.get("time", "07:00"))
    if freq != "hourly" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hhmm):
        sys.exit(f"time '{hhmm}' no válida. Formato esperado HH:MM en 24 h.")

    dow = str(sc.get("day_of_week", "monday")).lower()
    if freq == "weekly" and dow not in DAYS:
        sys.exit(f"day_of_week '{dow}' no válido. Usa un día en inglés en minúsculas.")

    return {
        "enabled":     bool(sc.get("enabled", False)),
        "frequency":   freq,
        "time":        hhmm,
        "day_of_week": dow,
        "task_name":   str(sc.get("task_name", "FinanceAutomationPipeline")),
    }


def _command() -> str:
    """Comando que ejecutará el planificador."""
    return f'"{PYTHON}" "{PROJ_DIR / "pipeline" / "run_pipeline.py"}" --unattended'


# --------------------------------------------------------------------------
# Windows — schtasks
# --------------------------------------------------------------------------

def _win_install(sc: dict, dry_run: bool) -> None:
    # LIMITACIÓN CONOCIDA (verificada 18/08/2026 contra el XML de una tarea
    # real): schtasks /Create con esta sintaxis básica no activa
    # StartWhenAvailable ni WakeToRun. Si el PC está apagado, dormido o (por
    # los valores por defecto) encendido pero con batería y desenchufado a la
    # hora programada, la tarea no se ejecuta ese día -- sin recuperación
    # posterior y sin ningún error registrado en logs/runs.csv, porque nunca
    # llega a arrancar. Arreglarlo exige pasar por PowerShell
    # (Register-ScheduledTask / Set-ScheduledTask) o crear la tarea desde un
    # XML propio en vez de argumentos sueltos; se deja documentado y sin
    # tocar (decisión 18/08/2026).
    freq_map = {"hourly": "HOURLY", "daily": "DAILY", "weekly": "WEEKLY"}
    cmd = [
        "schtasks", "/Create", "/TN", sc["task_name"],
        "/TR", _command(),
        "/SC", freq_map[sc["frequency"]],
        "/F",  # sobrescribe si ya existe
    ]
    if sc["frequency"] != "hourly":
        cmd += ["/ST", sc["time"]]
    if sc["frequency"] == "weekly":
        cmd += ["/D", DAYS[sc["day_of_week"]]]

    if dry_run:
        print("DRY RUN — se ejecutaría:")
        print("  " + subprocess.list2cmdline(cmd))
        return

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout or "", r.stderr or "", sep="")
        sys.exit(f"No se pudo crear la tarea (código {r.returncode}). "
                 "En Windows puede requerir una consola de administrador.")
    print(f"Tarea '{sc['task_name']}' creada.")


def _win_remove(sc: dict) -> None:
    r = subprocess.run(["schtasks", "/Delete", "/TN", sc["task_name"], "/F"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout or "", r.stderr or "", sep="")
        sys.exit(f"No se pudo eliminar la tarea (código {r.returncode}).")
    print(f"Tarea '{sc['task_name']}' eliminada.")


def _win_status(sc: dict) -> None:
    r = subprocess.run(["schtasks", "/Query", "/TN", sc["task_name"], "/V", "/FO", "LIST"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"No hay ninguna tarea registrada con el nombre '{sc['task_name']}'.")
        return
    keep = ("TaskName", "Next Run Time", "Status", "Last Run Time", "Last Result",
            "Schedule Type", "Start Time", "Days", "Task To Run",
            "Nombre de tarea", "Hora próxima ejecución", "Estado",
            "Hora última ejecución", "Último resultado", "Tipo de programación",
            "Hora de inicio", "Días", "Tarea que se ejecutará")
    for line in r.stdout.splitlines():
        if any(line.strip().startswith(k) for k in keep):
            print("  " + line.strip())


# --------------------------------------------------------------------------
# Unix — crontab
# --------------------------------------------------------------------------

def _cron_expression(sc: dict) -> str:
    if sc["frequency"] == "hourly":
        return "0 * * * *"
    hh, mm = sc["time"].split(":")
    hh, mm = int(hh), int(mm)
    if sc["frequency"] == "daily":
        return f"{mm} {hh} * * *"
    return f"{mm} {hh} * * {CRON_DOW[sc['day_of_week']]}"


def _read_crontab() -> list[str]:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _write_crontab(lines: list[str]) -> None:
    payload = "\n".join(lines).rstrip() + "\n"
    r = subprocess.run(["crontab", "-"], input=payload, text=True,
                       capture_output=True)
    if r.returncode != 0:
        print(r.stderr or "")
        sys.exit(f"No se pudo escribir el crontab (código {r.returncode}).")


def _strip_existing(lines: list[str]) -> list[str]:
    """Elimina la entrada previa (línea marcador + la línea de cron siguiente)."""
    out, skip = [], False
    for line in lines:
        if skip:
            skip = False
            continue
        if line.strip() == CRON_MARKER:
            skip = True
            continue
        out.append(line)
    return out


def _unix_install(sc: dict, dry_run: bool) -> None:
    entry = f"{_cron_expression(sc)} cd {PROJ_DIR} && {_command()} >> {PROJ_DIR}/logs/cron.log 2>&1"
    if dry_run:
        print("DRY RUN — se añadiría al crontab:")
        print(f"  {CRON_MARKER}")
        print(f"  {entry}")
        return
    lines = _strip_existing(_read_crontab())
    lines += [CRON_MARKER, entry]
    _write_crontab(lines)
    print("Entrada de crontab instalada:")
    print(f"  {entry}")


def _unix_remove(sc: dict) -> None:
    lines = _read_crontab()
    stripped = _strip_existing(lines)
    if len(stripped) == len(lines):
        print("No había ninguna entrada de crontab del pipeline.")
        return
    _write_crontab(stripped)
    print("Entrada de crontab eliminada.")


def _unix_status(sc: dict) -> None:
    lines = _read_crontab()
    for i, line in enumerate(lines):
        if line.strip() == CRON_MARKER and i + 1 < len(lines):
            print("  " + lines[i + 1])
            return
    print("No hay ninguna entrada de crontab del pipeline.")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true",
                   help="registra la tarea según el bloque schedule de config.yaml")
    g.add_argument("--remove", action="store_true", help="elimina la tarea registrada")
    g.add_argument("--status", action="store_true", help="muestra la tarea registrada")
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra lo que haría sin tocar el planificador")
    args = ap.parse_args()

    sc        = _schedule_cfg(_load_cfg())
    is_windows = platform.system() == "Windows"

    if args.status:
        print(f"Planificador: {'Programador de tareas (Windows)' if is_windows else 'cron'}")
        print(f"Config: enabled={sc['enabled']}  frequency={sc['frequency']}  "
              f"time={sc['time']}"
              + (f"  day={sc['day_of_week']}" if sc["frequency"] == "weekly" else ""))
        print("Estado en el sistema:")
        (_win_status if is_windows else _unix_status)(sc)
        return

    if args.remove:
        (_win_remove if is_windows else _unix_remove)(sc)
        return

    if not sc["enabled"]:
        sys.exit("schedule.enabled es false en config.yaml. "
                 "Ponlo a true para instalar la tarea.")

    print(f"Frecuencia: {sc['frequency']}"
          + ("" if sc["frequency"] == "hourly" else f" a las {sc['time']}")
          + (f" ({sc['day_of_week']})" if sc["frequency"] == "weekly" else ""))
    print(f"Comando:    {_command()}")
    (_win_install if is_windows else _unix_install)(sc, args.dry_run)


if __name__ == "__main__":
    main()
