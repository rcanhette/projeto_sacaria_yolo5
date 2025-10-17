"""
Instala ou atualiza o Agente do Projeto Sacaria via NSSM.

Este instalador é intencionalmente simples: ele registra um serviço que
executa `python agent_app.py` a partir da raiz do projeto, grava logs em
`logs/agent_service.out/err` e usa o arquivo `agent.ini` (ou a variável
de ambiente `AGENT_INI`) para configurar o agente.

Pré‑requisitos:
- NSSM disponível (no PATH ou informado via INI/ENV)
- `venv\\Scripts\\python.exe` ou Python no PATH
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path
from shutil import which

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallError(RuntimeError):
    pass


def _resolve_executable(candidates: list[str]) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        expanded = os.path.expandvars(os.path.expanduser(candidate))
        p = Path(expanded)
        if p.is_file():
            return str(p.resolve())
        hit = which(expanded)
        if hit:
            return hit
    raise InstallError(
        f"NSSM não encontrado (tentado: {', '.join([c for c in candidates if c])})."
    )


def _run_nssm(nssm_exe: str, args: list[str], fatal: bool) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run([nssm_exe, *args], cwd=str(PROJECT_ROOT), text=True, capture_output=True)
    if proc.returncode != 0 and fatal:
        raise InstallError(
            f"Falha ao executar: {nssm_exe} {' '.join(args)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def _read_agent_ini(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    try:
        if path.is_file():
            cfg.read(path, encoding="utf-8-sig")
    except Exception:
        pass
    return cfg


def main() -> None:
    # Descobre NSSM
    nssm_candidates = [
        os.getenv("NSSM_EXE"),
        str(PROJECT_ROOT / "nssm.exe"),
        "nssm.exe",
    ]
    nssm_exe = _resolve_executable(nssm_candidates)

    # Python
    py_candidates = [
        str(PROJECT_ROOT / "venv" / "Scripts" / "python.exe"),
        sys.executable,
        "python.exe",
        "python",
    ]
    python_exe = _resolve_executable(py_candidates)

    # Agent INI
    ini_env = os.getenv("AGENT_INI")
    ini_path = Path(ini_env) if ini_env else (PROJECT_ROOT / "agent.ini")
    cfg = _read_agent_ini(ini_path)
    agent_section = cfg["agent"] if cfg.has_section("agent") else {}
    agent_id = agent_section.get("id", "agent")
    tc_id = agent_section.get("tc_id", "?")

    service_name = f"ProjetoSacaria_Agent_{agent_id}"
    display_name = f"Projeto Sacaria - Agente ({agent_id}/TC{tc_id})"
    description = "Agente local do Projeto Sacaria (processa a TC e envia eventos)."

    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "agent_service.out"
    stderr_path = logs_dir / "agent_service.err"

    agent_app = PROJECT_ROOT / "agent_app.py"
    if not agent_app.is_file():
        raise InstallError(f"agent_app.py não encontrado em {PROJECT_ROOT}")

    print(f"Configurando serviço do Agente via NSSM: {service_name}")
    # Remove se já existir
    _run_nssm(nssm_exe, ["stop", service_name], fatal=False)
    _run_nssm(nssm_exe, ["remove", service_name, "confirm"], fatal=False)

    # Instala
    _run_nssm(nssm_exe, ["install", service_name, python_exe, str(agent_app)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppDirectory", str(PROJECT_ROOT)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "DisplayName", display_name], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "Description", description], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "Start", "SERVICE_AUTO_START"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppStdout", str(stdout_path)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppStderr", str(stderr_path)], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateFiles", "1"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateOnline", "1"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateSeconds", "86400"], fatal=True)
    _run_nssm(nssm_exe, ["set", service_name, "AppRotateBytes", "15728640"], fatal=True)

    # Informa AGENT_INI quando existir caminho customizado
    if ini_env:
        _run_nssm(nssm_exe, ["set", service_name, "AppEnvironmentExtra", f"AGENT_INI={ini_env}"], fatal=False)

    print("Instalação concluída. Inicie o serviço com:")
    print(f"  {nssm_exe} start {service_name}")


if __name__ == "__main__":
    try:
        main()
    except InstallError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        sys.exit(1)
