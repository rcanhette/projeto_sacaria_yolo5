# agent_app.py
import logging
import threading
import time
from pathlib import Path
from logging.config import dictConfig
from flask import Flask, jsonify, request
import socket
import cv2

# Reuso do CapturePoint local
from services.capture_point import CapturePoint

# Config via INI simples (um arquivo por agente)
import configparser
import requests

LOGS_DIR = Path(__file__).resolve().parent / "logs"


class CentralClient:
    def __init__(self, base_url: str, token: str, agent_id: str):
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
                    total=3,
                    connect=3,
                    read=3,
                    status=3,
                    backoff_factor=0.3,  # ~0.3s, 0.6s, 1.2s
                    status_forcelist=(429, 502, 503, 504),
                    allowed_methods=("GET", "POST"),
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
            else:
                adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50)
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)
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
            r = self.session.post(url, json=payload, headers=self._headers(), timeout=10)
            r.raise_for_status()
            data = r.json()
            return data.get("session_db_id")
        except Exception:
            return None

    def session_update(self, tc_id: int, session_db_id: int, increment: int, total: int):
        url = f"{self.base_url}/api/agent/v1/session/update"
        payload = {
            "agent_id": self.agent_id,
            "tc_id": tc_id,
            "session_db_id": session_db_id,
            "increment": increment,
            "total": total,
        }
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=5)
        except Exception:
            pass

    def session_finish(self, tc_id: int, session_db_id: int, total: int, observacao: str | None = None):
        url = f"{self.base_url}/api/agent/v1/session/finish"
        payload = {
            "agent_id": self.agent_id,
            "tc_id": tc_id,
            "session_db_id": session_db_id,
            "total": total,
            "observacao": observacao,
        }
        try:
            self.session.post(url, json=payload, headers=self._headers(), timeout=10)
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
        self.cp = None
        self.thread = None
        self.stop_event = threading.Event()
        # Optional streaming parameters
        try:
            self.stream_fps = max(1, int(a.get("stream_fps", fallback="12")))
        except Exception:
            self.stream_fps = 12
        try:
            self.jpeg_quality = min(95, max(30, int(a.get("jpeg_quality", fallback="80"))))
        except Exception:
            self.jpeg_quality = 80
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

    def start_loop(self, lote: str = None, contagem_alvo: int | None = None, source_type: str | None = None, file_path: str | None = None):
        # Busca configuração da TC no servidor central
        central = CentralClient(self.central_url, self.token, agent_id=self.agent_id)
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
        # callbacks para enviar ao servidor central

        def _on_create_session(ct_id, lote_val, alvo):
            return central.session_start(tc_id=ct_id, lote=lote_val, contagem_alvo=alvo)

        def _on_insert_log(session_id, ct_id, delta, total):
            central.session_update(tc_id=ct_id, session_db_id=session_id, increment=delta, total=total)

        def _on_finish_session(session_id, total, status='finalizado', observacao=None):
            central.session_finish(tc_id=self.tc_id, session_db_id=session_id, total=total, observacao=observacao)

        self.cp.on_create_session = _on_create_session
        self.cp.on_insert_log = _on_insert_log
        self.cp.on_finish_session = _on_finish_session
        self.cp.start_session(lote=lote or "", contagem_alvo=contagem_alvo)
        def run():
            while not self.stop_event.is_set():
                try:
                    # processamento ocorre dentro do CapturePoint (thread interna)
                    time.sleep(0.5)
                except Exception:
                    time.sleep(1)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop_loop(self, observacao: str | None = None):
        try:
            if self.cp:
                self.cp.stop_session(observacao=observacao)
                self.cp.release()
        finally:
            self.stop_event.set()
            try:
                with self._jpeg_lock:
                    self._last_jpeg = None
                    self._last_jpeg_ts = 0.0
            except Exception:
                pass

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
        central = CentralClient(service.central_url, service.token, agent_id=service.agent_id)
        while True:
            try:
                host_for_central = _best_host() or socket.gethostname()
                central.heartbeat(tc_id=service.tc_id, hostname=host_for_central, status="running", version="agent-1")
            except Exception as e:
                try:
                    logging.getLogger("agent").warning(
                        "Falha no heartbeat para %s: %s", service.central_url, e
                    )
                except Exception:
                    pass
            time.sleep(10)

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
                "Comando START recebido: lote=%s, alvo=%s, source_type=%s, file_path=%s",
                lote, contagem_alvo, source_type, file_path
            )
        except Exception:
            pass
        # Evita dupla inicializacao
        if service.thread is not None and service.thread.is_alive():
            return jsonify({"ok": True, "running": True}), 200
        # Dispara em background para responder rapido
        def _start_async():
            try:
                service.start_loop(lote=lote, contagem_alvo=contagem_alvo, source_type=source_type, file_path=file_path)
            except Exception:
                pass
        threading.Thread(target=_start_async, daemon=True).start()
        return jsonify({"ok": True, "running": True}), 200

    @app.route("/api/agent/v1/command/stop", methods=["POST"])
    def cmd_stop():
        data = request.get_json(silent=True) or {}
        obs = data.get("observacao")
        service.stop_loop(observacao=obs)
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
                central = CentralClient(service.central_url, service.token, agent_id=service.agent_id)
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

    return app


if __name__ == "__main__":
    app = create_agent_app()
    app.run(host="0.0.0.0", port=9090, debug=True, use_reloader=False, threaded=True)
