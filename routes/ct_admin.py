# routes/ct_admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from services.ct_repository import list_cts, get_ct, create_ct, update_ct, delete_ct
from services.runtime import drop_ct_runtime

ct_admin_bp = Blueprint("ct_admin", __name__)

def _validate_roi(roi:str) -> bool:
    try:
        parts = [p.strip() for p in roi.split(",")]
        return len(parts) == 4 and all(part.isdigit() for part in parts)
    except Exception:
        return False

@ct_admin_bp.route("/ct-admin")
def ct_admin_list():
    cts = list_cts()
    return render_template("ct_admin_list.html", cts=cts)

@ct_admin_bp.route("/ct-admin/new", methods=["GET", "POST"])
def ct_admin_new():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        source_path = (request.form.get("source_path") or "").strip()
        roi = (request.form.get("roi") or "").strip()
        model_path = (request.form.get("model_path") or "").strip()

        if not name or not source_path or not roi or not model_path:
            flash("Todos os campos são obrigatórios.", "error")
            return redirect(url_for("ct_admin.ct_admin_new"))

        if not _validate_roi(roi):
            flash("ROI inválido. Use o formato: x,y,w,h (somente números).", "error")
            return redirect(url_for("ct_admin.ct_admin_new"))

        new_id = create_ct(name, source_path, roi, model_path)
        flash(f"CT criada (id={new_id}).", "success")
        return redirect(url_for("ct_admin.ct_admin_list"))

    return render_template("ct_admin_form.html", mode="new", ct=None)

@ct_admin_bp.route("/ct-admin/<int:ct_id>/edit", methods=["GET", "POST"])
def ct_admin_edit(ct_id):
    row = get_ct(ct_id)
    if not row:
        flash("CT não encontrada.", "error")
        return redirect(url_for("ct_admin.ct_admin_list"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        source_path = (request.form.get("source_path") or "").strip()
        roi = (request.form.get("roi") or "").strip()
        model_path = (request.form.get("model_path") or "").strip()

        if not name or not source_path or not roi or not model_path:
            flash("Todos os campos são obrigatórios.", "error")
            return redirect(url_for("ct_admin.ct_admin_edit", ct_id=ct_id))

        if not _validate_roi(roi):
            flash("ROI inválido. Use o formato: x,y,w,h (somente números).", "error")
            return redirect(url_for("ct_admin.ct_admin_edit", ct_id=ct_id))

        update_ct(ct_id, name, source_path, roi, model_path)

        # reseta runtime desta CT para que nova config entre em vigor
        drop_ct_runtime(ct_id)

        flash("CT atualizada.", "success")
        return redirect(url_for("ct_admin.ct_admin_list"))

    return render_template("ct_admin_form.html", mode="edit", ct=row)

@ct_admin_bp.route("/ct-admin/<int:ct_id>/delete", methods=["POST"])
def ct_admin_delete(ct_id):
    # limpar runtime se existir
    drop_ct_runtime(ct_id)
    delete_ct(ct_id)
    flash("CT removida.", "info")
    return redirect(url_for("ct_admin.ct_admin_list"))
