# agent_app.py
import logging
import threading
import time
from pathlib import Path
from logging.config import dictConfig
from flask import Flask, jsonify, request
import socket
import cv2
import os

# Reuso do CapturePoint local
from services.capture_point import CapturePoint
from services.local_queue import LocalQueue

# Config via INI simples (um arquivo por agente)
import configparser
import requests
from uuid import uuid4

LOGS_DIR = Path(__file__).resolve().parent / "logs"


class CentralClient:
    def __init__(self, base_url: str, token: str, agent_id: str, verify: bool | str | None = True):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.agent_id = agent_id
        # HTTP pool with keep-alive for efficiency
        import requests
        from requests.adapters import HTTPAdapter
        try:
            # Prefer urllib3 Retry for backoff com limites seguros
            from urllib3.util.retry import Retry
        except Exception:
            Retry = None
        try:
            self.session = requests.Session()
            if Retry is not None:
                retry = Retry(
                    total=1,
                    connect=1,
                    read=1,
                    status=1,
                    backoff_factor=0.1,  # tempos menores para não travar start/stop
                    status_forcelist=(429, 502, 503, 504),
                    allowed_methods=("GET", "POST"),
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
            else:
                adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50)
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)
            try:
                if verify is not None:
                    self.session.verify = verify  # bool ou caminho CA
                    if verify is False:
                        # Evita warnings ruidosos quando opta por ignorar validação
                        try:
                            import urllib3
                            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                        except Exception:
                            pass
            except Exception:
                pass
        except Exception:
            # Fallback: direct requests if session fails
            self.session = requests

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

    def heartbeat(self, tc_id: int, hostname: str | None = None, status: str | None = None, version: str | None = None):
        url = f"{self.base_url}/api/agent/v1/heartbeat"
        payload = {"agent_id": self.agent_id, "tc_id": tc_id, "hostname": hostname, "status": status, "version": version}
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=5)
        except Exception:
            pass

    def session_start(self, tc_id: int, lote: str, contagem_alvo: int | None = None) -> int | None:
        url = f"{self.base_url}/api/agent/v1/session/start"
        payload = {"agent_id": self.agent_id, "tc_id": tc_id, "lote": lote, "contagem_alvo": contagem_alvo}
        try:
            r = self.session.post(url, json=payload, headers=self._headers(), timeout=5)
            r.raise_for_status()
            data = r.json()
            return data.get("session_db_id")
        except Exception:
            return None

    def session_update(self, tc_id: int, session_db_id: int, increment: int, total: int, event_id: str | None = None):
        url = f"{self.base_url}/api/agent/v1/session/update"
        payload = {
            "agent_id": self.agent_id,
            "tc_id": tc_id,
            "session_db_id": session_db_id,
            "increment": increment,
            "total": total,
        }
        if event_id:
            payload["event_id"] = event_id
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=5)
        except Exception:
            pass

    def session_finish(self, tc_id: int, session_db_id: int, total: int, observacao: str | None = None, status: str | None = None, camera_alert: str | None = None):
        url = f"{self.base_url}/api/agent/v1/session/finish"
        payload = {
            "agent_id": self.agent_id,
            "tc_id": tc_id,
            "session_db_id": session_db_id,
            "total": total,
            "observacao": observacao,
        }
        if status:
            payload["status"] = status
        if camera_alert:
            payload["camera_alert"] = camera_alert
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=5)
        except Exception:
            pass

    def session_pause(self, tc_id: int, session_db_id: int, motivo: str | None = None):
        url = f"{self.base_url}/api/agent/v1/session/pause"
        payload = {
            "agent_id": self.agent_id,
            "tc_id": tc_id,
            "session_db_id": session_db_id,
            "motivo": motivo,
        }
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=5)
        except Exception:
            pass

    def get_config(self, tc_id: int):
        url = f"{self.base_url}/api/agent/v1/config/{tc_id}"
        try:
            r = self.session.get(url, headers=self._headers(), timeout=10)
            if r.ok:
                return r.json()
        except Exception:
            return None
        return None

    def get_active_session(self, tc_id: int):
        url = f"{self.base_url}/api/agent/v1/session/active/{tc_id}"
        try:
            r = self.session.get(url, headers=self._headers(), timeout=10)
            if r.ok:
                return r.json()
        except Exception:
            return None
        return None


def _configure_logging():
    if getattr(_configure_logging, "_configured", False):
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"standard": {"format": "%(asctime)s %(levelname)s [%(name)s] %(message)s"}},
        "handlers": {
            "stdout": {"class": "logging.StreamHandler", "level": "INFO", "stream": "ext://sys.stdout", "formatter": "standard"},
            "file": {"class": "logging.handlers.RotatingFileHandler", "level": "INFO", "formatter": "standard", "filename": str(LOGS_DIR/"agent_runtime.log"), "maxBytes": 5*1024*1024, "backupCount": 3, "encoding": "utf-8"},
        },
        "root": {"level": "INFO", "handlers": ["stdout", "file"]},
    })
    _configure_logging._configured = True


class AgentService:
    def __init__(self, cfg_path: Path):
        self.cfg = configparser.ConfigParser()
        # Usa 'utf-8-sig' para tolerar arquivos salvos com BOM (Notepad/Windows)
        self.cfg.read(cfg_path, encoding="utf-8-sig")
        self.log = logging.getLogger("agent")
        # Leitura de config
        a = self.cfg["agent"]
        self.agent_id = a.get("id", "agent-1")
        self.tc_id = a.getint("tc_id", fallback=1)
        self.central_url = a.get("central_url", "http://localhost:8080")
        self.token = a.get("token", "dev-token")
        # TLS/HTTPS do Central
        try:
            v = (a.get("central_verify", fallback="true") or "true").strip().lower()
            self.central_verify = (v in ("1", "true", "yes", "on"))
        except Exception:
            self.central_verify = True
        try:
            ca = (a.get("central_ca", fallback="") or "").strip()
            self.central_ca = str(Path(ca)) if ca else None
        except Exception:
            self.central_ca = None
        self.cp = None
        self.thread = None
        self.stop_event = threading.Event()
        self._start_lock = threading.Lock()
        self._start_inflight = False
        self._last_start_ts = 0.0
        # Porta HTTP do agente (informada no heartbeat como IP:porta)
        try:
            import os as _os
            self.http_port = int(a.get('port', fallback=_os.getenv('AGENT_PORT') or '9090'))
        except Exception:
            self.http_port = 9090

        # Optional streaming parameters
        try:
            self.stream_fps = max(1, int(a.get("stream_fps", fallback="12")))
        except Exception:
            self.stream_fps = 12
        try:
            self.jpeg_quality = min(95, max(30, int(a.get("jpeg_quality", fallback="80"))))
        except Exception:
            self.jpeg_quality = 80
        try:
            self.start_cooldown_sec = float(a.get("start_cooldown_sec", fallback="2.0"))
        except Exception:
            self.start_cooldown_sec = 2.0
        # Shared JPEG buffer for fan-out
        self._jpeg_lock = threading.Lock()
        self._last_jpeg = None
        self._last_jpeg_ts = 0.0
        # Snapshot cache (câmera temporária aberta para capturar frames sem sessão)
        self._snap_cam = None
        self._snap_cam_src = None
        self._snap_cam_opened_ts = 0.0
        # Preferir parâmetros vindos da Central (runtime-only)
        try:
            pref = a.get("prefer_server_stream_params", fallback="true").strip().lower()
            self.prefer_server_stream_params = (pref in ("1", "true", "yes", "on"))
        except Exception:
            self.prefer_server_stream_params = True
        try:
            self.log.info(
                "Agente iniciado: id=%s, tc_id=%s, central_url=%s",
                self.agent_id,
                self.tc_id,
                self.central_url,
            )
        except Exception:
            pass
        # Fila local durável
        try:
            self.local_queue = LocalQueue(LOGS_DIR / "agent_queue.db")
        except Exception:
            self.local_queue = None
        # Thresholds de severidade (health)
        def _getint(key: str, default: int) -> int:
            try:
                return int(a.get(key, fallback=str(default)))
            except Exception:
                return default
        self.sev_warn_events = _getint("severity_warn_events", 1)
        self.sev_crit_events = _getint("severity_crit_events", 100)
        try:
            # Nome amigável se vier na config (opcional)
            self.tc_name = a.get("tc_name", fallback=f"TC{self.tc_id}")
        except Exception:
            self.tc_name = f"TC{self.tc_id}"
        self.sev_warn_sessions = _getint("severity_warn_sessions", 1)
        self.sev_crit_sessions = _getint("severity_crit_sessions", 3)
        self.sev_crit_last_sync_ms = _getint("severity_crit_last_sync_ms", 15*60*1000)
        # Shadow local_id para journaling da sessão atual
        self._shadow_local_id: int | None = None
        # Estado de conectividade com o Central
        self._central_online: bool = False
        # Monitoramento de camera quando ocioso
        try:
            self._camera_check_interval = float(os.getenv("CAMERA_IDLE_CHECK_SEC", "10"))
        except Exception:
            self._camera_check_interval = 10.0
        if self._camera_check_interval < 2.0:
            self._camera_check_interval = 2.0
        try:
            self._camera_open_timeout_ms = int(os.getenv("CAMERA_OPEN_TIMEOUT_MS", "2000"))
        except Exception:
            self._camera_open_timeout_ms = 2000
        try:
            self._camera_read_timeout_ms = int(os.getenv("CAMERA_READ_TIMEOUT_MS", "2000"))
        except Exception:
            self._camera_read_timeout_ms = 2000
        self._camera_last_check_ts = 0.0
        self._camera_status = None
        self._camera_source_path = None
        self._probe_lock = threading.Lock()
        self._probe_inflight = False
        self._probe_thread = None

    def _probe_camera_idle(self, central_client) -> str | None:
        try:
            if self.cp and getattr(self.cp, "session_active", False):
                return "camera_offline" if getattr(self.cp, "camera_lost", False) else "camera_online"
        except Exception:
            pass
        now = time.time()
        if (now - self._camera_last_check_ts) < self._camera_check_interval:
            return self._camera_status
        with self._probe_lock:
            if self._probe_inflight:
                if self._probe_thread and not self._probe_thread.is_alive():
                    self._probe_inflight = False
                else:
                    return self._camera_status
            self._probe_inflight = True
            self._camera_last_check_ts = now

        def _worker():
            path = ""
            try:
                cfg = central_client.get_config(self.tc_id) or {}
                path = (cfg.get("path") or "").strip()
                if path:
                    self._camera_source_path = path
            except Exception:
                path = self._camera_source_path or ""
            if not path or not str(path).lower().startswith("rtsp"):
                self._camera_status = None
                with self._probe_lock:
                    self._probe_inflight = False
                return
            cap = None
            ok = False
            try:
                try:
                    timeout_us = int(os.getenv("CAMERA_FFMPEG_STIMEOUT_US", str(int(self._camera_open_timeout_ms) * 1000)))
                    if timeout_us > 0:
                        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"stimeout;{timeout_us}"
                except Exception:
                    pass
                cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
                try:
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self._camera_open_timeout_ms))
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self._camera_read_timeout_ms))
                except Exception:
                    pass
                if cap and cap.isOpened():
                    ret, frame = cap.read()
                    ok = bool(ret and frame is not None)
            except Exception:
                ok = False
            finally:
                try:
                    if cap:
                        cap.release()
                except Exception:
                    pass
            self._camera_status = "camera_online" if ok else "camera_offline"
            with self._probe_lock:
                self._probe_inflight = False

        t = threading.Thread(target=_worker, daemon=True)
        self._probe_thread = t
        t.start()
        return self._camera_status

    def _parse_roi(self, roi_val):
        if not roi_val:
            return None
        s = str(roi_val).strip()
        # aceita JSON simples {x:..,y:..,w:..,h:..} ou "x,y,w,h"
        if "," in s and "{" not in s:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) == 4:
                try:
                    return tuple(int(p) for p in parts)
                except Exception:
                    return None
        try:
            import json
            obj = json.loads(s)
            x = int(obj.get("x")); y = int(obj.get("y")); w = int(obj.get("w")); h = int(obj.get("h"))
            return (x, y, w, h)
        except Exception:
            return None

    def _init_capture_point(self, central, source_type: str | None = None, file_path: str | None = None):
        cfg = central.get_config(self.tc_id) or {}
        ct_info = {"id": self.tc_id, "name": cfg.get("name") or f"TC{self.tc_id}"}
        # adapta ROI
        roi = self._parse_roi(cfg.get("roi"))
        capture_cfg = {
            "source_type": cfg.get("source_type", "rtsp"),
            "path": cfg.get("path", ""),
            "roi": roi,
            "model": cfg.get("model", "sacaria_yolov5n.pt"),
            "line_offset_red": cfg.get("line_offset_red", 40),
            "line_offset_blue": cfg.get("line_offset_blue", -40),
            "flow_mode": cfg.get("flow_mode", "cima"),
            "max_lost": cfg.get("max_lost", 2),
            "match_dist": cfg.get("match_dist", 150),
            "min_conf": cfg.get("min_conf", 0.8),
            "missed_frame_dir": cfg.get("missed_frame_dir"),
        }
        # Overrides de origem enviados pelo Central (sessão com arquivo local)
        if source_type == "file" and file_path:
            capture_cfg["source_type"] = "file"
            capture_cfg["path"] = file_path
            try:
                self.log.info("Override de origem para TC %s: file='%s'", self.tc_id, file_path)
            except Exception:
                pass
        else:
            try:
                self.log.info("Origem efetiva para TC %s: %s -> %s", self.tc_id, capture_cfg.get("source_type"), capture_cfg.get("path"))
            except Exception:
                pass

        # Ajusta streaming via Central se habilitado
        if getattr(self, 'prefer_server_stream_params', True):
            try:
                sfps = cfg.get("stream_fps")
                squa = cfg.get("stream_quality")
                if sfps is not None:
                    v = int(sfps)
                    if 1 <= v <= 60:
                        self.stream_fps = v
                if squa is not None:
                    q = int(squa)
                    if 30 <= q <= 95:
                        self.jpeg_quality = q
                try:
                    self.log.info("Aplicando streaming do servidor: fps=%s, qualidade=%s", self.stream_fps, self.jpeg_quality)
                except Exception:
                    pass
            except Exception:
                pass

        self.cp = CapturePoint(ct_info, capture_cfg)
        # callbacks para persistir e/ou enviar ao servidor central

        def _on_create_session(ct_id, lote_val, alvo):
            # Tenta criar no central; se falhar e houver fila local, cria sessão local negativa
            t0_cs = time.time()
            sid = central.session_start(tc_id=ct_id, lote=lote_val, contagem_alvo=alvo)
            t_cs = time.time() - t0_cs
            if sid:
                try:
                    self.log.info("Sessao remota criada: tc=%s (%s) id=%s em %.2fs", ct_id, getattr(self, "tc_name", ""), sid, t_cs)
                except Exception:
                    pass
                # Journaling: cria sombra local apontando para a sessão remota
                if self.local_queue:
                    try:
                        self._shadow_local_id = self.local_queue.ensure_shadow_session_remote(ct_id, lote_val or "", alvo, int(sid))
                    except Exception:
                        self._shadow_local_id = None
                return sid
            else:
                try:
                    self.log.warning("Sessao remota NAO criada: tc=%s (%s) em %.2fs (offline?)", ct_id, getattr(self, "tc_name", ""), t_cs)
                except Exception:
                    pass
            if self.local_queue:
                try:
                    self._shadow_local_id = self.local_queue.create_local_session(ct_id, lote_val or "", alvo)
                    return self._shadow_local_id
                except Exception:
                    self._shadow_local_id = None
                    return None
            return None

        def _on_insert_log(session_id, ct_id, delta, total):
            online = False
            try:
                online = bool(isinstance(session_id, int) and session_id >= 0)
            except Exception:
                online = False
            # Journaling local (sempre que possível)
            try:
                if self.local_queue and self._shadow_local_id is not None:
                    self.local_queue.enqueue_event(self._shadow_local_id, delta, total, mark_sent=online)
            except Exception:
                pass
            # Envio online (idempotente) quando houver sessão remota
            if online:
                try:
                    central.session_update(tc_id=ct_id, session_db_id=int(session_id), increment=delta, total=total, event_id=str(uuid4()))
                except Exception:
                    pass

        def _on_finish_session(session_id, total, status='finalizado', observacao=None):
            online = False
            try:
                online = bool(isinstance(session_id, int) and session_id >= 0)
            except Exception:
                online = False
            # Journaling local
            try:
                if self.local_queue and self._shadow_local_id is not None:
                    self.local_queue.mark_finish(self._shadow_local_id, total, observacao, status=status, mark_sent=online)
            except Exception:
                pass
            # Envio online
            if online:
                try:
                    t0_fn = time.time()
                    camera_alert = observacao if status == "erro_camera" else None
                    central.session_finish(tc_id=self.tc_id, session_db_id=int(session_id), total=total, observacao=observacao, status=status, camera_alert=camera_alert)
                    self.log.info("Finish central tc=%s (%s) id=%s total=%s em %.2fs", self.tc_id, getattr(self, "tc_name", ""), session_id, total, time.time()-t0_fn)
                except Exception as e:
                    self.log.warning("Finish central falhou tc=%s (%s) id=%s: %s", self.tc_id, getattr(self, "tc_name", ""), session_id, e)

        self.cp.on_create_session = _on_create_session
        self.cp.on_insert_log = _on_insert_log
        self.cp.on_finish_session = _on_finish_session
        def _on_pause_session(session_id, motivo=None):
            try:
                if session_id is None:
                    return
                central.session_pause(tc_id=self.tc_id, session_db_id=int(session_id), motivo=motivo)
            except Exception:
                pass
        self.cp.on_pause_session = _on_pause_session

    def _start_worker(self, central):
        def run():
            while not self.stop_event.is_set():
                try:
                    # processamento ocorre dentro do CapturePoint (thread interna)
                    # Worker de replicação: reenvia pendências quando possível
                    try:
                        if self.local_queue:
                            self.local_queue.sync_to_central(central)
                    except Exception:
                        pass
                    time.sleep(0.5)
                except Exception:
                    time.sleep(1)
        self.stop_event.clear()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def start_loop(self, lote: str = None, contagem_alvo: int | None = None, source_type: str | None = None, file_path: str | None = None):
        # Busca configuração da TC no servidor central
        verify_opt = (self.central_ca or self.central_verify)
        central = CentralClient(self.central_url, self.token, agent_id=self.agent_id, verify=verify_opt)
        self._init_capture_point(central, source_type=source_type, file_path=file_path)
        t0 = time.time()
        self.log.info("START loop: preparando sessao tc=%s (%s) lote=%s alvo=%s", self.tc_id, getattr(self, "tc_name", ""), lote, contagem_alvo)
        self.cp.start_session(lote=lote or "", contagem_alvo=contagem_alvo)
        self._start_worker(central)
        self.log.info("START loop concluido tc=%s (%s) em %.2fs", self.tc_id, getattr(self, "tc_name", ""), time.time() - t0)

    def resume_existing_session(self, session_id: int, lote: str, contagem_alvo: int | None = None, total_atual: int | None = None):
        verify_opt = (self.central_ca or self.central_verify)
        central = CentralClient(self.central_url, self.token, agent_id=self.agent_id, verify=verify_opt)
        self._init_capture_point(central)
        if self.local_queue and session_id is not None:
            try:
                self._shadow_local_id = self.local_queue.ensure_shadow_session_remote(self.tc_id, lote or "", contagem_alvo, int(session_id))
            except Exception:
                self._shadow_local_id = None
        self.log.info("RESUME loop: reanexando sessao tc=%s (%s) id=%s lote=%s", self.tc_id, getattr(self, "tc_name", ""), session_id, lote)
        self.cp.attach_session(session_db_id=session_id, lote=lote or "", contagem_alvo=contagem_alvo, current_total=total_atual)
        self._start_worker(central)

    def stop_loop(self, observacao: str | None = None):
        t0 = time.time()
        try:
            if self.cp:
                self.log.info("STOP solicitado (agent) para %s (%s), obs=%s", self.tc_id, getattr(self, "tc_name", ""), bool(observacao))
                self.cp.stop_session(observacao=observacao)
                self.cp.release()
                self.log.info("STOP concluido (agent) para %s (%s)", self.tc_id, getattr(self, "tc_name", ""))
        except Exception as e:
            self.log.error("Erro ao finalizar sessao local para %s (%s): %s", self.tc_id, getattr(self, "tc_name", ""), e, exc_info=True)
        finally:
            self.stop_event.set()
            try:
                with self._start_lock:
                    self._start_inflight = False
            except Exception:
                pass
            try:
                with self._jpeg_lock:
                    self._last_jpeg = None
                    self._last_jpeg_ts = 0.0
            except Exception:
                pass
            try:
                self._shadow_local_id = None
            except Exception:
                pass
        self.log.info("STOP loop tc=%s (%s) finalizado em %.2fs", self.tc_id, getattr(self, "tc_name", ""), time.time() - t0)

        # Marca parada também no central se necessário (já feito via callback no stop_session)



def create_agent_app(cfg_path: str | None = None):
    _configure_logging()
    app = Flask(__name__)
    # Resolve arquivo de configuração do agente
    import os
    if not cfg_path:
        cfg_path = os.getenv("AGENT_INI")
    if not cfg_path:
        if Path("agent.ini").is_file():
            cfg_path = "agent.ini"
        elif Path("windows_service.ini").is_file():
            cfg_path = "windows_service.ini"
        else:
            cfg_path = "agent.ini"  # padrão
    service = AgentService(Path(cfg_path))
    try:
        logging.getLogger("agent").info("Usando config do agente em: %s", Path(cfg_path).resolve())
    except Exception:
        pass

    def _best_host() -> str | None:
        try:
            name = socket.gethostname()
            # tenta IPs conhecidos da máquina
            try:
                candidates = socket.gethostbyname_ex(name)[2]
            except Exception:
                candidates = []
            # prioriza redes privadas comuns
            def is_private(ip: str) -> bool:
                return (
                    ip.startswith("10.") or
                    ip.startswith("192.168.") or
                    any(ip.startswith(f"172.{i}.") for i in range(16,32))
                ) and ip != "127.0.0.1"
            for ip in candidates:
                if is_private(ip):
                    return ip
            # fallback: primeiro candidato não loopback
            for ip in candidates:
                if ip != "127.0.0.1":
                    return ip
            return name
        except Exception:
            return None

    # thread de heartbeat
    def _hb():
        verify_opt = (service.central_ca or service.central_verify)
        central = CentralClient(service.central_url, service.token, agent_id=service.agent_id, verify=verify_opt)
        while True:
            try:
                host_for_central = _best_host() or socket.gethostname()
                cam_status = service._probe_camera_idle(central)
                hb_status = cam_status or 'running'
                central.heartbeat(tc_id=service.tc_id, hostname=f'{host_for_central}:{service.http_port}', status=hb_status, version='agent-1')
                service._central_online = True
            except Exception as e:
                try:
                    logging.getLogger("agent").warning(
                        "Falha no heartbeat para %s: %s", service.central_url, e
                    )
                except Exception:
                    pass
                service._central_online = False
            time.sleep(5)

    threading.Thread(target=_hb, daemon=True).start()

    @app.route("/api/agent/v1/status")
    def status():
        return jsonify({
            "agent_id": service.agent_id,
            "tc_id": service.tc_id,
            "running": service.thread is not None and service.thread.is_alive(),
        })

    @app.route("/health")
    def health():
        try:
            running = service.thread is not None and service.thread.is_alive()
        except Exception:
            running = False
        try:
            age_ms = int((time.time() - service._last_jpeg_ts) * 1000) if getattr(service, "_last_jpeg_ts", 0) else None
        except Exception:
            age_ms = None
        return jsonify({
            "ok": True,
            "agent_id": service.agent_id,
            "tc_id": service.tc_id,
            "running": running,
            "stream_fps": getattr(service, "stream_fps", None),
            "jpeg_quality": getattr(service, "jpeg_quality", None),
            "last_jpeg_age_ms": age_ms,
            "version": "agent-1",
        })

    @app.route("/api/agent/v1/command/start", methods=["POST"])
    def cmd_start():
        data = request.get_json(silent=True) or {}
        lote = data.get("lote")
        contagem_alvo = data.get("contagem_alvo")
        source_type = data.get("source_type")
        file_path = data.get("file_path")
        try:
            logging.getLogger("agent").info(
                "Comando START recebido: tc=%s (%s) lote=%s alvo=%s source_type=%s file_path=%s",
                service.tc_id, getattr(service, "tc_name", ""), lote, contagem_alvo, source_type, file_path
            )
        except Exception:
            pass
        # Evita dupla inicializacao e STARTs repetidos em curto intervalo
        now = time.time()
        with service._start_lock:
            if service.thread is not None and service.thread.is_alive():
                return jsonify({"ok": True, "running": True}), 200
            if service._start_inflight:
                return jsonify({"ok": True, "running": True, "ignored": "start_inflight"}), 200
            if (now - service._last_start_ts) < service.start_cooldown_sec:
                return jsonify({"ok": True, "running": True, "ignored": "debounce"}), 200
            service._start_inflight = True
            service._last_start_ts = now
        # Dispara em background para responder rapido
        def _start_async():
            try:
                service.start_loop(lote=lote, contagem_alvo=contagem_alvo, source_type=source_type, file_path=file_path)
                logging.getLogger("agent").info("START disparado em background para tc=%s (%s)", service.tc_id, getattr(service, "tc_name", ""))
            except Exception as e:
                logging.getLogger("agent").error("Falha ao iniciar contagem tc=%s (%s): %s", service.tc_id, getattr(service, "tc_name", ""), e, exc_info=True)
            finally:
                try:
                    with service._start_lock:
                        service._start_inflight = False
                except Exception:
                    pass
        threading.Thread(target=_start_async, daemon=True).start()
        return jsonify({"ok": True, "running": True}), 200

    @app.route("/api/agent/v1/command/stop", methods=["POST"])
    def cmd_stop():
        data = request.get_json(silent=True) or {}
        obs = data.get("observacao")
        try:
            logging.getLogger("agent").info("Comando STOP recebido: tc=%s (%s) obs=%s", service.tc_id, getattr(service, "tc_name", ""), bool(obs))
            service.stop_loop(observacao=obs)
        except Exception as e:
            logging.getLogger("agent").error("Falha ao processar STOP tc=%s (%s): %s", service.tc_id, getattr(service, "tc_name", ""), e, exc_info=True)
        return jsonify({"ok": True})

    @app.route("/api/agent/v1/command/pause", methods=["POST"])
    def cmd_pause():
        cp = service.cp
        try:
            if cp:
                cp.pause_session()
                logging.getLogger("agent").info("Comando PAUSE aplicado: tc=%s (%s)", service.tc_id, getattr(service, "tc_name", ""))
            else:
                logging.getLogger("agent").warning("Comando PAUSE ignorado: sem cp/tc ativa (tc=%s)", service.tc_id)
        except Exception as e:
            logging.getLogger("agent").error("Falha no PAUSE tc=%s (%s): %s", service.tc_id, getattr(service, "tc_name", ""), e, exc_info=True)
        return jsonify({"ok": True})

    @app.route("/api/agent/v1/command/resume", methods=["POST"])
    def cmd_resume():
        cp = service.cp
        try:
            if cp:
                if getattr(cp, "camera_lost", False):
                    logging.getLogger("agent").warning("Comando RESUME bloqueado: camera offline tc=%s", service.tc_id)
                    return jsonify({"ok": False, "error": "camera_offline"}), 409
                cp.resume_session()
                logging.getLogger("agent").info("Comando RESUME aplicado: tc=%s (%s)", service.tc_id, getattr(service, "tc_name", ""))
            else:
                verify_opt = (service.central_ca or service.central_verify)
                central = CentralClient(service.central_url, service.token, agent_id=service.agent_id, verify=verify_opt)
                data = central.get_active_session(service.tc_id) or {}
                sess = data.get("session") if isinstance(data, dict) else None
                if sess and sess.get("id") is not None:
                    service.resume_existing_session(
                        session_id=int(sess.get("id")),
                        lote=sess.get("lote") or "",
                        contagem_alvo=sess.get("contagem_alvo"),
                        total_atual=sess.get("total_atual"),
                    )
                    logging.getLogger("agent").info("Comando RESUME aplicado (reativo): tc=%s (%s)", service.tc_id, getattr(service, "tc_name", ""))
                else:
                    logging.getLogger("agent").warning("Comando RESUME ignorado: sem cp/tc ativa (tc=%s)", service.tc_id)
        except Exception as e:
            logging.getLogger("agent").error("Falha no RESUME tc=%s (%s): %s", service.tc_id, getattr(service, "tc_name", ""), e, exc_info=True)
        return jsonify({"ok": True})

    @app.route("/api/agent/v1/snapshot.jpg")
    def snapshot():
        cp = service.cp
        frame = None
        # 1) Tenta usar frame da sessão ativa
        if cp and getattr(cp, "session_active", False):
            frame = getattr(cp, "last_vis_frame", None)
            if frame is None and getattr(cp, "camera", None) is not None:
                try:
                    ret, raw = cp.camera.get_frame()
                    if ret:
                        frame = raw
                except Exception:
                    frame = None
        # 2) Fallback: abre fonte rapidamente para um snapshot mesmo sem sessão
        if frame is None:
            try:
                central = CentralClient(service.central_url, service.token, agent_id=service.agent_id, verify=(service.central_ca or service.central_verify))
                cfg = central.get_config(service.tc_id) or {}
                src = cfg.get("path") or ""
                if src:
                    from services.video_source import VideoSource
                    now = time.time()
                    cam = None
                    # Reutiliza câmera cacheada por até 30 segundos
                    try:
                        if (service._snap_cam is not None and
                            service._snap_cam_src == src and
                            (now - service._snap_cam_opened_ts) < 30.0):
                            cam = service._snap_cam
                        else:
                            # Fecha anterior, se houver
                            try:
                                if service._snap_cam is not None:
                                    service._snap_cam.release()
                            except Exception:
                                pass
                            service._snap_cam = VideoSource(src)
                            service._snap_cam_src = src
                            service._snap_cam_opened_ts = time.time()
                            cam = service._snap_cam
                        # Tenta várias amostras (até ~3s)
                        for _ in range(30):
                            ret, raw = cam.get_frame()
                            if ret and raw is not None:
                                frame = raw
                                break
                            time.sleep(0.1)
                    except Exception:
                        frame = None
            except Exception:
                frame = None
        if frame is None:
            return ("no frame", 503)
        ok, buf = cv2.imencode('.jpg', frame)
        if not ok:
            return ("encode error", 500)
        return app.response_class(buf.tobytes(), mimetype='image/jpeg')

    @app.route("/api/agent/v1/video")
    def video():
        cp = service.cp
        if not cp or not getattr(cp, "session_active", False):
            return ("no active session", 404)
        def gen():
            import cv2
            interval = max(0.01, 1.0 / float(service.stream_fps or 12))
            enc_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(service.jpeg_quality or 80)]
            while True:
                try:
                    if not getattr(cp, "session_active", False):
                        break
                    frame = getattr(cp, "last_vis_frame", None)
                    if frame is None and getattr(cp, "camera", None) is not None:
                        ret, raw = cp.camera.get_frame()
                        if ret:
                            frame = raw
                    if frame is None:
                        time.sleep(interval)
                        continue
                    now = time.time()
                    data = None
                    with service._jpeg_lock:
                        if service._last_jpeg is None or (now - service._last_jpeg_ts) >= interval:
                            ok, buf = cv2.imencode('.jpg', frame, enc_params)
                            if ok:
                                service._last_jpeg = buf.tobytes()
                                service._last_jpeg_ts = now
                        data = service._last_jpeg
                    if data is None:
                        time.sleep(interval)
                        continue
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + data + b'\r\n')
                    time.sleep(interval)
                except GeneratorExit:
                    break
                except Exception:
                    time.sleep(interval)
        return app.response_class(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route("/api/agent/v1/health")
    def agent_health():
        stats = {}
        try:
            if service.local_queue:
                stats = service.local_queue.count_pending()
                stats["last_sync_ms"] = service.local_queue.last_sync_ms
                stats["last_compact_ms"] = service.local_queue.last_compact_ms
        except Exception:
            stats = {"error": "queue_unavailable"}
        # Severidade (heurística simples)
        try:
            now_ms = int(time.time() * 1000)
            last_sync_age_ms = None
            if isinstance(stats.get("last_sync_ms"), int):
                last_sync_age_ms = max(0, now_ms - int(stats.get("last_sync_ms")))
            last_compact_age_ms = None
            if isinstance(stats.get("last_compact_ms"), int):
                last_compact_age_ms = max(0, now_ms - int(stats.get("last_compact_ms")))
            # ISO helpers
            try:
                from datetime import datetime
                if isinstance(stats.get("last_sync_ms"), int):
                    stats["last_sync_iso"] = datetime.fromtimestamp(int(stats.get("last_sync_ms"))/1000.0).isoformat()
                else:
                    stats["last_sync_iso"] = None
                if isinstance(stats.get("last_compact_ms"), int):
                    stats["last_compact_iso"] = datetime.fromtimestamp(int(stats.get("last_compact_ms"))/1000.0).isoformat()
                else:
                    stats["last_compact_iso"] = None
            except Exception:
                stats["last_sync_iso"] = None
                stats["last_compact_iso"] = None
            ev = int(stats.get("events_pending", 0))
            sess_no_remote = int(stats.get("sessions_without_remote", 0))
            sess_finish = int(stats.get("sessions_finish_pending", 0))
            severity = "ok"
            if ev > 0 or sess_no_remote > 0 or sess_finish > 0:
                severity = "atencao"
            if (ev >= self.sev_crit_events or
                sess_no_remote >= self.sev_crit_sessions or
                sess_finish >= self.sev_crit_sessions):
                severity = "critico"
            if last_sync_age_ms is not None and last_sync_age_ms > self.sev_crit_last_sync_ms:
                severity = "critico"
            # Anexa idades ao payload
            stats["last_sync_age_ms"] = last_sync_age_ms
            stats["last_compact_age_ms"] = last_compact_age_ms
        except Exception:
            severity = "desconhecido"

        # Sessão atual
        current = {}
        try:
            cp = service.cp
            if cp and getattr(cp, "session_active", False):
                current = {
                    "online": bool(getattr(cp, "session_db_id", None)),
                    "remote_session_id": getattr(cp, "session_db_id", None),
                    "local_shadow_id": service._shadow_local_id,
                    "count": getattr(cp, "current_session_count", None),
                    "lote": getattr(cp, "session_lote", None),
                }
            else:
                current = {"online": False, "remote_session_id": None, "local_shadow_id": None}
        except Exception:
            current = {"error": "unavailable"}

        return {
            "ok": True,
            "tc_id": service.tc_id,
            "pending": stats,
            "severity": severity,
            "current_session": current,
        }, 200

    @app.route("/api/agent/v1/sync", methods=["POST"])
    def sync_now():
        if not service.local_queue:
            return {"ok": False, "error": "queue_unavailable"}, 503
        # Snapshot antes
        before = service.local_queue.count_pending()
        # Usa um cliente fresco para garantir sessão HTTP válida
        central = CentralClient(service.central_url, service.token, agent_id=service.agent_id, verify=(service.central_ca or service.central_verify))
        try:
            service.local_queue.sync_to_central(central)
        except Exception as e:
            return {"ok": False, "error": str(e), "before": before}, 500
        after = service.local_queue.count_pending()
        return {"ok": True, "before": before, "after": after, "last_sync_ms": service.local_queue.last_sync_ms}, 200

    @app.route("/api/agent/v1/pending")
    def list_pending():
        if not service.local_queue:
            return {"ok": False, "error": "queue_unavailable"}, 503
        try:
            limit = int(request.args.get("limit", "50"))
        except Exception:
            limit = 50
        try:
            data = service.local_queue.list_pending(events_limit=limit)
            return {"ok": True, "data": data}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    @app.route("/api/agent/v1/compact", methods=["POST"])
    def compact_queue():
        if not service.local_queue:
            return {"ok": False, "error": "queue_unavailable"}, 503
        payload = request.get_json(silent=True) or {}
        hard = False
        try:
            hard = bool(payload.get("hard") in (True, 1, "1", "true", "yes", "on"))
        except Exception:
            hard = False
        try:
            result = service.local_queue.compact(hard=hard)
            return {"ok": True, **result}, 200
        except Exception as e:
            return {"ok": False, "error": str(e)}, 500

    return app


if __name__ == "__main__":
    app = create_agent_app()
    app.run(host="0.0.0.0", port=9090, debug=True, use_reloader=False, threaded=True)



