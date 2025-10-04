import os
import cv2
import time
import json
import threading
from datetime import datetime
from flask import Flask, render_template, Response, request, redirect, url_for, flash
from industrial_tag_detector import IndustrialTagDetector
from video_source import VideoSource

app = Flask(__name__)
app.secret_key = "supersecret"

# =========================
# CONFIG FIXA DE CTs (teste)
# =========================
CT_LIST = {
    1: {"id": 1, "name": "M1", "camera_key": "cam1"},
    2: {"id": 2, "name": "CT 2", "camera_key": "cam2"},
}

# ROI nos valores originais (x, y, w, h)
CAMERA_OPTIONS = {
    "cam1": {
        "source_type": "rtsp",
        "path": "rtsp://meucam1",
        "roi": (765, 495, 300, 375),  # (x, y, w, h)
        "model": "sacaria_yolov5n.pt",
    },
    "cam2": {
        "source_type": "rtsp",
        "path": "rtsp://meucam2",
        "roi": (100, 100, 500, 400),  # (x, y, w, h)
        "model": "sacaria_yolov5n.pt",
    },
}

# abrir direto CT 1 na raiz
DEFAULT_CT_ID = 1

# =========================
# CLASSE DE CAPTURE (CT)
# =========================
class CapturePoint:
    """
    - Mantém câmera + detector.
    - Thread de processamento em background: conta mesmo sem abrir /video.
    - Sessão START/STOP com lote, data/hora, log detalhado e resumo.
    - ROI passada para o detector (linhas de cruzamento preservadas).
    - Guarda o último frame ANOTADO pelo detector para o /video.
    """
    def __init__(self, ct, config):
        self.ct = ct
        self.default_source_type = config["source_type"]
        self.default_source_path = config["path"]
        self.roi_cfg = config.get("roi", None)
        self.model_path = config.get("model", "sacaria_yolov5n.pt")

        # Fonte atual (pode ser alterada no START)
        self.source_type = self.default_source_type
        self.source_path = self.default_source_path

        # Recursos
        self.camera = None
        self.detector = None

        # Thread
        self.thread = None
        self.stop_event = threading.Event()

        # Sessão
        self.session_active = False
        self.session_lote = None
        self.session_data = None
        self.session_hora_inicio = None
        self.session_hora_fim = None
        self.session_log_file = None

        # Contagem
        self.current_session_count = 0  # relativo à sessão
        self._last_session_logged_total = None  # para gerar RECONHECIMENTO ± 1
        self._base_counter_snapshot = 0  # snapshot do contador absoluto no START

        # Visualização (último frame anotado pelo detector)
        self.last_vis_frame = None

    def _open_sources(self):
        # Fecha camera antiga se houver
        if self.camera:
            try:
                self.camera.release()
            except Exception:
                pass
            self.camera = None

        # Abre camera
        self.camera = VideoSource(self.source_path)

        # Cria detector (PASSANDO ROI para ele, preservando linhas/zonas)
        self.detector = IndustrialTagDetector(self.model_path, roi=self.roi_cfg)

    def _ensure_thread(self):
        if self.thread and self.thread.is_alive():
            return

        # Abre fontes se ainda não abertas
        if not self.camera or not self.detector:
            self._open_sources()

        def loop():
            while not self.stop_event.is_set():
                try:
                    # Se não existe camera/detector, tenta reabrir
                    if self.camera is None or self.detector is None:
                        self._open_sources()
                        time.sleep(0.05)
                        continue

                    ret, frame = self.camera.get_frame()
                    if not ret or frame is None:
                        time.sleep(0.01)
                        continue

                    # Processamento contínuo: detector desenha linhas e calcula total absoluto
                    vis, total_counter_abs = self.detector.detect_and_tag(frame)
                    self.last_vis_frame = vis  # guardamos o frame anotado para o /video

                    if self.session_active:
                        # Se o detector tiver um atributo 'counter', usamos como verdade
                        total_abs = getattr(self.detector, "counter", total_counter_abs)
                        rel_total = int(max(0, total_abs - self._base_counter_snapshot))

                        if rel_total != self.current_session_count:
                            self.current_session_count = rel_total
                            self._log_deltas(rel_total)

                    time.sleep(0.005)

                except Exception as e:
                    print(f"[CT{self.ct['id']}] loop error: {e}")
                    time.sleep(0.05)

        self.thread = threading.Thread(target=loop, daemon=True)
        self.thread.start()

    def start_session(self, lote: str):
        # Garante thread de processamento rodando
        self._ensure_thread()

        agora = datetime.now()
        os.makedirs("logs", exist_ok=True)
        fname = f"{self.ct['name']}_{lote}_{agora.strftime('%Y%m%d_%H%M%S')}.log"
        self.session_log_file = os.path.join("logs", fname)

        # Snapshot do contador absoluto atual (se disponível)
        try:
            base = int(getattr(self.detector, "counter", 0))
        except Exception:
            base = 0

        # Ativa sessão
        self.session_active = True
        self.session_lote = lote
        self.session_data = agora.strftime("%d/%m/%Y")
        self.session_hora_inicio = agora.strftime("%H:%M:%S")
        self.session_hora_fim = None

        self._base_counter_snapshot = base
        self.current_session_count = 0
        self._last_session_logged_total = 0  # inicia do zero relativo

        # Cabeçalho do log (padrão completo)
        try:
            with open(self.session_log_file, 'a', encoding='utf-8') as f:
                f.write("CABEÇALHO: LOTE | DATA | HORA INICIO | HORA FIM | TOTAL DE SACARIA CONTADAS\n")
                f.write(f"LOTE: {lote}\n")
                f.write(f"DATA: {self.session_data}\n")
                f.write(f"HORA INICIO: {self.session_hora_inicio}\n")
                f.write(f"FONTE: {self.source_type} | {self.source_path}\n")
                f.write(f"ROI: {self.roi_cfg}\n")
        except Exception as e:
            print(f"[LOG] erro no início da sessão: {e}")

    def _log_deltas(self, current_rel_total: int):
        """
        Gera as linhas de RECONHECIMENTO ± 1 HORA: ... TOTAL: N
        conforme muda a contagem relativa.
        """
        if not self.session_active or not self.session_log_file:
            return

        if self._last_session_logged_total is None:
            self._last_session_logged_total = current_rel_total
            return

        diff = current_rel_total - self._last_session_logged_total
        if diff == 0:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        try:
            with open(self.session_log_file, 'a', encoding='utf-8') as f:
                if diff > 0:
                    for _ in range(diff):
                        self._last_session_logged_total += 1
                        f.write(f"RECONHECIMENTO + 1 HORA: {now_str} TOTAL: {self._last_session_logged_total}\n")
                else:
                    for _ in range(-diff):
                        self._last_session_logged_total -= 1
                        if self._last_session_logged_total < 0:
                            self._last_session_logged_total = 0
                        f.write(f"RECONHECIMENTO - 1 HORA: {now_str} TOTAL: {self._last_session_logged_total}\n")
        except Exception as e:
            print(f"[ERRO LOG] CT{self.ct['id']} delta: {e}")

    def stop_session(self):
        if not self.session_active:
            return

        agora = datetime.now()
        self.session_hora_fim = agora.strftime("%H:%M:%S")
        quantidade = int(self.current_session_count)

        try:
            if self.session_log_file:
                with open(self.session_log_file, 'a', encoding='utf-8') as f:
                    f.write(
                        f"LOTE: {self.session_lote} | DATA: {self.session_data} | "
                        f"HORA INICIO: {self.session_hora_inicio} | HORA FIM: {self.session_hora_fim} | "
                        f"TOTAL: {quantidade}\n"
                    )
        except Exception as e:
            print(f"[LOG] erro ao finalizar: {e}")

        # limpa estado da sessão (thread continua para reutilização)
        self.session_active = False
        self.session_lote = None
        self.session_data = None
        self.session_hora_inicio = None
        self.session_hora_fim = None
        self.session_log_file = None
        self.current_session_count = 0
        self._last_session_logged_total = None
        self._base_counter_snapshot = 0

    def set_source(self, source_type: str, source_path: str | None):
        """Configura fonte (rtsp/arquivo) antes do start."""
        if source_type == "file" and source_path:
            self.source_type = "file"
            self.source_path = source_path
        else:
            self.source_type = "rtsp"
            self.source_path = self.default_source_path

        # Troca de fonte -> reabrir recursos
        self._open_sources()

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


# =========================
# RUNTIME
# =========================
ct_runtime = {}

# =========================
# ROTAS (prefixo ct_)
# =========================
@app.route("/")
def index():
    return redirect(url_for("ct_detail", ct_id=DEFAULT_CT_ID))

@app.route("/cts")
def cts():
    return render_template("cts.html", cts=CT_LIST.values())

@app.route("/ct/<int:ct_id>")
def ct_detail(ct_id):
    ct = CT_LIST.get(ct_id)
    if not ct:
        flash("CT não encontrada.", "error")
        return redirect(url_for("cts"))

    if ct_id not in ct_runtime:
        cfg = CAMERA_OPTIONS.get(ct["camera_key"], CAMERA_OPTIONS["cam1"])
        ct_runtime[ct_id] = CapturePoint(ct, cfg)

    cp = ct_runtime[ct_id]
    return render_template("ct_detail.html", ct=ct, cp=cp)

@app.route("/ct/<int:ct_id>/start", methods=["POST"])
def ct_start(ct_id):
    ct = CT_LIST.get(ct_id)
    if not ct:
        flash("CT não encontrada.", "error")
        return redirect(url_for("cts"))

    lote = request.form.get("lote")
    source_type = request.form.get("source_type", "rtsp")
    file_path = request.form.get("file_path", "").strip() or None

    if not lote:
        flash("Lote é obrigatório.", "error")
        return redirect(url_for("ct_detail", ct_id=ct_id))

    if ct_id not in ct_runtime:
        cfg = CAMERA_OPTIONS.get(ct["camera_key"], CAMERA_OPTIONS["cam1"])
        ct_runtime[ct_id] = CapturePoint(ct, cfg)

    cp = ct_runtime[ct_id]
    cp.set_source(source_type, file_path)
    cp.start_session(lote)

    flash(f"{ct['name']} iniciada com lote {lote}.", "success")
    return redirect(url_for("ct_detail", ct_id=ct_id))

@app.route("/ct/<int:ct_id>/stop", methods=["POST"])
def ct_stop(ct_id):
    cp = ct_runtime.get(ct_id)
    if not cp:
        flash("CT não encontrada.", "error")
        return redirect(url_for("cts"))

    cp.stop_session()
    flash(f"{cp.ct['name']} parada.", "info")
    return redirect(url_for("ct_detail", ct_id=ct_id))

@app.route("/sse/ct/<int:ct_id>")
def sse_ct(ct_id):
    cp = ct_runtime.get(ct_id)
    if not cp:
        return "CT não encontrada", 404

    def stream():
        while True:
            payload = {
                "session_active": cp.session_active,
                "lote": cp.session_lote,
                "data": cp.session_data,
                "hora_inicio": cp.session_hora_inicio,
                "count": int(cp.current_session_count),
                "fonte": cp.source_type,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1)

    return Response(stream(), mimetype="text/event-stream")

@app.route("/ct/<int:ct_id>/video")
def ct_video(ct_id):
    cp = ct_runtime.get(ct_id)
    if not cp or not cp.session_active:
        return "Nenhuma sessão ativa para esta CT.", 404

    def gen():
        while True:
            try:
                # Exibe o último frame anotado pelo detector (linhas + caixas), sem reprocessar
                frame = cp.last_vis_frame

                # Fallback: se ainda não temos um frame anotado, pega um da câmera
                if frame is None and cp.camera is not None:
                    ret, raw = cp.camera.get_frame()
                    if not ret or raw is None:
                        time.sleep(0.02)
                        continue
                    frame = raw

                if frame is None:
                    time.sleep(0.02)
                    continue

                # Mostra contagem atual
                text = f"TOTAL: {int(cp.current_session_count)}"
                cv2.putText(frame, text, (15, 45), cv2.FONT_HERSHEY_SIMPLEX,
                            1.5, (255, 255, 255), 3)

                ok, buffer = cv2.imencode('.jpg', frame)
                if ok:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.01)

            except Exception as e:
                # Mantém o stream vivo mostrando o erro
                err = frame if frame is not None else (raw if 'raw' in locals() else None)
                if err is not None:
                    cv2.putText(err, f"ERRO: {e}", (15, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    ok, buffer = cv2.imencode('.jpg', err)
                    if ok:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.1)

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
