# app.py
import logging
from pathlib import Path
from logging.config import dictConfig
from flask import Flask, redirect, url_for, render_template, request
from routes.tc import tc_bp
from routes.logs import logs_bp
from routes.agent import agent_bp
from routes.auth import auth_bp, current_user
from routes.user_admin import user_admin_bp
from routes.tc_admin import tc_admin_bp
from services.tc_repository import list_tcs
from services.runtime import tc_runtime
import atexit
from services.db import ensure_schema
from services.session_repository import close_all_active_sessions_on_boot
from services.auth_repository import list_user_tc_ids, user_can_control_tc
from services.agent_repository import get_tc_status
from datetime import datetime, timedelta


LOGS_DIR = Path(__file__).resolve().parent / "logs"


def _configure_logging() -> None:
    if getattr(_configure_logging, "_configured", False):
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                }
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "level": "INFO",
                    "stream": "ext://sys.stdout",
                    "formatter": "standard",
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "INFO",
                    "formatter": "standard",
                    "filename": str(LOGS_DIR / "app_runtime.log"),
                    "maxBytes": 10 * 1024 * 1024,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "root": {
                "level": "INFO",
                "handlers": ["stdout", "file"],
            },
        }
    )

    logging.getLogger("services").setLevel(logging.INFO)
    logging.getLogger("services.capture_point").setLevel(logging.INFO)
    logging.getLogger("services.video_source").setLevel(logging.INFO)
    logging.getLogger("services.industrial_tag_detector").setLevel(logging.INFO)

    _configure_logging._configured = True

def create_app():
    # ---- LOGGING ----
    _configure_logging()
    log = logging.getLogger("app")

    app = Flask(__name__)
    # Em produção, use variável de ambiente segura:
    app.secret_key = "supersecret"

    # Apenas garante o schema (nenhum seed automático)
    log.info("Garantindo schema...")
    ensure_schema()

    # Ao iniciar, finalize sessões que ficaram 'ativas' no banco (queda do processo)
    try:
        affected = close_all_active_sessions_on_boot(final_status="finalizado")
        if affected:
            log.info(f"Sessões ativas remanescentes finalizadas no boot: {affected}")
    except Exception as e:
        log.warning(f"Falha ao finalizar sessões remanescentes no boot: {e}")

    # Blueprints
    app.register_blueprint(auth_bp)        # /login, /logout
    app.register_blueprint(tc_bp)          # /tc/<id>, start/stop/SSE etc.
    app.register_blueprint(logs_bp)        # /logs
    app.register_blueprint(user_admin_bp)  # /users, /user-access-tc
    app.register_blueprint(tc_admin_bp)    # /tc-admin (CRUD de TCs)
    app.register_blueprint(agent_bp)       # /api/agent/v1 (ingestão de agentes)

    # Disponibiliza current_user() nos templates (ex.: _navbar.html)
    @app.context_processor
    def inject_current_user():
        return {"current_user": current_user}

    # Força login para tudo, exceto login/logout/static
    @app.before_request
    def require_login_guard():
        # Health sem login
        if request.path == "/health":
            return None
        exempt = {"auth.login", "auth.logout", "static"}
        # Libera as APIs do agente sem exigir login
        if request.endpoint and (request.endpoint in exempt or request.endpoint.startswith("agent.")):
            return None
        if not current_user():
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
        all_cts = list_tcs()

        if u["role"] in ("admin", "supervisor"):
            allowed = all_cts
        else:
            ids = set(list_user_tc_ids(u["id"]))
            allowed = [ct for ct in all_cts if ct["id"] in ids]

        cts_view = []
        for ct in allowed:
            row = dict(ct)
            row["can_control"] = user_can_control_tc(u, ct["id"])

            online = False
            hostname = None
            try:
                st = get_tc_status(ct["id"])
                if st:
                    hostname = st.get("hostname")
                    last_seen = st.get("last_seen")
                    status = (st.get("status") or "").strip().lower()
                    if last_seen:
                        try:
                            now = (
                                datetime.now(last_seen.tzinfo)
                                if hasattr(last_seen, "tzinfo")
                                else datetime.now()
                            )
                            online = (now - last_seen) <= timedelta(seconds=40)
                        except Exception:
                            pass
                    if status == "offline":
                        online = False
            except Exception:
                pass

            row["agent_online"] = online
            row["agent_hostname"] = hostname
            cts_view.append(row)

        return render_template("tc_dashboard.html", cts=cts_view, role=u["role"])

    # Atalho de menu
    @app.route("/acompanhamento")
    def acompanhamento():
        return redirect(url_for("index"))

    # Health/metrics leve (sem login)
    @app.route("/health")
    def health():
        from services.db import query_one
        db_ok = True
        try:
            query_one("SELECT 1")
        except Exception:
            db_ok = False
        import os
        pool_min = os.getenv("DB_POOL_MIN")
        pool_max = os.getenv("DB_POOL_MAX")
        try:
            import routes.tc as tc_routes
            sse_cache = len(getattr(tc_routes, "_sse_db_cache", {}) or {})
            local_streams = len(getattr(tc_routes, "_mjpeg_state", {}) or {})
        except Exception:
            sse_cache = None
            local_streams = None
        return {
            "ok": True,
            "db_ok": db_ok,
            "db_pool": {"min": pool_min, "max": pool_max},
            "sse_cache_size": sse_cache,
            "local_streams": local_streams,
        }, 200

    return app


if __name__ == "__main__":
    # Shutdown limpo: libera todas as CTs na saída do processo
    @atexit.register
    def _shutdown_release_all():
        try:
            for cp in list(tc_runtime.values()):
                try:
                    # Finaliza sessão ativa para marcar data_fim no banco
                    if getattr(cp, "session_active", False) or getattr(cp, "session_db_id", None) is not None:
                        try:
                            cp.stop_session()
                        except Exception:
                            pass
                    cp.release()
                except Exception:
                    pass
        except Exception:
            pass

    app = create_app()
    app.logger.info("Iniciando servidor Flask em 0.0.0.0:8080 (debug=True, use_reloader=False)")
    # Importante: 'threaded=True' para evitar travamentos com SSE/MJPEG no servidor de dev
    app.run(host="0.0.0.0", port=8080, debug=True, use_reloader=False, threaded=True)


