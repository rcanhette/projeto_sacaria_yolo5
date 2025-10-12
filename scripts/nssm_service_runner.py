"""
Entry point used by NSSM to launch the Projeto Sacaria backend.

The script relies on ``service_config`` so that all environment variables,
paths and logging rules defined in ``windows_service.ini`` continue to apply
even when the service is managed by NSSM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the repository root is importable before loading helpers.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service_config import build_waitress_command


def main() -> None:
    """
    Apply the service configuration and then replace the current process with
    the waitress command computed by ``service_config.build_waitress_command``.
    """
    cmd, cwd, _logs_dir = build_waitress_command(PROJECT_ROOT)

    # Ensure the waitress command runs from the project root, matching the
    # expectation of the configuration helper.
    os.chdir(cwd)

    # ``os.execv`` never returns: the current interpreter is replaced by the
    # waitress executable (or ``python -m waitress`` fallback).
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    main()
