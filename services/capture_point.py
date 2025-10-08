# services/capture_point.py
import cv2
import time
import threading
from datetime import datetime
from services.industrial_tag_detector import IndustrialTagDetector
from services.video_source import VideoSource
from services.session_repository import create_session, insert_log, finish_session

class CapturePoint:
    def __init__(self, ct, config):
        self.ct = ct  # dict: {id, name, source_path, roi, model_path}
        self.default_source_type = config["source_type"]          # "rtsp"
        self.default_source_path = config["path"]                 # rtsp url
        self.roi_cfg = config.get("roi", None)                    # (x,y,w,h)
        self.model_path = config.get("model", "sacaria_yolov5n.pt")

        # fonte atual (pode ser “file” para testes na sessão corrente)
        self.source_type = self.default_source_type
        self.source_path = self.default_source_path

        self.camera = None
        self.detector = None

        self.thread = None
        self.stop_event = threading.Event()

        # estado de sessão
        self.session_active = False
        self.session_lote = None
        self.session_data = None
        self.session_hora_inicio = None
        self.session_hora_fim = None
        self.session_db_id = None   # <<< ID na tabela session

        # contadores
        self.current_session_count = 0
        self._last_session_logged_total = None
        self._base_counter_snapshot = 0

        # último frame anotado p/ /video
        self.last_vis_frame = None

    # ---------- recursos ----------
    def _open_sources(self):
        if self.camera:
            try: self.camera.release()
            except Exception: pass
            self.camera = None

        self.camera = VideoSource(self.source_path)
        self.detector = IndustrialTagDetector(self.model_path, roi=self.roi_cfg)

    def _ensure_thread(self):
        if self.thread and self.thread.is_alive():
            return
        if not self.camera or not self.detector:
            self._open_sources()

        def loop():
            while not self.stop_event.is_set():
                try:
                    if self.camera is None or self.detector is None:
                        self._open_sources()
                        time.sleep(0.05)
                        continue

                    ret, frame = self.camera.get_frame()
                    if not ret or frame is None:
                        time.sleep(0.01)
                        continue

                    vis, total_counter_abs = self.detector.detect_and_tag(frame)
                    self.last_vis_frame = vis

                    if self.session_active:
                        total_abs = getattr(self.detector, "counter", total_counter_abs)
                        rel_total = int(max(0, total_abs - self._base_counter_snapshot))
                        if rel_total != self.current_session_count:
                            self.current_session_count = rel_total
                            self._log_deltas(rel_total)

                    time.sleep(0.005)
                except Exception as e:
                    print(f"[CT{self.ct['id']}] loop error: {e}")
                    time.sleep(0.05)

        # Garante que o evento de parada esteja limpo antes de iniciar uma nova thread
        self.stop_event.clear()
        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    # ---------- sessão ----------
    def start_session(self, lote: str):
        self._ensure_thread()

        agora = datetime.now()
        try:
            base = int(getattr(self.detector, "counter", 0))
        except Exception:
            base = 0

        # cria registro no banco e guarda o id
        self.session_db_id = create_session(self.ct["id"], lote)

        self.session_active = True
        self.session_lote = lote
        self.session_data = agora.strftime("%d/%m/%Y")
        self.session_hora_inicio = agora.strftime("%H:%M:%S")
        self.session_hora_fim = None

        self._base_counter_snapshot = base
        self.current_session_count = 0
        self._last_session_logged_total = 0

        # (não há mais cabeçalho em .txt — virou a linha da tabela `session`)

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
                    insert_log(
                        session_id=self.session_db_id,
                        ct_id=self.ct["id"],
                        delta=+1,
                        total_atual=self._last_session_logged_total
                    )
            else:
                for _ in range(-diff):
                    self._last_session_logged_total -= 1
                    if self._last_session_logged_total < 0:
                        self._last_session_logged_total = 0
                    insert_log(
                        session_id=self.session_db_id,
                        ct_id=self.ct["id"],
                        delta=-1,
                        total_atual=self._last_session_logged_total
                    )
        except Exception as e:
            print(f"[ERRO LOG DB] CT{self.ct['id']} delta: {e}")

    def stop_session(self):
        # Mesmo que não haja sessão ativa, atender STOP deve encerrar captura

        agora = datetime.now()
        try:
            if self.session_active:
                self.session_hora_fim = agora.strftime("%H:%M:%S")
                quantidade = int(self.current_session_count)
                try:
                    if self.session_db_id is not None:
                        finish_session(self.session_db_id, quantidade, status='finalizado')
                except Exception as e:
                    print(f"[LOG DB] erro ao finalizar sessão: {e}")
        finally:
            # limpa estado de sessão
            self.session_active = False
            self.session_lote = None
            self.session_data = None
            self.session_hora_inicio = None
            self.session_hora_fim = None
            self.session_db_id = None
            self.current_session_count = 0
            self._last_session_logged_total = None
            self._base_counter_snapshot = 0

        # IMPORTANTE: encerrar de fato a thread de captura e conexões (RTSP/Arquivo)
        try:
            self.stop_event.set()
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.5)
        except Exception:
            pass
        finally:
            self.thread = None

        # Encerra a fonte de vídeo (isso para a thread interna do VideoSource)
        if self.camera:
            try:
                self.camera.release()
            except Exception:
                pass
        self.camera = None

        # Solta o detector para liberar memória GPU/CPU
        self.detector = None

        # Prepara um novo evento para próxima sessão (senão a thread sairia imediatamente)
        self.stop_event = threading.Event()

    # ---------- fonte ----------
    def set_source(self, source_type: str, source_path: str | None):
        # “file” só para teste da sessão corrente (não persiste no banco)
        if source_type == "file" and source_path:
            self.source_type = "file"
            self.source_path = source_path
        else:
            self.source_type = "rtsp"
            self.source_path = self.default_source_path
        # Reabre a fonte apenas se já houver thread ativa; caso contrário, será aberta ao iniciar
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
            try: self.camera.release()
            except Exception: pass
        self.camera = None
        self.detector = None
