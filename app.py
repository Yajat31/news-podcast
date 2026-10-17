"""Sarvam Pod web app: a plain WSGI app. Vercel serves `app` directly; `python app.py` runs it locally."""
import json
import os
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from core import ROUTES, handle

INDEX = Path(__file__).with_name("index.html")


def app(environ, start_response):
    method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")

    if method == "GET" and path in ("/", "/index.html"):
        body = INDEX.read_bytes()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))])
        return [body]

    name = path.removeprefix("/api/")
    if method == "POST" and name in ROUTES:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        data = environ["wsgi.input"].read(length) if length else b""
        code, result = handle(name, data, {"Content-Type": environ.get("CONTENT_TYPE", "")})
        body = json.dumps(result).encode()
        status = {200: "200 OK", 400: "400 Bad Request"}.get(code, f"{code} Bad Gateway")
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        return [body]

    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"not found"]


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Sarvam Pod on http://localhost:{port}")
    make_server("", port, app, server_class=ThreadingWSGIServer).serve_forever()
