from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.tc_repository import list_tcs, get_tc, create_tc, update_tc, delete_tc
from services.agent_repository import get_effective_tc_status, get_host_for_tc
from services.runtime import drop_tc_runtime
from routes.auth import role_required
import requests

tc_admin_bp = Blueprint("tc_admin", __name__)

@tc_admin_bp.before_request
@role_required("admin")
def _only_admin():
    pass

def _parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _normalize_flow(flow: str) -> str:
    flow_norm = (flow or "cima").strip().lower()
    return flow_norm if flow_norm in ("cima", "baixo", "sem_fluxo") else "cima"

def _parse_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

@tc_admin_bp.route("/tc-admin")
def tc_admin_list():
    tcs = list_tcs()
    from datetime import datetime, timedelta
    enriched = []
    for ct in tcs:
        row = dict(ct)
        online = False
        hostname = None
        last_seen = None
        try:
            st = get_effective_tc_status(ct["id"])  # {tc_id, last_seen, hostname, status}
            if st:
                hostname = st.get("hostname")
                last_seen = st.get("last_seen")
                status = (st.get("status") or "").strip().lower()
                if last_seen:
                    try:
                        now = datetime.now(last_seen.tzinfo) if hasattr(last_seen, "tzinfo") else datetime.now()
                        online = (now - last_seen) <= timedelta(seconds=40)
                    except Exception:
                        pass
                if status == "offline":
                    online = False
        except Exception:
            pass
        row["agent_online"] = online
        row["agent_hostname"] = hostname
        row["agent_last_seen"] = last_seen
        enriched.append(row)
    return render_template("tc_admin_list.html", tcs=enriched)

@tc_admin_bp.route("/tc-admin/<int:tc_id>/edit", methods=["GET", "POST"])
def tc_admin_edit(tc_id):
    tc = get_tc(tc_id)
    if request.method == "GET":
        # status do agente (ultima conexao) quando houver
        st = None
        try:
            st = get_effective_tc_status(tc_id)
        except Exception:
            st = None
        return render_template("tc_admin_edit.html", ct=tc, agent_status=st)
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    line_offset_red = _parse_int(request.form.get("line_offset_red"), 40)
    line_offset_blue = _parse_int(request.form.get("line_offset_blue"), -40)
    flow_mode = _normalize_flow(request.form.get("flow_mode"))
    max_lost = _parse_int(request.form.get("max_lost"), 2)
    match_dist = _parse_float(request.form.get("match_dist"), 150)
    min_conf = _parse_float(request.form.get("min_conf"), 0.8)
    missed_frame_dir = (request.form.get("missed_frame_dir") or "").strip()
    if max_lost < 0:
        max_lost = 0
    if match_dist <= 0:
        match_dist = 1.0
    match_dist = int(round(match_dist))
    if min_conf < 0:
        min_conf = 0.0
    if min_conf > 1:
        min_conf = 1.0
    update_tc(tc_id, name, source_path, roi, model_path,
              line_offset_red, line_offset_blue, flow_mode,
              max_lost, match_dist, min_conf, missed_frame_dir)
    drop_tc_runtime(tc_id)
    flash("TC atualizada.", "success")
    return redirect(url_for("tc_admin.tc_admin_list"))

@tc_admin_bp.route("/tc-admin/new", methods=["GET", "POST"])
def tc_admin_new():
    if request.method == "GET":
        return render_template("tc_admin_edit.html", ct=None)
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    line_offset_red = _parse_int(request.form.get("line_offset_red"), 40)
    line_offset_blue = _parse_int(request.form.get("line_offset_blue"), -40)
    flow_mode = _normalize_flow(request.form.get("flow_mode"))
    max_lost = _parse_int(request.form.get("max_lost"), 2)
    match_dist = _parse_float(request.form.get("match_dist"), 150)
    min_conf = _parse_float(request.form.get("min_conf"), 0.8)
    missed_frame_dir = (request.form.get("missed_frame_dir") or "").strip()
    if max_lost < 0:
        max_lost = 0
    if match_dist <= 0:
        match_dist = 1.0
    match_dist = int(round(match_dist))
    if min_conf < 0:
        min_conf = 0.0
    if min_conf > 1:
        min_conf = 1.0
    create_tc(name, source_path, roi, model_path,
              line_offset_red, line_offset_blue, flow_mode,
              max_lost, match_dist, min_conf, missed_frame_dir)
    flash("TC criada.", "success")
    return redirect(url_for("tc_admin.tc_admin_list"))

@tc_admin_bp.route("/tc-admin/<int:tc_id>/delete", methods=["POST"])
def tc_admin_delete(tc_id):
    delete_tc(tc_id)
    flash("TC removida.", "info")
    return redirect(url_for("tc_admin.tc_admin_list"))

@tc_admin_bp.route("/tc-admin/<int:tc_id>/calibrate", methods=["GET", "POST"])
def tc_admin_calibrate(tc_id):
    tc = get_tc(tc_id)
    if not tc:
        flash("TC não encontrada.", "error")
        return redirect(url_for("tc_admin.tc_admin_list"))
    if request.method == "GET":
        st = None
        try:
            st = get_effective_tc_status(tc_id)
        except Exception:
            st = None
        return render_template("tc_admin_calibrate.html", ct=tc, agent_status=st)

    # POST: salvar ajustes de ROI/linhas e parâmetros opcionais
    def _parse_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    def _parse_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    def _normalize_flow(flow: str) -> str:
        flow_norm = (flow or "cima").strip().lower()
        return flow_norm if flow_norm in ("cima", "baixo", "sem_fluxo") else "cima"

    name = tc.get("name")
    source_path = tc.get("source_path")
    model_path = tc.get("model_path")
    roi = (request.form.get("roi") or str(tc.get("roi") or "")).strip()
    line_offset_red = _parse_int(request.form.get("line_offset_red"), int(tc.get("line_offset_red") or 40))
    line_offset_blue = _parse_int(request.form.get("line_offset_blue"), int(tc.get("line_offset_blue") or -40))
    flow_mode = _normalize_flow(request.form.get("flow_mode") or tc.get("flow_mode") or "cima")
    max_lost = _parse_int(request.form.get("max_lost"), int(tc.get("max_lost") or 2))
    match_dist = _parse_float(request.form.get("match_dist"), float(tc.get("match_dist") or 150))
    min_conf = _parse_float(request.form.get("min_conf"), float(tc.get("min_conf") or 0.8))
    missed_frame_dir = (request.form.get("missed_frame_dir") or tc.get("missed_frame_dir") or "").strip()
    # streaming persistente (opcional)
    stream_fps = request.form.get("stream_fps")
    stream_quality = request.form.get("stream_quality")
    try:
        stream_fps = int(stream_fps) if stream_fps not in (None, "") else None
    except Exception:
        stream_fps = None
    try:
        stream_quality = int(stream_quality) if stream_quality not in (None, "") else None
    except Exception:
        stream_quality = None
    if stream_fps is not None:
        stream_fps = max(1, min(60, stream_fps))
    if stream_quality is not None:
        stream_quality = max(30, min(95, stream_quality))

    if max_lost < 0:
        max_lost = 0
    if match_dist <= 0:
        match_dist = 1.0
    match_dist = int(round(match_dist))
    if min_conf < 0:
        min_conf = 0.0
    if min_conf > 1:
        min_conf = 1.0

    update_tc(tc_id, name, source_path, roi, model_path,
              line_offset_red, line_offset_blue, flow_mode,
              max_lost, match_dist, min_conf, missed_frame_dir,
              stream_fps, stream_quality)
    drop_tc_runtime(tc_id)
    flash("Calibração salva.", "success")
    return redirect(url_for("tc_admin.tc_admin_calibrate", tc_id=tc_id))

@tc_admin_bp.route("/tc-admin/<int:tc_id>/test-agent", methods=["POST"])
def tc_admin_test_agent(tc_id: int):
    tc = get_tc(tc_id)
    if not tc:
        flash("TC não encontrada.", "error")
        return redirect(url_for("tc_admin.tc_admin_list"))
    host = None
    try:
        host = get_host_for_tc(tc_id)
    except Exception:
        host = None
    if not host:
        flash("Agente não identificado (sem heartbeat recente). Abra o agente e tente novamente.", "error")
        return redirect(url_for("tc_admin.tc_admin_edit", tc_id=tc_id))
    try:
        r = requests.get(f"http://{host}:9090/api/agent/v1/status", timeout=3)
        if r.ok:
            data = r.json()
            running = data.get("running")
            flash(f"Agente em {host}: status OK (running={running}).", "success")
        else:
            flash(f"Agente em {host}: respondeu HTTP {r.status_code}.", "error")
    except Exception as e:
        flash(f"Falha ao contatar agente em {host}: {e}", "error")
    return redirect(url_for("tc_admin.tc_admin_edit", tc_id=tc_id))

@tc_admin_bp.route("/tc-admin/<int:tc_id>/test-ct", methods=["POST"])
def tc_admin_test_ct(tc_id: int):
    tc = get_tc(tc_id)
    if not tc:
        flash("TC não encontrada.", "error")
        return redirect(url_for("tc_admin.tc_admin_list"))
    src = (tc.get("source_path") or "").strip()
    if not src:
        flash("Fonte (URL) não configurada.", "error")
        return redirect(url_for("tc_admin.tc_admin_edit", tc_id=tc_id))
    try:
        from services.video_source import VideoSource
        cam = VideoSource(src)
        ok_any = False
        try:
            import time
            deadline = time.time() + 3.0
            while time.time() < deadline:
                ret, frame = cam.get_frame()
                if ret and frame is not None:
                    ok_any = True
                    break
                time.sleep(0.1)
        finally:
            try:
                cam.release()
            except Exception:
                pass
        if ok_any:
            flash("Conexão da CT OK (frame capturado).", "success")
        else:
            flash("Não foi possível obter frame (tempo esgotado).", "error")
    except Exception as e:
        flash(f"Falha ao abrir fonte: {e}", "error")
    return redirect(url_for("tc_admin.tc_admin_edit", tc_id=tc_id))

