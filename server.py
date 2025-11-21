import os
import threading
import webbrowser
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from handlers import UploadHandler
from web.viewer_template import write_viewer_html
from config import PORT, WEB_ROOT

server_ref = None

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_server():
    global server_ref

    try:
        print("DEBUG: Generating viewer.html...")
        write_viewer_html()

        print("DEBUG: Ensuring WEB_ROOT exists:", WEB_ROOT)
        os.makedirs(WEB_ROOT, exist_ok=True)

        print("DEBUG: Changing directory to WEB_ROOT")
        os.chdir(WEB_ROOT)

        print("DEBUG: Creating server...")
        server_ref = ThreadingHTTPServer(("0.0.0.0", PORT), UploadHandler)

        print(f"DEBUG: Server starting at http://localhost:{PORT}/viewer.html")
        server_ref.serve_forever()

    except Exception as e:
        print("SERVER ERROR:", e)


def stop_server():
    global server_ref

    if server_ref:
        print("Shutting down server...")
        server_ref.shutdown()
        server_ref.server_close()
        server_ref = None