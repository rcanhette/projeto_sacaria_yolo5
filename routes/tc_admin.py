from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.tc_repository import list_tcs, get_tc, create_tc, update_tc, delete_tc
from services.runtime import drop_tc_runtime
from routes.auth import role_required

tc_admin_bp = Blueprint("tc_admin", __name__)

@tc_admin_bp.before_request
@role_required("admin")
def _only_admin():
    pass

@tc_admin_bp.route("/tc-admin")
def tc_admin_list():
    tcs = list_tcs()
    return render_template("tc_admin_list.html", tcs=tcs)

@tc_admin_bp.route("/tc-admin/<int:tc_id>/edit", methods=["GET", "POST"])
def tc_admin_edit(tc_id):
    tc = get_tc(tc_id)
    if request.method == "GET":
        return render_template("tc_admin_edit.html", tc=tc)
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    update_tc(tc_id, name, source_path, roi, model_path)
    drop_tc_runtime(tc_id)
    flash("TC atualizada.", "success")
    return redirect(url_for("tc_admin.tc_admin_list"))

@tc_admin_bp.route("/tc-admin/new", methods=["GET", "POST"])
def tc_admin_new():
    if request.method == "GET":
        return render_template("tc_admin_new.html")
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    create_tc(name, source_path, roi, model_path)
    flash("TC criada.", "success")
    return redirect(url_for("tc_admin.tc_admin_list"))

@tc_admin_bp.route("/tc-admin/<int:tc_id>/delete", methods=["POST"])
def tc_admin_delete(tc_id):
    delete_tc(tc_id)
    flash("TC removida.", "info")
    return redirect(url_for("tc_admin.tc_admin_list"))

