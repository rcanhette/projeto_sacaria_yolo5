from flask import Blueprint, render_template, request, redirect, url_for, session, flash, request as req
from functools import wraps
from services.auth_repository import (
    get_user_by_username, get_user_by_id, verify_password, user_can_view_tc
)

auth_bp = Blueprint("auth", __name__)

# ----- Helpers -----
def current_user():
    uid = session.get("uid")
    return get_user_by_id(uid) if uid else None

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=req.path))
        return f(*args, **kwargs)
    return wrapper

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            u = current_user()
            if not u:
                return redirect(url_for("auth.login", next=req.path))
            if u["role"] not in roles:
                flash("Acesso negado.", "error")
                return redirect(url_for("index"))
            return f(*args, **kwargs)
        return wrapper
    return deco

def require_ct_access(ct_id:int):
    u = current_user()
    return user_can_view_tc(u, ct_id)

# ----- Rotas -----
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    username = request.form.get("username","").strip().lower()
    password = request.form.get("password","")
    u = get_user_by_username(username)
    if not u or not verify_password(u, password):
        flash("Usuário ou senha inválidos.", "error")
        return render_template("login.html"), 401
    session["uid"] = u["id"]
    flash(f"Bem-vindo, {username}!", "success")
    nxt = request.args.get("next") or url_for("index")
    return redirect(nxt)

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
