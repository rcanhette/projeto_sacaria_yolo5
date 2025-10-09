import os
import sys
import time
import subprocess
import configparser

import win32event
import win32service
import win32serviceutil
import servicemanager
import win32api
import win32con


class AppServerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ProjetoSacaria"
    _svc_display_name_ = "Projeto Sacaria YOLOv5"
    _svc_description_ = (
        "Serviço Flask/Waitress para Projeto Sacaria (contagem com YOLOv5)."
    )

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    # Utilitário de log no Event Log do Windows
    def log(self, msg):
        try:
            servicemanager.LogInfoMsg(f"[{self._svc_name_}] {msg}")
        except Exception:
            pass

    def _load_config(self, root: str) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        # Nome padrão do arquivo de configuração
        ini_path = os.path.join(root, "windows_service.ini")
        if os.path.isfile(ini_path):
            try:
                cfg.read(ini_path, encoding="utf-8")
            except Exception:
                pass
        return cfg

    def _build_command(self):
        root = os.path.dirname(os.path.abspath(__file__))
        cfg = self._load_config(root)

        # Diretório de logs: pode vir do INI (paths.logs_dir) ou padrão 'logs'
        logs_dir_cfg = cfg.get("paths", "logs_dir", fallback="logs")
        logs_dir = logs_dir_cfg if os.path.isabs(logs_dir_cfg) else os.path.join(root, logs_dir_cfg)
        os.makedirs(logs_dir, exist_ok=True)

        # Configs: prioridade INI > ambiente > padrão
        host = cfg.get("server", "host", fallback=os.getenv("APP_HOST", "0.0.0.0"))
        port = cfg.get("server", "port", fallback=os.getenv("APP_PORT", "8080"))
        # Evita qualquer tentativa do YOLO de instalar deps
        os.environ.setdefault("YOLOV5_NO_AUTOINSTALL", "1")
        # Garante PYTHONPATH com a raiz do projeto
        os.environ.setdefault("PYTHONPATH", root)

        # Caminhos prováveis do waitress-serve
        venv_waitress = os.path.join(root, "venv", "Scripts", "waitress-serve.exe")
        alt_waitress = cfg.get("paths", "waitress_exe", fallback=os.getenv("WAITRESS_EXE", ""))

        if alt_waitress and os.path.isfile(alt_waitress):
            cmd = [alt_waitress]
        elif os.path.isfile(venv_waitress):
            cmd = [venv_waitress]
        else:
            # Fallback: python -m waitress
            python = cfg.get("paths", "python_exe", fallback=os.getenv("PYTHON_EXE", sys.executable))
            cmd = [python, "-m", "waitress"]

        # Argumentos para servir o app via callable app:create_app
        args = [
            "--host", host,
            "--port", str(port),
            "--call", "app:create_app",
        ]

        # Carrega variáveis adicionais da seção [env]
        if cfg.has_section("env"):
            for k, v in cfg.items("env"):
                # Não sobrescreve se já definido no ambiente
                os.environ.setdefault(k, v)

        return cmd + args, root, logs_dir

    def SvcDoRun(self):
        self.log("Inicializando serviço...")

        try:
            cmd, cwd, logs_dir = self._build_command()
            stdout_path = os.path.join(logs_dir, "service.out")
            stderr_path = os.path.join(logs_dir, "service.err")

            # Abrimos stdout/stderr em append
            stdout = open(stdout_path, "a", buffering=1, encoding="utf-8", errors="ignore")
            stderr = open(stderr_path, "a", buffering=1, encoding="utf-8", errors="ignore")

            # Cria novo grupo para permitir sinais/terminação isolada
            creationflags = 0x00000200  # CREATE_NEW_PROCESS_GROUP

            self.process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )

            self.log(f"Processo iniciado PID={self.process.pid}: {' '.join(cmd)}")

            # Aguarda até receber stop, checando o processo periodicamente
            while True:
                rc = win32event.WaitForSingleObject(self.stop_event, 1000)
                if rc == win32event.WAIT_OBJECT_0:
                    break
                # Se o processo saiu por conta própria, encerramos também
                if self.process.poll() is not None:
                    break

        except Exception as e:
            self.log(f"Erro ao iniciar: {e}")

        # Finalização
        try:
            if self.process and self.process.poll() is None:
                self.log("Solicitando término do processo...")
                try:
                    # Primeiro tentamos encerrar gentilmente
                    self.process.terminate()
                except Exception:
                    pass
                # Aguarda um pouco
                for _ in range(30):
                    if self.process.poll() is not None:
                        break
                    time.sleep(0.2)
                if self.process.poll() is None:
                    self.log("Forçando encerramento do processo...")
                    try:
                        self.process.kill()
                    except Exception:
                        pass
        finally:
            self.process = None
            self.log("Serviço finalizado.")

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        # Sinaliza o loop principal
        win32event.SetEvent(self.stop_event)
        self.log("Recebido STOP.")
        self.ReportServiceStatus(win32service.SERVICE_STOPPED)


if __name__ == "__main__":
    # Permite: install/start/stop/remove
    win32serviceutil.HandleCommandLine(AppServerService)
