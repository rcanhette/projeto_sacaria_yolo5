# services/capture_point.py

import cv2

import time

import threading

import logging

import os

from datetime import datetime

from services.industrial_tag_detector import IndustrialTagDetector
from services.video_source import VideoSource
from services.session_repository import pause_session as _pause_session_db, resume_session as _resume_session_db

# Em ambientes "agente" o módulo de repositório de sessão (que depende de DB)
# pode não estar disponível. Fazemos import tolerante e definimos stubs que
# disparam exceção apenas quando usados, permitindo callbacks assumirem.
try:
    from services.session_repository import (
        create_session as _create_session_impl,
        insert_log as _insert_log_impl,
        finish_session as _finish_session_impl,
    )
except Exception:
    _create_session_impl = None
    _insert_log_impl = None
    _finish_session_impl = None

def create_session(*args, **kwargs):
    if _create_session_impl is None:
        raise RuntimeError("session_repository indisponivel (modo agente)")
    return _create_session_impl(*args, **kwargs)

def insert_log(*args, **kwargs):
    if _insert_log_impl is None:
        raise RuntimeError("session_repository indisponivel (modo agente)")
    return _insert_log_impl(*args, **kwargs)

def finish_session(*args, **kwargs):
    if _finish_session_impl is None:
        raise RuntimeError("session_repository indisponivel (modo agente)")
    return _finish_session_impl(*args, **kwargs)

log = logging.getLogger(__name__)

class CapturePoint:

    def __init__(self, ct, config):

        self.ct = ct  # dict: {id, name, source_path, roi, model_path}

        self.default_source_type = config["source_type"]          # "rtsp"

        self.default_source_path = config["path"]                 # rtsp url

        self.roi_cfg = config.get("roi", None)                    # (x,y,w,h)

        self.model_path = config.get("model", "sacaria_yolov5n.pt")

        try:

            self.line_offset_red = int(config.get("line_offset_red", 40))

        except Exception:
            pass
            self.line_offset_red = 40

        try:

            self.line_offset_blue = int(config.get("line_offset_blue", -40))

        except Exception:
            pass
            self.line_offset_blue = -40

        flow_mode_cfg = str(config.get("flow_mode", "cima") or "cima").strip().lower()

        if flow_mode_cfg not in ("cima", "baixo", "sem_fluxo"):

            flow_mode_cfg = "cima"

        self.flow_mode = flow_mode_cfg

        self.missed_frame_dir = (config.get("missed_frame_dir") or "").strip() or None

        try:

            self.max_lost = int(config.get("max_lost", 2))

        except Exception:
            pass

            self.max_lost = 2

        try:

            self.match_dist = float(config.get("match_dist", 150))

        except Exception:
            pass

            self.match_dist = 150.0

        if self.match_dist <= 0:

            self.match_dist = 1.0

        try:

            self.min_conf = float(config.get("min_conf", 0.8))

        except Exception:

            self.min_conf = 0.8

        if self.min_conf < 0:

            self.min_conf = 0.0

        if self.min_conf > 1:

            self.min_conf = 1.0

        # fonte atual (pode ser file para testes na sesso corrente)

        self.source_type = self.default_source_type

        self.source_path = self.default_source_path

        self.camera = None

        self.detector = None

        self.thread = None

        self.stop_event = threading.Event()

        self.session_lock = threading.Lock()

        # estado de sessao
        self.session_active = False
        # novo estado de pausa (sessao aberta, mas sem contar)
        self.session_paused = False
        self.session_lote = None
        self.session_data = None
        self.session_hora_inicio = None
        self.session_hora_fim = None
        self.session_db_id = None   # <<< ID na tabela session
        self.session_contagem_alvo = None

        # monitoramento de camera
        try:
            self.camera_lost_timeout = float(os.getenv("CAMERA_LOST_TIMEOUT_SEC", "2.5"))
        except Exception:
            self.camera_lost_timeout = 5.0
        if self.camera_lost_timeout < 1.0:
            self.camera_lost_timeout = 1.0
        self.camera_lost = False
        self.camera_alert = None
        self._last_camera_ok_ts = None
        try:
            self.camera_idle_check_interval = float(os.getenv("CAMERA_IDLE_CHECK_SEC", "5"))
        except Exception:
            self.camera_idle_check_interval = 5.0
        if self.camera_idle_check_interval < 2.0:
            self.camera_idle_check_interval = 2.0
        self._last_camera_probe_ts = 0.0

        try:
            self.camera_reopen_interval = float(os.getenv("CAMERA_REOPEN_INTERVAL_SEC", "5"))
        except Exception:
            self.camera_reopen_interval = 5.0
        if self.camera_reopen_interval < 1.0:
            self.camera_reopen_interval = 1.0
        self._last_camera_reopen_ts = 0.0

        # contadores

        self.current_session_count = 0

        self._last_session_logged_total = None

        self._base_counter_snapshot = 0

        # ltimo frame anotado p/ /video

        self.last_vis_frame = None

    # ---------- recursos ----------

    def _open_sources(self):

        if self.camera:

            try: self.camera.release()

            except Exception: pass

            self.camera = None

        self.camera = VideoSource(self.source_path)

        if not self.camera or getattr(self.camera, "cap", None) is None:

            log.error("[CT%s] Falha ao abrir a fonte de vÃ­deo: %s", self.ct.get('id'), self.source_path)

        else:

            log.info("[CT%s] Fonte de vÃ­deo pronta: %s", self.ct.get('id'), self.source_path)

        roi_val = self.roi_cfg if self.roi_cfg is not None else (0, 0, 0, 0)
        self.detector = IndustrialTagDetector(

            self.model_path,

            roi=roi_val,

            cross_point_mode='meio',

            line_offset_red=self.line_offset_red,

            line_offset_blue=self.line_offset_blue,

            flow_mode=self.flow_mode,

            max_lost=self.max_lost,

            match_dist=self.match_dist,

            min_conf=self.min_conf,

            missed_frame_dir=self.missed_frame_dir,

            ct_id=self.ct.get('id'),

            ct_name=self.ct.get('name'),

        )

        log.info("[CT%s] Detector inicializado com modelo %s", self.ct.get('id'), getattr(self.detector, "model_path_for_load", self.model_path))

        if self.session_active and self.session_lote:

            try:

                self.detector.set_session_context(self.session_lote)

            except Exception:

                pass

        self._apply_cross_point_mode()

    def _apply_cross_point_mode(self):
        """Garante que o ponto de cruzamento permanece central."""
        try:
            if self.detector and getattr(self.detector, "cross_point_mode", None) != "meio":
                self.detector.cross_point_mode = "meio"
        except Exception:
            pass

    def _ensure_thread(self):

        self._apply_cross_point_mode()

        if self.thread and self.thread.is_alive():

            if self.camera_lost:
                self._maybe_reopen_camera(time.time(), reason="camera_lost")
            return

        if not self.camera or not self.detector or self.camera_lost:

            self._open_sources()

        def loop():

            while not self.stop_event.is_set():

                try:

                    if self.camera is None or self.detector is None:

                        self._open_sources()

                        if self.camera is None and self.session_active:
                            try:
                                now = time.monotonic()
                            except Exception:
                                now = time.time()
                            self._check_camera_timeout(now, has_frame=False)

                        time.sleep(0.05)

                        continue

                    ret, frame = self.camera.get_frame()

                    if not ret or frame is None:

                        try:
                            now = time.monotonic()
                        except Exception:
                            now = time.time()
                        self._check_camera_timeout(now, has_frame=False)
                        time.sleep(0.01)

                        continue

                    try:
                        now = time.monotonic()
                    except Exception:
                        now = time.time()
                    self._check_camera_timeout(now, has_frame=True)

                    vis, total_counter_abs = self.detector.detect_and_tag(frame)

                    self.last_vis_frame = vis

                    # Quando em pausa, mantemos a sessao mas nao geramos novos logs
                    if self.session_active and not getattr(self, "session_paused", False):

                        total_abs = getattr(self.detector, "counter", total_counter_abs)

                        rel_total = int(max(0, total_abs - self._base_counter_snapshot))

                        if rel_total != self.current_session_count:

                            self.current_session_count = rel_total

                            self._log_deltas(rel_total)

                    time.sleep(0.005)

                except Exception as e:

                    log.warning("[CT%s] Erro no loop de captura: %s", self.ct.get('id'), e, exc_info=True)

                    time.sleep(0.05)
        # Garante que o evento de parada esteja limpo antes de iniciar uma nova thread

        self.stop_event.clear()

        self.thread = threading.Thread(target=loop, daemon=True)

        self.thread.start()

    # ---------- sesso ----------

    def start_session(self, lote: str, contagem_alvo: int | None = None):

        # Evita corrida de START duplo (duplo clique ou chamadas concorrentes)

        with self.session_lock:

            if self.session_active or self.session_db_id is not None:

                return

            self._ensure_thread()

            try:

                detector = self.detector

                requested = getattr(detector, "model_path_requested", self.model_path)

                resolved = getattr(detector, "model_path_resolved", requested)

                load_path = getattr(detector, "model_path_for_load", resolved)

                exists = getattr(detector, "model_path_exists", None)

                status_txt = "desconhecido"

                if exists is True:

                    status_txt = "encontrado"

                elif exists is False:

                    status_txt = "nao encontrado"

                log_msg = (
                    f"[CT{self.ct['id']}] START lote='{lote}' "
                    f"source(type='{self.source_type}', path='{self.source_path}'), "
                    f"modelo='{load_path}' (solicitado='{requested}', resolvido='{resolved}', status={status_txt}), "
                    f"fluxo='{self.flow_mode}', offsets(red={self.line_offset_red}, azul={self.line_offset_blue}), "
                    f"max_lost={self.max_lost}, match_dist={self.match_dist}, min_conf={self.min_conf}, "
                    f"missed_dir='{self.missed_frame_dir or '-'}'"
                )

                if exists is False:

                    log.warning(log_msg)

                else:

                    log.info(log_msg)

            except Exception as log_err:

                log.warning("[CT%s] START lote='%s' - nao foi possvel registrar informacoes do modelo (%s)",

                            self.ct.get('id'), lote, log_err)

            agora = datetime.now()

            try:

                base = int(getattr(self.detector, "counter", 0))

            except Exception:

                base = 0

            # cria registro no banco e guarda o id (com fallback via hook)
            try:
                self.session_db_id = create_session(self.ct["id"], lote, contagem_alvo)
            except Exception as e:
                try:
                    cb = getattr(self, "on_create_session", None)
                    if callable(cb):
                        self.session_db_id = cb(self.ct["id"], lote, contagem_alvo)
                    else:
                        raise e
                except Exception:
                    log.error("[CT%s] Falha ao criar sessao: %s", self.ct.get('id'), e, exc_info=True)
                    self.session_db_id = None

            self.session_active = True
            self.session_paused = False
            self.session_lote = lote
            self.session_data = agora.strftime("%d/%m/%Y")
            self.session_hora_inicio = agora.strftime("%H:%M:%S")
            self.session_hora_fim = None
            self.session_contagem_alvo = int(contagem_alvo) if contagem_alvo is not None else None
            try:
                self._last_camera_ok_ts = time.monotonic()
            except Exception:
                self._last_camera_ok_ts = None
            self.camera_lost = False
            self.camera_alert = None

            if self.detector:

                try:

                    self.detector.set_session_context(lote)

                except Exception as log_err:

                    log.warning("[CT%s] Falha ao definir contexto da sessao para snapshots (%s)", self.ct.get('id'), log_err)

            self._base_counter_snapshot = base

            self.current_session_count = 0

            self._last_session_logged_total = 0

        # (no h mais cabealho em .txt  virou a linha da tabela `session`)

    def attach_session(self, session_db_id: int, lote: str, contagem_alvo: int | None = None, current_total: int | None = None):
        """Reanexa a uma sessao ja existente (ex.: agent reiniciou)."""
        with self.session_lock:
            if self.session_active or self.session_db_id is not None:
                return
            self._ensure_thread()
            try:
                agora = datetime.now()
                self.session_data = agora.strftime("%d/%m/%Y")
                self.session_hora_inicio = agora.strftime("%H:%M:%S")
            except Exception:
                pass
            self.session_db_id = int(session_db_id) if session_db_id is not None else None
            self.session_active = True
            self.session_paused = False
            self.session_lote = lote
            self.session_contagem_alvo = int(contagem_alvo) if contagem_alvo is not None else None
            try:
                self._last_camera_ok_ts = time.monotonic()
            except Exception:
                self._last_camera_ok_ts = None
            self.camera_lost = False
            self.camera_alert = None
            if self.detector:
                try:
                    self.detector.set_session_context(lote)
                except Exception as log_err:
                    log.warning("[CT%s] Falha ao definir contexto da sessao para snapshots (%s)", self.ct.get('id'), log_err)
            base_total = 0
            try:
                if current_total is not None:
                    base_total = int(current_total)
            except Exception:
                base_total = 0
            self._base_counter_snapshot = -base_total
            self.current_session_count = base_total
            self._last_session_logged_total = base_total
            log.info("[CT%s] Sessao reanexada (db_id=%s, lote=%s, total=%s)", self.ct.get('id'), self.session_db_id, lote, base_total)

    def _log_deltas(self, current_rel_total: int):

        if not self.session_active or self.session_db_id is None:

            return

        if self._last_session_logged_total is None:

            self._last_session_logged_total = current_rel_total

            return

        diff = current_rel_total - self._last_session_logged_total

        if diff == 0:

            return

        try:

            if diff > 0:

                for _ in range(diff):

                    self._last_session_logged_total += 1

                    try:
                        insert_log(
                            session_id=self.session_db_id,
                            ct_id=self.ct["id"],
                            delta=+1,
                            total_atual=self._last_session_logged_total
                        )
                    except Exception as e:
                        cb = getattr(self, "on_insert_log", None)
                        if callable(cb):
                            try:
                                cb(self.session_db_id, self.ct["id"], +1, self._last_session_logged_total)
                            except Exception:
                                log.error("[CT%s] Falha no callback on_insert_log: %s", self.ct.get('id'), e, exc_info=True)
                        else:
                            log.error("[CT%s] Falha ao registrar delta no banco: %s", self.ct.get('id'), e, exc_info=True)

            else:

                for _ in range(-diff):

                    self._last_session_logged_total -= 1

                    if self._last_session_logged_total < 0:

                        self._last_session_logged_total = 0

                    try:
                        insert_log(
                            session_id=self.session_db_id,
                            ct_id=self.ct["id"],
                            delta=-1,
                            total_atual=self._last_session_logged_total
                        )
                    except Exception as e:
                        cb = getattr(self, "on_insert_log", None)
                        if callable(cb):
                            try:
                                cb(self.session_db_id, self.ct["id"], -1, self._last_session_logged_total)
                            except Exception:
                                log.error("[CT%s] Falha no callback on_insert_log: %s", self.ct.get('id'), e, exc_info=True)
                        else:
                            log.error("[CT%s] Falha ao registrar delta no banco: %s", self.ct.get('id'), e, exc_info=True)

        except Exception as e:

            log.error("[CT%s] Falha ao registrar delta no banco: %s", self.ct.get('id'), e, exc_info=True)

    def stop_session(self, observacao: str | None = None, status: str = "finalizado"):
        """Finaliza a sessao corrente e libera recursos."""
        agora = datetime.now()
        lote_atual = self.session_lote
        total_final = int(self.current_session_count)

        try:
            if self.session_active:
                self.session_hora_fim = agora.strftime("%H:%M:%S")
                quantidade = int(self.current_session_count)
                total_final = quantidade
                try:
                    if self.session_db_id is not None:
                        finish_session(self.session_db_id, quantidade, status=status, observacao=observacao)
                except Exception as exc:
                    # fallback via hook
                    cb = getattr(self, "on_finish_session", None)
                    if callable(cb):
                        try:
                            cb(self.session_db_id, quantidade, status=status, observacao=observacao)
                        except Exception:
                            log.error("[CT%s] Erro no callback on_finish_session: %s", self.ct.get('id'), exc, exc_info=True)
                    else:
                        log.error("[CT%s] Erro ao finalizar sessao no banco: %s", self.ct.get('id'), exc, exc_info=True)
        finally:
            self.session_active = False
            self.session_paused = False
            self.session_lote = None
            self.session_data = None
            self.session_hora_inicio = None
            self.session_hora_fim = None
            self.session_db_id = None
            self.session_contagem_alvo = None
            self.current_session_count = 0
            self._last_session_logged_total = None
            self._base_counter_snapshot = 0

        try:
            if self.detector:
                try:
                    self.detector.set_session_context(None)
                except Exception:
                    pass
            self.stop_event.set()
            if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
                self.thread.join(timeout=1.5)
        except Exception:
            pass
        finally:
            self.thread = None

        if self.camera:
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None

        log.info("[CT%s] STOP lote='%s' concluido (total=%s)", self.ct.get('id'), lote_atual or "-", total_final)

        # Solta o detector para liberar memoria GPU/CPU
        self.detector = None

        # Prepara um novo evento para a proxima sessao
        self.stop_event = threading.Event()

    # ---------- pausa / retomada ----------

    def pause_session(self, motivo: str | None = None):
        """Pausa a sessao atual (nao cria nova sessao no banco)."""
        with self.session_lock:
            if not self.session_active or self.session_db_id is None:
                log.warning("[CT%s] PAUSE ignorado: sem sessao ativa (db_id=%s)", self.ct.get('id'), self.session_db_id)
                return
            self.session_paused = True
            log.info("[CT%s] PAUSE aplicado (db_id=%s, total_atual=%s)", self.ct.get('id'), self.session_db_id, self.current_session_count)
            try:
                _pause_session_db(self.session_db_id)
            except Exception:
                # Em modo agente sem DB, tentamos callback
                try:
                    cb = getattr(self, "on_pause_session", None)
                    if callable(cb):
                        cb(self.session_db_id, motivo)
                    else:
                        raise RuntimeError("callback ausente")
                except Exception:
                    log.debug("[CT%s] PAUSE: DB indisponivel/ignorado", self.ct.get('id'))

    def resume_session(self):
        """Retoma a sessao pausada, voltando a contar a partir do total atual."""
        with self.session_lock:
            if not self.session_active or self.session_db_id is None:
                log.warning("[CT%s] RESUME ignorado: sem sessao ativa (db_id=%s)", self.ct.get('id'), self.session_db_id)
                return
            if getattr(self, "camera_lost", False):
                log.warning("[CT%s] RESUME bloqueado: camera offline", self.ct.get('id'))
                return
            # ajusta baseline para manter a contagem relativa
            try:
                total_abs = int(getattr(self.detector, "counter", 0))
            except Exception:
                total_abs = 0
            try:
                rel_atual = int(self.current_session_count or 0)
            except Exception:
                rel_atual = 0
            self._base_counter_snapshot = total_abs - rel_atual
            self.session_paused = False
            log.info("[CT%s] RESUME aplicado (db_id=%s, base=%s, total_abs=%s, rel_atual=%s)", self.ct.get('id'), self.session_db_id, self._base_counter_snapshot, total_abs, rel_atual)
            try:
                _resume_session_db(self.session_db_id)
            except Exception:
                # Em modo agente sem DB, ignoramos
                log.debug("[CT%s] RESUME: DB indisponivel/ignorado", self.ct.get('id'))

    # ---------- camera ----------

    def _check_camera_timeout(self, now: float, has_frame: bool) -> None:
        if self.source_type != "rtsp":
            return
        if has_frame:
            self._last_camera_ok_ts = now
            if self.camera_lost:
                self.camera_lost = False
                self.camera_alert = None
                log.info("[CT%s] Fonte de video recuperada.", self.ct.get('id'))
            return
        if not self.session_active:
            return
        last_ok = self._last_camera_ok_ts if self._last_camera_ok_ts is not None else now
        if (now - last_ok) < self.camera_lost_timeout:
            return
        if self.camera_lost:
            self._maybe_reopen_camera(now, reason="timeout_reconnect")
            return
        self._handle_camera_lost()
        self._maybe_reopen_camera(now, reason="timeout_reconnect")

    def _maybe_reopen_camera(self, now: float, reason: str) -> None:
        if (now - self._last_camera_reopen_ts) < self.camera_reopen_interval:
            return
        self._last_camera_reopen_ts = now
        log.info("[CT%s] Reabrindo fonte de video (%s).", self.ct.get('id'), reason)
        self._open_sources()

    def _handle_camera_lost(self) -> None:
        self.camera_lost = True
        msg = "Perda de conexao com a camera. Sessao pausada automaticamente."
        self.camera_alert = msg
        log.warning("[CT%s] %s", self.ct.get('id'), msg)
        try:
            self.pause_session(motivo=msg)
        except Exception as e:
            log.error("[CT%s] Falha ao pausar sessao apos perda de camera: %s", self.ct.get('id'), e, exc_info=True)

    def probe_camera_idle(self) -> None:
        if self.session_active or self.source_type != "rtsp":
            return
        try:
            now = time.monotonic()
        except Exception:
            now = time.time()
        if (now - self._last_camera_probe_ts) < self.camera_idle_check_interval:
            return
        self._last_camera_probe_ts = now
        path = self.source_path or self.default_source_path
        if not path:
            return
        cap = None
        ok = False
        try:
            try:
                timeout_us = int(os.getenv("CAMERA_FFMPEG_STIMEOUT_US", str(int(os.getenv("CAMERA_OPEN_TIMEOUT_MS", "2000")) * 1000)))
                if timeout_us > 0:
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"stimeout;{timeout_us}"
            except Exception:
                pass
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(os.getenv("CAMERA_OPEN_TIMEOUT_MS", "2000")))
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(os.getenv("CAMERA_READ_TIMEOUT_MS", "2000")))
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
        if ok:
            self.camera_lost = False
            self.camera_alert = None
            self._last_camera_ok_ts = now
        else:
            self.camera_lost = True
            self.camera_alert = "Camera offline"

    # ---------- fonte ----------

    def set_source(self, source_type: str, source_path: str | None):
        if source_type == "file" and source_path:
            self.source_type = "file"
            self.source_path = source_path
        else:
            self.source_type = "rtsp"
            self.source_path = self.default_source_path

        if self.thread and self.thread.is_alive():
            self._open_sources()

    # ---------- cleanup ----------

    def release(self):
        self.stop_event.set()

        try:
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
        except Exception:
            pass

        if self.camera:
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None

        self.detector = None
