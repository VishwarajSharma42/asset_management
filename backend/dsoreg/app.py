"""Application factory."""
from __future__ import annotations

from flask import Flask, abort, redirect, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Settings
from .context import Ctx
from .db import make_engine, metadata
from .errors import init_errors
from .routes import auth, device, public, staff

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def create_app(settings: Settings | None = None, engine=None) -> Flask:
    settings = settings or Settings.from_env()
    engine = engine or make_engine(settings.database_url)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_body_bytes
    app.json.sort_keys = False
    if settings.trust_proxy_hops:
        n = settings.trust_proxy_hops
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    init_errors(app)
    if not settings.production:
        metadata.create_all(engine)  # production schema is created explicitly: `cli init-db`
    app.extensions["dsoreg"] = Ctx.build(settings, engine)

    for module in (public, device, auth, staff):
        app.register_blueprint(module.bp)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if settings.production and settings.hsts:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        from flask import request
        if request.path.startswith("/api/") or request.path == "/health":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    if settings.serve_frontend:
        _serve_frontend(app, settings.frontend_dir)
    return app


def _serve_frontend(app: Flask, root: str) -> None:
    """Development convenience. In production nginx serves these directly (deploy/nginx.conf)."""
    @app.get("/")
    def _root():
        return redirect("/app/", code=302)

    @app.get("/c")
    def _scan_page():
        return send_from_directory(root, "scan.html", max_age=0)

    @app.get("/app/")
    def _app_index():
        return send_from_directory(f"{root}/app", "index.html", max_age=0)

    @app.get("/app/<path:name>")
    def _app_files(name: str):
        return send_from_directory(f"{root}/app", name, max_age=0)

    @app.get("/css/<path:name>")
    def _css(name: str):
        return send_from_directory(f"{root}/css", name, max_age=0)

    @app.get("/js/<path:name>")
    def _js(name: str):
        return send_from_directory(f"{root}/js", name, max_age=0)


def wsgi():  # gunicorn "dsoreg.app:wsgi()"
    return create_app()
