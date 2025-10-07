from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.ct_repository import list_cts, get_ct, create_ct, update_ct, delete_ct
from services.runtime import drop_ct_runtime
from routes.auth import role_required

ct_admin_bp = Blueprint("ct_admin", __name__)

@ct_admin_bp.before_request
@role_required("admin")
def _only_admin():
    pass

@ct_admin_bp.route("/ct-admin")
def ct_admin_list():
    cts = list_cts()
    return render_template("ct_admin_list.html", cts=cts)

@ct_admin_bp.route("/ct-admin/<int:ct_id>/edit", methods=["GET", "POST"])
def ct_admin_edit(ct_id):
    ct = get_ct(ct_id)
    if request.method == "GET":
        return render_template("ct_admin_edit.html", ct=ct)
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    update_ct(ct_id, name, source_path, roi, model_path)
    drop_ct_runtime(ct_id)
    flash("CT atualizada.", "success")
    return redirect(url_for("ct_admin.ct_admin_list"))

@ct_admin_bp.route("/ct-admin/new", methods=["GET", "POST"])
def ct_admin_new():
    if request.method == "GET":
        return render_template("ct_admin_new.html")
    name = request.form.get("name","").strip()
    source_path = request.form.get("source_path","").strip()
    roi = request.form.get("roi","").strip()
    model_path = request.form.get("model_path","").strip()
    create_ct(name, source_path, roi, model_path)
    flash("CT criada.", "success")
    return redirect(url_for("ct_admin.ct_admin_list"))

@ct_admin_bp.route("/ct-admin/<int:ct_id>/delete", methods=["POST"])
def ct_admin_delete(ct_id):
    delete_ct(ct_id)
    flash("CT removida.", "info")
    return redirect(url_for("ct_admin.ct_admin_list"))
