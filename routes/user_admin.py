# routes/user_admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from routes.auth import role_required
from services.ct_repository import list_cts
from services.auth_repository import (
    list_users, get_user_by_id, create_user, update_user, reset_password, delete_user,
    list_users_by_role, list_user_ids_for_ct, set_ct_users,
)

user_admin_bp = Blueprint("user_admin", __name__)

@user_admin_bp.before_request
@role_required("admin")
def _only_admin():
    """Apenas admin em todas as rotas deste blueprint."""
    pass

# =========================
# CRUD de Usuários (Admin)
# =========================
@user_admin_bp.route("/users", methods=["GET"])
def users_list():
    users = list_users()
    return render_template("users_list.html", users=users)

@user_admin_bp.route("/users/new", methods=["GET", "POST"])
def users_new():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        role     = (request.form.get("role") or "").strip()
        active   = bool(request.form.get("active"))
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Usuário e senha são obrigatórios.", "error")
            return render_template("user_form.html", mode="new", user=None)

        try:
            create_user(username=username, password=password, role=role, active=active)
            flash("Usuário criado com sucesso.", "success")
            return redirect(url_for("user_admin.users_list"))
        except Exception as e:
            flash(f"Erro ao criar usuário: {e}", "error")
            return render_template("user_form.html", mode="new", user=None)

    # GET
    return render_template("user_form.html", mode="new", user=None)

@user_admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def users_edit(user_id: int):
    u = get_user_by_id(user_id)
    if not u:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("user_admin.users_list"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        role     = (request.form.get("role") or "").strip()
        active   = bool(request.form.get("active"))
        new_pwd  = request.form.get("password") or ""

        try:
            update_user(user_id=user_id, username=username, role=role, active=active)
            if new_pwd.strip():
                reset_password(user_id=user_id, new_password=new_pwd)
            flash("Usuário atualizado com sucesso.", "success")
            return redirect(url_for("user_admin.users_list"))
        except Exception as e:
            flash(f"Erro ao atualizar: {e}", "error")
            u = get_user_by_id(user_id)
            return render_template("user_form.html", mode="edit", user=u)

    # GET
    return render_template("user_form.html", mode="edit", user=u)

@user_admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
def users_delete(user_id: int):
    try:
        delete_user(user_id)
        flash("Usuário excluído.", "success")
    except Exception as e:
        flash(f"Erro ao excluir: {e}", "error")
    return redirect(url_for("user_admin.users_list"))

# =========================
# Acessos (CT × Usuários)
# =========================
@user_admin_bp.route("/user-access-ct", methods=["GET"])
def user_access_by_ct_panel():
    """
    Tela centrada em CT: para cada CT, escolher usuários (operadores e visualizadores)
    que terão acesso.
    """
    cts = list_cts()
    operators = list_users_by_role(["operator"])
    viewers   = list_users_by_role(["viewer"])
    ct_user_map = { ct["id"]: list(list_user_ids_for_ct(ct["id"])) for ct in cts }
    return render_template("user_access_ct.html", cts=cts, operators=operators, viewers=viewers, ct_user_map=ct_user_map)

@user_admin_bp.route("/user-access-ct/<int:ct_id>", methods=["POST"])
def user_access_by_ct_update(ct_id:int):
    op_ids = [int(x) for x in request.form.getlist("op_user_id")]
    vw_ids = [int(x) for x in request.form.getlist("vw_user_id")]
    new_user_ids = sorted(set(op_ids + vw_ids))
    set_ct_users(ct_id, new_user_ids)
    flash(f"Acessos atualizados para CT {ct_id}.", "success")
    return redirect(url_for("user_admin.user_access_by_ct_panel"))
