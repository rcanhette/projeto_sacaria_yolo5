# app.py
import logging
from flask import Flask, redirect, url_for, render_template, request
from routes.ct import ct_bp
from routes.logs import logs_bp
from routes.auth import auth_bp, current_user
from routes.user_admin import user_admin_bp
from routes.ct_admin import ct_admin_bp
from services.ct_repository import list_cts
from services.runtime import ct_runtime
import atexit
from services.db import ensure_schema
from services.auth_repository import list_user_ct_ids, user_can_control_ct

def create_app():
    # ---- LOGGING ----
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    log = logging.getLogger("app")

    app = Flask(__name__)
    # Em produção, use variável de ambiente segura:
    app.secret_key = "supersecret"

    # Apenas garante o schema (nenhum seed automático)
    log.info("Garantindo schema...")
    ensure_schema()

    # Blueprints
    app.register_blueprint(auth_bp)        # /login, /logout
    app.register_blueprint(ct_bp)          # /ct/<id>, start/stop/SSE etc.
    app.register_blueprint(logs_bp)        # /logs
    app.register_blueprint(user_admin_bp)  # /users, /user-access-ct
    app.register_blueprint(ct_admin_bp)    # /ct-admin (CRUD de CTs)

    # Disponibiliza current_user() nos templates (ex.: _navbar.html)
    @app.context_processor
    def inject_current_user():
        return {"current_user": current_user}

    # Força login para tudo, exceto login/logout/static
    @app.before_request
    def require_login_guard():
        exempt = {"auth.login", "auth.logout", "static"}
        if request.endpoint not in exempt and not current_user():
            # preserva next para redirecionar após login
            return redirect(url_for("auth.login", next=request.path))

    # Dashboard principal (acompanhamento)
    @app.route("/")
    def index():
        """
        Mostra as CTs visíveis para o usuário logado.
        - admin/supervisor: vê todas
        - operator/viewer : vê apenas CTs vinculadas
        Também marca "can_control" por CT (start/stop liberado para admin/supervisor/operator).
        """
        u = current_user()
        all_cts = list_cts()

        if u["role"] in ("admin", "supervisor"):
            allowed = all_cts
        else:
            ids = set(list_user_ct_ids(u["id"]))
            allowed = [ct for ct in all_cts if ct["id"] in ids]

        cts_view = []
        for ct in allowed:
            row = dict(ct)
            row["can_control"] = user_can_control_ct(u, ct["id"])
            cts_view.append(row)

        return render_template("ct_dashboard.html", cts=cts_view, role=u["role"])

    # Atalho de menu
    @app.route("/acompanhamento")
    def acompanhamento():
        return redirect(url_for("index"))

    return app


if __name__ == "__main__":
    # Shutdown limpo: libera todas as CTs na saída do processo
    @atexit.register
    def _shutdown_release_all():
        try:
            for cp in list(ct_runtime.values()):
                try:
                    cp.release()
                except Exception:
                    pass
        except Exception:
            pass

    app = create_app()
    app.logger.info("Iniciando servidor Flask em 0.0.0.0:8080 (debug=True, use_reloader=False)")
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False)
