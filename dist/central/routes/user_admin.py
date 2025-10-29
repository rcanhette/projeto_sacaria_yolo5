# routes/user_admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from routes.auth import role_required
from services.tc_repository import list_tcs
from services.auth_repository import (
    list_users, get_user_by_id, create_user, update_user, reset_password, delete_user,
    list_users_by_role, list_user_ids_for_tc, set_tc_users,
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
# Acessos (TC × Usuários)
# =========================
@user_admin_bp.route("/user-access-tc", methods=["GET"])
def user_access_by_tc_panel():
    """
    Tela centrada em TC: para cada TC, escolher usuários (operadores e visualizadores)
    que terão acesso.
    """
    tcs = list_tcs()
    operators = list_users_by_role(["operator"])
    viewers   = list_users_by_role(["viewer"])
    tc_user_map = { tc["id"]: list(list_user_ids_for_tc(tc["id"])) for tc in tcs }
    return render_template("user_access_tc.html", tcs=tcs, operators=operators, viewers=viewers, tc_user_map=tc_user_map)

@user_admin_bp.route("/user-access-tc/<int:tc_id>", methods=["POST"])
def user_access_by_tc_update(tc_id:int):
    op_ids = [int(x) for x in request.form.getlist("op_user_id")]
    vw_ids = [int(x) for x in request.form.getlist("vw_user_id")]
    new_user_ids = sorted(set(op_ids + vw_ids))
    set_tc_users(tc_id, new_user_ids)
    flash(f"Acessos atualizados para TC {tc_id}.", "success")
    return redirect(url_for("user_admin.user_access_by_tc_panel"))







