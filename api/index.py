# Vercel serverless entrypoint: exposes the Flask WSGI app.
# Vercel rewrites every path to /api/index; strip that prefix so Flask
# sees the original route ("/", "/upload", ...).
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as flask_app  # noqa: E402


def app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    for prefix in ("/api/index", "/api"):
        if path.startswith(prefix):
            environ["PATH_INFO"] = path[len(prefix):] or "/"
            break
    return flask_app(environ, start_response)
