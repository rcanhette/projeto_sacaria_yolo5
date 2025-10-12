import time
import json
import cv2
import logging
from flask import Blueprint, render_template, Response, request, redirect, url_for, flash
from services.capture_point import CapturePoint
from services.tc_repository import get_tc, list_tcs
from services.session_repository import get_active_session_by_ct
from services.runtime import tc_runtime
from routes.auth import current_user, login_required
from services.auth_repository import user_can_view_tc, user_can_control_tc
from services.session_repository import get_active_session_by_ct

tc_bp = Blueprint("tc", __name__)
log = logging.getLogger(__name__)

def _parse_roi(roi_val):
    if roi_val is None:
        return None
    if isinstance(roi_val, (tuple, list)) and len(roi_val) == 4:
        return tuple(int(v) for v in roi_val)
    parts = [p.strip() for p in str(roi_val).split(",")]
    if len(parts) != 4:
        return None
    return tuple(int(p) for p in parts)

def _ensure_cp(tc_row):
    tc_id = tc_row["id"]
    if tc_id in tc_runtime:
        return tc_runtime[tc_id]
    log.info(
        "Inicializando CapturePoint para TC %s (%s) com fonte '%s'",
        tc_id,
        tc_row.get("name"),
        tc_row.get("source_path"),
    )
    cfg = {
        "source_type": "rtsp",
        "path": tc_row["source_path"],
        "roi": _parse_roi(tc_row["roi"]),
        "model": tc_row.get("model_path") or "sacaria_yolov5n.pt",
        "line_offset_red": tc_row.get("line_offset_red", 40),
        "line_offset_blue": tc_row.get("line_offset_blue", -40),
        "flow_mode": tc_row.get("flow_mode") or "cima",
        "max_lost": int(tc_row.get("max_lost", 2) or 0),
        "match_dist": float(tc_row.get("match_dist", 150) or 150),
        "min_conf": float(tc_row.get("min_conf", 0.8) or 0.8),
        "missed_frame_dir": (tc_row.get("missed_frame_dir") or "").strip(),
    }
    log.debug("ConfiguraÃÂ§ÃÂ£o completa da TC %s: %s", tc_id, cfg)
    cp = CapturePoint(tc_row, cfg)
    tc_runtime[tc_id] = cp
    return cp

@tc_bp.route("/tc/<int:tc_id>")
@login_required
def tc_detail(tc_id):
    # Somente admin pode abrir a tela individual
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado ÃÂ  tela individual.", "error")
        return redirect(url_for("index"))
    tc_row = get_tc(tc_id)
    if not tc_row:
        flash("TC nÃÂ£o encontrada.", "error")
        return redirect(url_for("index"))
    cp = _ensure_cp(tc_row)
    return render_template("tc_detail.html", tc=tc_row, ct=tc_row, cp=cp)

@tc_bp.route("/tc-operacao")
@login_required
def tc_multi():
    u = current_user()
    if u["role"] != "admin":
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))
    tcs = list_tcs()
    return render_template("tc_multi.html", tcs=tcs)

@tc_bp.route("/tc/<int:tc_id>/start", methods=["POST"])
@login_required
def tc_start(tc_id):
    user = current_user()
    log.info("Usuario %s (%s) solicitou START para TC %s", user.get("username"), user.get("role"), tc_id)
    if not user_can_control_tc(user, tc_id):
        flash("Voce nao tem permissao para iniciar esta TC.", "error")
        log.warning("START negado: usuario %s sem permissao para TC %s", user.get("username"), tc_id)
        return redirect(url_for("index"))

    tc_row = get_tc(tc_id)
    if not tc_row:
        flash("TC nao encontrada.", "error")
        log.error("START abortado: TC %s nao encontrada", tc_id)
        return redirect(url_for("index"))

    lote = request.form.get("lote")
    contagem_alvo_raw = request.form.get("contagem_alvo")
    contagem_alvo = None
    if contagem_alvo_raw is not None and contagem_alvo_raw.strip() != "":
        try:
            contagem_alvo = int(contagem_alvo_raw)
            if contagem_alvo <= 0:
                raise ValueError
        except Exception:
            flash("Contagem alvo deve ser um numero inteiro positivo.", "error")
            log.warning("START abortado: contagem alvo invalida (%s) para TC %s", contagem_alvo_raw, tc_id)
            return redirect(url_for("index"))
    source_type = request.form.get("source_type", "rtsp")
    file_path = (request.form.get("file_path") or "").strip() or None

    if not lote:
        flash("Lote e obrigatorio.", "error")
        log.warning("START abortado: lote vazio para TC %s", tc_id)
        return redirect(url_for("index"))

    cp = _ensure_cp(tc_row)

    if cp.session_active or cp.session_db_id is not None:
        if request.headers.get("X-Requested-With") == "fetch":
            log.info("START ignorado: sessao ja ativa para TC %s (via fetch)", tc_id)
            return ("", 204)
        flash("Ja existe uma sessao operando para esta TC.", "info")
        log.info("START ignorado: sessao ja ativa para TC %s", tc_id)
        return redirect(url_for("index"))

    active = None
    try:
        active = get_active_session_by_ct(tc_id)
    except Exception:
        active = None
    if active and active.get("status") in ("operando", "ativo"):
        if request.headers.get("X-Requested-With") == "fetch":
            return ("", 204)
        flash("Ja existe uma sessao operando registrada no banco para esta TC.", "info")
        return redirect(url_for("index"))

    log.info("START autorizado para TC %s (lote=%s, alvo=%s, source_type=%s, arquivo=%s)", tc_id, lote, contagem_alvo, source_type, file_path)
    cp.set_source(source_type, file_path)
    cp.start_session(lote, contagem_alvo)
    log.info("START executado para TC %s (sessao ativa=%s, lote=%s)", tc_id, cp.session_active, cp.session_lote)

    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    flash(f"{tc_row['name']} iniciada com lote {lote}.", "success")
    return redirect(url_for("index"))

# AJAX-friendly start endpoint used by OperaÃÂ§ÃÂµes TCs
@tc_bp.route("/tc/<int:tc_id>/start-ajax", methods=["POST"])
@login_required
def tc_start_ajax(tc_id):
    u = current_user()
    if not user_can_control_tc(u, tc_id):
        return ("VocÃÂª nÃÂ£o tem permissÃÂ£o para iniciar esta TC.", 403)

    tc_row = get_tc(tc_id)
    if not tc_row:
        return ("TC nÃÂ£o encontrada.", 404)

    lote = request.form.get("lote")
    contagem_alvo_raw = request.form.get("contagem_alvo")
    contagem_alvo = None
    if contagem_alvo_raw is not None and str(contagem_alvo_raw).strip() != "":
        try:
            contagem_alvo = int(contagem_alvo_raw)
            if contagem_alvo <= 0:
                raise ValueError
        except Exception:
            return ("Contagem alvo deve ser um nÃÂºmero inteiro positivo.", 400)

    source_type = request.form.get("source_type", "rtsp")
    file_path = (request.form.get("file_path") or "").strip() or None

    if not lote:
        return ("Lote ÃÂ© obrigatÃÂ³rio.", 400)

    cp = _ensure_cp(tc_row)
    if cp.session_active or cp.session_db_id is not None:
        return ("", 204)

    try:
        active = get_active_session_by_ct(tc_id)
    except Exception:
        active = None
    if active and active.get("status") in ("operando", "ativo"):
        return ("", 204)

    cp.set_source(source_type, file_path)
    cp.start_session(lote, contagem_alvo)
    return ("", 204)

@tc_bp.route("/tc/<int:tc_id>/stop", methods=["POST"])
@login_required
def tc_stop(tc_id):
    user = current_user()
    log.info("Usuario %s (%s) solicitou STOP para TC %s", user.get("username"), user.get("role"), tc_id)
    if not user_can_control_tc(user, tc_id):
        flash("Voce nao tem permissao para parar esta TC.", "error")
        log.warning("STOP negado: usuario %s sem permissao para TC %s", user.get("username"), tc_id)
        return redirect(url_for("index"))

    cp = tc_runtime.get(tc_id)
    if not cp:
        flash("TC nao encontrada.", "error")
        log.error("STOP abortado: TC %s nao encontrada", tc_id)
        return redirect(url_for("index"))

    observacao = (request.form.get("observacao") or "").strip()
    try:
        alvo = cp.session_contagem_alvo
    except Exception:
        alvo = None
    qtd = int(cp.current_session_count)
    require_obs = alvo is not None and qtd != int(alvo)
    if require_obs and len(observacao) < 10:
        message = "Observacao (min. 10 caracteres) e obrigatoria quando total != contagem alvo."
        log.warning(
            "STOP negado: observacao insuficiente (qtd=%s alvo=%s) para TC %s",
            qtd,
            alvo,
            tc_id,
        )
        if request.headers.get("X-Requested-With") == "fetch":
            return (message, 400)
        flash(message, "error")
        return redirect(url_for("index"))
    if observacao and len(observacao) < 10:
        message = "Observacao deve ter pelo menos 10 caracteres."
        log.warning("STOP negado: observacao menor que 10 caracteres (TC %s)", tc_id)
        if request.headers.get("X-Requested-With") == "fetch":
            return (message, 400)
        flash(message, "error")
        return redirect(url_for("index"))

    log.info("STOP autorizado para TC %s (observacao=%s)", tc_id, bool(observacao))
    cp.stop_session(observacao=observacao or None)
    log.info("STOP executado para TC %s (total_final=%s)", tc_id, cp.current_session_count)

    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)
    flash(f"{cp.ct['name']} parada.", "info")
    return redirect(url_for("index"))

@tc_bp.route("/sse/tc/<int:tc_id>")
@login_required
def sse_tc(tc_id):
    u = current_user()
    if not user_can_view_tc(u, tc_id):
        return "forbidden", 403

    cp = tc_runtime.get(tc_id)
    if not cp:
        tc_row = get_tc(tc_id)
        if not tc_row:
            return "TC nÃÂ£o encontrada", 404
        cp = _ensure_cp(tc_row)

    def stream():
        while True:
            # Inclui status do banco para maior robustez (pÃÂ¡ginas que chegaram depois do START)
            try:
                db_row = get_active_session_by_ct(tc_id)
                db_status = (db_row.get("status") if db_row else None)
                db_total = db_row.get("total_final") if db_row else None
            except Exception:
                db_status = None
                db_total = None

            payload = {
                "session_active": cp.session_active,
                "lote": cp.session_lote,
                "data": cp.session_data,
                "hora_inicio": cp.session_hora_inicio,
                "count": int(cp.current_session_count),
                "fonte": cp.source_type,
                "contagem_alvo": cp.session_contagem_alvo,
                "db_status": db_status,
                "db_total_final": db_total,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1)

    return Response(stream(), mimetype="text/event-stream")

@tc_bp.route("/tc/<int:tc_id>/video")
@login_required
def tc_video(tc_id):
    # Apenas admin pode abrir vÃÂ­deo individual
    u = current_user()
    if u["role"] != "admin":
        return "forbidden", 403

    cp = tc_runtime.get(tc_id)
    if not cp or not cp.session_active:
        return "Nenhuma sessÃÂ£o ativa para esta TC.", 404

    def gen():
        frame = None
        raw = None
        while True:
            # Encerra imediatamente o streaming quando a sessÃÂ£o parar
            if not cp.session_active:
                break
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

    # Stream MJPEG com boundary 'frame'
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')
