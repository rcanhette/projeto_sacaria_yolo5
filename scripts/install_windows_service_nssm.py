"""
Install or update the Projeto Sacaria service using NSSM.

The installer reads ``windows_service.ini`` to keep environment variables,
logging paths and the WSGI entrypoint in a single place.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from shutil import which

# Make the project root importable before touching service configuration.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service_config import (
    DEFAULT_DESCRIPTION,
    DEFAULT_DISPLAY_NAME,
    DEFAULT_SERVICE_NAME,
    build_waitress_command,
    load_config,
)


class InstallError(RuntimeError):
    """Raised when a required step to configure the NSSM service fails."""


def _resolve_executable(candidates: list[str]) -> str:
    """
    Return the first existing executable from the candidate list.
    Accepts absolute paths or names that can be located via PATH.
    """
    for candidate in candidates:
        if not candidate:
            continue
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        path = Path(expanded)
        if path.is_file():
            return str(path.resolve())
        lookup = which(expanded)
        if lookup:
            return lookup
    raise InstallError(
        f"Nenhuma instalacao valida do NSSM encontrada (tentado: {', '.join(candidates)})."
    )


def _run_nssm(nssm_exe: str, args: list[str], fatal: bool) -> subprocess.CompletedProcess[str]:
    """
    Execute an NSSM command. When ``fatal`` is True, non-zero exit codes raise.
    """
    proc = subprocess.run(
        [nssm_exe, *args],
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 and fatal:
        raise InstallError(
            f"Falha ao executar: {nssm_exe} {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def main() -> None:
    cfg = load_config(PROJECT_ROOT)

    # Resolve executables (NSSM, Python, Waitress runner).
    nssm_candidates = [
        cfg.get("paths", "nssm_exe", fallback=""),
        os.getenv("NSSM_EXE"),
        "nssm.exe",
    ]
    nssm_candidates = [c for c in nssm_candidates if c]
    nssm_exe = _resolve_executable(nssm_candidates)

    python_candidates = [
        cfg.get("paths", "python_exe", fallback=""),
        sys.executable,
    ]
    python_candidates = [c for c in python_candidates if c]
    python_exe = _resolve_executable(python_candidates)

    runner = PROJECT_ROOT / "scripts" / "nssm_service_runner.py"
    if not runner.is_file():
        raise InstallError(f"Runner ausente: {runner}")

    # Validate we can build the waitress command (and capture log dir).
    _cmd, _cwd, logs_dir = build_waitress_command(PROJECT_ROOT, cfg)
    logs_path = Path(logs_dir)

    stdout_path = logs_path / "service.out"
    stderr_path = logs_path / "service.err"

    service_section = cfg["service"] if cfg.has_section("service") else {}
    service_name = service_section.get("name", DEFAULT_SERVICE_NAME)
    display_name = service_section.get("display_name", DEFAULT_DISPLAY_NAME)
    description = service_section.get("description", DEFAULT_DESCRIPTION)

    print(f"Configurando servico '{service_name}' via NSSM...")

    # Try to stop/remove an existing instance, but ignore failures.
    _run_nssm(nssm_exe, ["stop", service_name], fatal=False)
    _run_nssm(nssm_exe, ["remove", service_name, "confirm"], fatal=False)

    # Install with the python runner script.
    install_args = ["install", service_name, python_exe, str(runner)]
    _run_nssm(nssm_exe, install_args, fatal=True)

    # Apply metadata and startup options.
    _run_nssm(nssm_exe, ["set", service_name, "AppDirectory", str(PROJECT_ROOT)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "DisplayName", display_name], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "Description", description], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "Start", "SERVICE_AUTO_START"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppStdout", str(stdout_path)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppStderr", str(stderr_path)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateFiles", "1"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateOnline", "1"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateSeconds", "86400"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateBytes", "15728640"], fatal=True)  # ~15MB

    print("Instalacao concluida. Inicie o servico manualmente:")
    print(f"  {nssm_exe} start {service_name}")


if __name__ == "__main__":
    try:
        main()
    except InstallError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        sys.exit(1)
