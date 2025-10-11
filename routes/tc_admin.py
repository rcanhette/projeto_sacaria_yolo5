from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.tc_repository import list_tcs, get_tc, create_tc, update_tc, delete_tc
from services.runtime import drop_tc_runtime
from routes.auth import role_required

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
    return render_template("tc_admin_list.html", tcs=tcs)

@tc_admin_bp.route("/tc-admin/<int:tc_id>/edit", methods=["GET", "POST"])
def tc_admin_edit(tc_id):
    tc = get_tc(tc_id)
    if request.method == "GET":
        return render_template("tc_admin_edit.html", ct=tc)
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
