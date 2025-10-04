# app.py
from flask import Flask, redirect, url_for
from routes.ct import ct_bp
from routes.ct_admin import ct_admin_bp
from services.ct_repository import list_cts
from routes.logs import logs_bp

def first_ct_id():
    cts = list_cts()
    return cts[0]["id"] if cts else 1

def create_app():
    app = Flask(__name__)
    app.secret_key = "supersecret"  # se quiser, mova p/ env

    app.register_blueprint(ct_bp)
    app.register_blueprint(ct_admin_bp)
    app.register_blueprint(logs_bp)

    @app.route("/")
    def index():
        return redirect(url_for("ct.ct_detail", ct_id=first_ct_id()))

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=8080, debug=True)
