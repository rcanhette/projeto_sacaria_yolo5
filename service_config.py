"""
Shared helpers to configure the Projeto Sacaria service runtime.

The module centralises the logic previously embedded inside the legacy
pywin32-based Windows service so that other tooling such as the NSSM
installer or runner can reuse it without that dependency. Functions here do
not touch Windows-specific APIs; they only prepare environment variables and
the Waitress command-line.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path
from typing import Iterable, Tuple, List

DEFAULT_SERVICE_NAME = "ProjetoSacaria"
DEFAULT_DISPLAY_NAME = "Projeto Sacaria"
DEFAULT_DESCRIPTION = (
    "Servico Flask/Waitress para Projeto Sacaria (contagem com YOLOv5)."
)


def load_config(root: Path | str) -> configparser.ConfigParser:
    """
    Read ``windows_service.ini`` from ``root`` when present.
    """
    cfg = configparser.ConfigParser()
    ini_path = Path(root) / "windows_service.ini"
    if ini_path.is_file():
        try:
            cfg.read(ini_path, encoding="utf-8")
        except Exception:
            pass
    return cfg


def _resolve_server_int(
    cfg: configparser.ConfigParser,
    option: str,
    env_var: str,
    default: int,
) -> int:
    """
    Resolve integer values from the configuration file with fallbacks to
    environment variables and, finally, sane defaults.
    """
    if cfg.has_option("server", option):
        try:
            return cfg.getint("server", option)
        except ValueError:
            # Invalid custom value: fallback to default.
            pass
    env_val = os.getenv(env_var)
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            pass
    return default


def _first_existing_path(candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def build_waitress_command(
    root: Path | str,
    cfg: configparser.ConfigParser | None = None,
) -> Tuple[List[str], str, str]:
    """
    Assemble the command used to launch Waitress along with its working
    directory and log folder.
    """
    root_path = Path(root).resolve()
    cfg = cfg or load_config(root_path)

    logs_dir_cfg = cfg.get("paths", "logs_dir", fallback="logs")
    logs_dir = (
        Path(logs_dir_cfg)
        if os.path.isabs(logs_dir_cfg)
        else root_path / logs_dir_cfg
    )
    logs_dir.mkdir(parents=True, exist_ok=True)

    host = cfg.get("server", "host", fallback=os.getenv("APP_HOST", "0.0.0.0"))
    port = cfg.get("server", "port", fallback=os.getenv("APP_PORT", "8080"))

    os.environ.setdefault("YOLOV5_NO_AUTOINSTALL", "1")
    os.environ.setdefault("PYTHONPATH", str(root_path))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    threads = _resolve_server_int(cfg, "threads", "APP_THREADS", 16)
    backlog = _resolve_server_int(cfg, "backlog", "APP_BACKLOG", 2048)
    conn_limit = _resolve_server_int(cfg, "connection_limit", "APP_CONNECTION_LIMIT", 200)
    channel_timeout = _resolve_server_int(cfg, "channel_timeout", "APP_CHANNEL_TIMEOUT", 90)

    venv_waitress = root_path / "venv" / "Scripts" / "waitress-serve.exe"
    alt_waitress = cfg.get("paths", "waitress_exe", fallback=os.getenv("WAITRESS_EXE", ""))

    cmd: List[str]
    waitress_executable = _first_existing_path([alt_waitress, str(venv_waitress)])
    if waitress_executable:
        cmd = [waitress_executable]
    else:
        python_candidates = [
            cfg.get("paths", "python_exe", fallback=os.getenv("PYTHON_EXE", "")),
            sys.executable,
        ]
        python_executable = _first_existing_path(python_candidates)
        if not python_executable:
            python_executable = sys.executable
        cmd = [python_executable, "-m", "waitress"]

    args = [
        "--host",
        host,
        "--port",
        str(port),
        "--threads",
        str(threads),
        "--backlog",
        str(backlog),
        "--connection-limit",
        str(conn_limit),
        "--channel-timeout",
        str(channel_timeout),
        "--call",
        "app:create_app",
    ]

    if cfg.has_section("env"):
        for key, value in cfg.items("env"):
            os.environ.setdefault(key, value)

    if cfg.has_section("database"):
        db = cfg["database"]
        os.environ.setdefault("PGHOST", db.get("host", "localhost"))
        os.environ.setdefault("PGPORT", db.get("port", "5432"))
        os.environ.setdefault("PGDATABASE", db.get("database", "contagem_sacaria"))
        os.environ.setdefault("PGUSER", db.get("user", "postgres"))
        os.environ.setdefault("PGPASSWORD", db.get("password", "postgres"))

    return cmd + args, str(root_path), str(logs_dir)
