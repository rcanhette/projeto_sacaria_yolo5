# services/capture_point.py

import cv2

import time

import threading

import logging

from datetime import datetime

from services.industrial_tag_detector import IndustrialTagDetector

from services.video_source import VideoSource

from services.session_repository import create_session, insert_log, finish_session

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

            self.line_offset_red = 40

        try:

            self.line_offset_blue = int(config.get("line_offset_blue", -40))

        except Exception:

            self.line_offset_blue = -40

        flow_mode_cfg = str(config.get("flow_mode", "cima") or "cima").strip().lower()

        if flow_mode_cfg not in ("cima", "baixo", "sem_fluxo"):

            flow_mode_cfg = "cima"

        self.flow_mode = flow_mode_cfg

        self.missed_frame_dir = (config.get("missed_frame_dir") or "").strip() or None

        try:

            self.max_lost = int(config.get("max_lost", 2))

        except Exception:

            self.max_lost = 2

        try:

            self.match_dist = float(config.get("match_dist", 150))

        except Exception:

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

        # estado de sesso

        self.session_active = False

        self.session_lote = None

        self.session_data = None

        self.session_hora_inicio = None

        self.session_hora_fim = None

        self.session_db_id = None   # <<< ID na tabela session

        self.session_contagem_alvo = None

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

        self.detector = IndustrialTagDetector(

            self.model_path,

            roi=self.roi_cfg,

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

        if self.session_active and self.session_lote:

            try:

                self.detector.set_session_context(self.session_lote)

            except Exception:

                pass

        self._apply_cross_point_mode()

    def _apply_cross_point_mode(self):

        """Garante que o ponto de cruzamento permanea central."""

        try:

            if self.detector and getattr(self.detector, "cross_point_mode", None) != "meio":

                self.detector.cross_point_mode = "meio"

        except Exception:

            pass

    def _ensure_thread(self):

        self._apply_cross_point_mode()

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

                    f"[CT{self.ct['id']}] START lote='{lote}' modelo='{load_path}' "

                    f"(solicitado='{requested}', resolvido='{resolved}', status={status_txt}, "

                    f"fluxo='{self.flow_mode}', offsets(red={self.line_offset_red}, azul={self.line_offset_blue}), "

                    f"max_lost={self.max_lost}, match_dist={self.match_dist}, min_conf={self.min_conf}, "

                    f"missed_dir='{self.missed_frame_dir or '-'}')"

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

            # cria registro no banco e guarda o id

            self.session_db_id = create_session(self.ct["id"], lote, contagem_alvo)

            self.session_active = True

            self.session_lote = lote

            self.session_data = agora.strftime("%d/%m/%Y")

            self.session_hora_inicio = agora.strftime("%H:%M:%S")

            self.session_hora_fim = None

            self.session_contagem_alvo = int(contagem_alvo) if contagem_alvo is not None else None

            if self.detector:

                try:

                    self.detector.set_session_context(lote)

                except Exception as log_err:

                    log.warning("[CT%s] Falha ao definir contexto da sessao para snapshots (%s)", self.ct.get('id'), log_err)

            self._base_counter_snapshot = base

            self.current_session_count = 0

            self._last_session_logged_total = 0

        # (no h mais cabealho em .txt  virou a linha da tabela `session`)

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

    def stop_session(self, observacao: str | None = None):

        # Mesmo que no haja sesso ativa, atender STOP deve encerrar captura

        agora = datetime.now()

        try:

            if self.session_active:

                self.session_hora_fim = agora.strftime("%H:%M:%S")

                quantidade = int(self.current_session_count)

                try:

                    if self.session_db_id is not None:

                        finish_session(self.session_db_id, quantidade, status='finalizado', observacao=observacao)

                except Exception as e:

                    print(f"[LOG DB] erro ao finalizar sesso: {e}")

        finally:

            # limpa estado de sesso

            self.session_active = False

            self.session_lote = None

            self.session_data = None

            self.session_hora_inicio = None

            self.session_hora_fim = None

            self.session_db_id = None

            self.session_contagem_alvo = None

            self.current_session_count = 0

            self._last_session_logged_total = None

            self._base_counter_snapshot = 0

        # IMPORTANTE: encerrar de fato a thread de captura e conexes (RTSP/Arquivo)

        try:

            if self.detector:

                try:

                    self.detector.set_session_context(None)

                except Exception:

                    pass

            self.stop_event.set()

            if self.thread and self.thread.is_alive():

                self.thread.join(timeout=1.5)

        except Exception:

            pass

        finally:

            self.thread = None

        # Encerra a fonte de vdeo (isso para a thread interna do VideoSource)

        if self.camera:

            try:

                self.camera.release()

            except Exception:

                pass

        self.camera = None

        # Solta o detector para liberar memria GPU/CPU

        self.detector = None

        # Prepara um novo evento para prxima sesso (seno a thread sairia imediatamente)

        self.stop_event = threading.Event()

    # ---------- fonte ----------

    def set_source(self, source_type: str, source_path: str | None):

        # file s para teste da sesso corrente (no persiste no banco)

        if source_type == "file" and source_path:

            self.source_type = "file"

            self.source_path = source_path

        else:

            self.source_type = "rtsp"

            self.source_path = self.default_source_path

        # Reabre a fonte apenas se j houver thread ativa; caso contrrio, ser aberta ao iniciar

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

