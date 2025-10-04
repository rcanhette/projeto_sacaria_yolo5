# routes/ct.py
import time
import json
import cv2
from flask import Blueprint, render_template, Response, request, redirect, url_for, flash
from services.capture_point import CapturePoint
from services.ct_repository import get_ct
from services.runtime import ct_runtime

ct_bp = Blueprint("ct", __name__)

def _parse_roi(roi_val):
    if roi_val is None:
        return None
    if isinstance(roi_val, (tuple, list)) and len(roi_val) == 4:
        return tuple(int(v) for v in roi_val)
    parts = [p.strip() for p in str(roi_val).split(",")]
    if len(parts) != 4:
        return None
    return tuple(int(p) for p in parts)

def _ensure_cp(ct_row):
    ct_id = ct_row["id"]
    if ct_id in ct_runtime:
        return ct_runtime[ct_id]
    cfg = {
        "source_type": "rtsp",
        "path": ct_row["source_path"],
        "roi": _parse_roi(ct_row["roi"]),
        "model": ct_row.get("model_path") or "sacaria_yolov5n.pt",
    }
    cp = CapturePoint(ct_row, cfg)
    ct_runtime[ct_id] = cp
    return cp

@ct_bp.route("/ct/<int:ct_id>")
def ct_detail(ct_id):
    ct_row = get_ct(ct_id)
    if not ct_row:
        flash("CT não encontrada.", "error")
        return redirect(url_for("ct_admin.ct_admin_list"))
    cp = _ensure_cp(ct_row)
    return render_template("ct_detail.html", ct=ct_row, cp=cp)

@ct_bp.route("/ct/<int:ct_id>/start", methods=["POST"])
def ct_start(ct_id):
    ct_row = get_ct(ct_id)
    if not ct_row:
        flash("CT não encontrada.", "error")
        return redirect(url_for("ct_admin.ct_admin_list"))

    lote = request.form.get("lote")
    source_type = request.form.get("source_type", "rtsp")
    file_path = (request.form.get("file_path") or "").strip() or None

    if not lote:
        flash("Lote é obrigatório.", "error")
        return redirect(url_for("ct.ct_detail", ct_id=ct_id))

    cp = _ensure_cp(ct_row)
    cp.set_source(source_type, file_path)
    cp.start_session(lote)

    flash(f"{ct_row['name']} iniciada com lote {lote}.", "success")
    return redirect(url_for("ct.ct_detail", ct_id=ct_id))

@ct_bp.route("/ct/<int:ct_id>/stop", methods=["POST"])
def ct_stop(ct_id):
    cp = ct_runtime.get(ct_id)
    if not cp:
        flash("CT não encontrada.", "error")
        return redirect(url_for("ct_admin.ct_admin_list"))

    cp.stop_session()
    flash(f"{cp.ct['name']} parada.", "info")
    return redirect(url_for("ct.ct_detail", ct_id=ct_id))

@ct_bp.route("/sse/ct/<int:ct_id>")
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

@ct_bp.route("/ct/<int:ct_id>/video")
def ct_video(ct_id):
    cp = ct_runtime.get(ct_id)
    if not cp or not cp.session_active:
        return "Nenhuma sessão ativa para esta CT.", 404

    def gen():
        frame = None
        raw = None
        while True:
            try:
                frame = cp.last_vis_frame
                if frame is None and cp.camera is not None:
                    ret, raw = cp.camera.get_frame()
                    if not ret or raw is None:
                        time.sleep(0.02)
                        continue
                    frame = raw

                if frame is None:
                    time.sleep(0.02)
                    continue

                text = f"TOTAL: {int(cp.current_session_count)}"
                cv2.putText(frame, text, (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)

                ok, buffer = cv2.imencode('.jpg', frame)
                if ok:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.01)
            except Exception as e:
                if frame is not None:
                    cv2.putText(frame, f"ERRO: {e}", (15, 120),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    ok, buffer = cv2.imencode('.jpg', frame)
                    if ok:
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                time.sleep(0.1)

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')
