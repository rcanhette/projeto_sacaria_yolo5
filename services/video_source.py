import logging
import os
import threading
import time
from typing import Optional, Tuple

import cv2

log = logging.getLogger(__name__)


class VideoSource:
    """Wrapper simples para leitura contínua de frames com OpenCV."""

    def __init__(self, source_path: str):
        self.source_path = source_path
        self.cap: Optional[cv2.VideoCapture] = None
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_ok_ts: float | None = None
        self.last_read_ts: float | None = None
        self.last_error: str | None = None
        self.delay = 0.0
        self.is_file = not source_path.lower().startswith("rtsp")

        log.info("Abrindo fonte de vídeo (%s): %s", "arquivo" if self.is_file else "stream", source_path)

        if self.is_file:
            self.cap = cv2.VideoCapture(source_path)
        else:
            self.cap = cv2.VideoCapture(source_path, cv2.CAP_FFMPEG)
            try:
                buf_env = os.getenv("VIDEO_RTSP_BUFFER_SIZE")
                buffer_size = int(buf_env) if buf_env is not None else 3
            except Exception:
                buffer_size = 3
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)

        if not self.cap or not self.cap.isOpened():
            log.error("Não foi possível abrir a fonte de vídeo: %s", source_path)
            self.cap = None
            return

        if self.is_file:
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            delay_ms_env = os.getenv("VIDEO_FILE_DELAY_MS")
            delay_factor_env = os.getenv("VIDEO_FILE_DELAY_FACTOR")

            if delay_ms_env is not None:
                try:
                    self.delay = max(0.0, float(delay_ms_env) / 1000.0)
                except Exception:
                    self.delay = 0.033
            elif fps > 0:
                try:
                    factor = float(delay_factor_env) if delay_factor_env is not None else 0.9
                except Exception:
                    factor = 0.9
                self.delay = max(0.0, factor / fps)
                log.info("Arquivo %s: FPS=%.2f, delay ajustado para %.4fs", source_path, fps, self.delay)
            else:
                self.delay = 0.033

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        if self.cap is None:
            return

        while not self.stop_event.is_set():
            try:
                ret, frame = self.cap.read()
            except Exception as exc:
                log.warning("Exceção ao ler frames da fonte %s: %s. Encerrando captura.", self.source_path, exc)
                with self.lock:
                    self.ret = False
                    self.frame = None
                self.last_read_ts = time.monotonic()
                self.last_error = str(exc)
                break

            self.last_read_ts = time.monotonic()
            with self.lock:
                self.ret = ret
                if ret:
                    self.frame = frame
                    self.last_ok_ts = self.last_read_ts
                elif self.is_file:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    log.info("Arquivo de vídeo reiniciado: %s", self.source_path)
                    continue

            if self.is_file and self.delay > 0:
                time.sleep(self.delay)
            else:
                time.sleep(0.001)

    def get_frame(self) -> Tuple[bool, Optional[object]]:
        with self.lock:
            ret = self.ret
            frame = self.frame.copy() if self.frame is not None else None
        return ret, frame

    def release(self) -> None:
        log.info("Encerrando fonte de vídeo: %s", self.source_path)
        try:
            self.stop_event.set()
        except Exception:
            pass
        try:
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass
