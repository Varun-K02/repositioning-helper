import webview
import threading
import time
import requests

from server import start_server, stop_server
from config import PORT


def wait_for_server(url, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            requests.get(url)
            return True
        except:
            time.sleep(0.2)
    return False


def open_app_window():
    url = f"http://localhost:{PORT}/viewer.html"

    # Start server in background
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Splash window (main thread)
    splash = webview.create_window(
        "Loading CAD Tool...",
        "web/splash.html",
        width=500,
        height=300,
        frameless=True,
        easy_drag=True,
        resizable=False
    )

    # This runs inside the pywebview event loop
    def on_loaded():
        # Wait for server to be ready
        if wait_for_server(url):
            splash.load_url(url)
            splash.toggle_fullscreen()
        else:
            splash.load_html("<h1>Server failed to start.</h1>")

    # When UI closes, stop server cleanly
    def on_closed():
        stop_server()

    # attach window close event
    splash.events.closed += on_closed

    # Start GUI - Note: only func available in your pywebview version
    webview.start(on_loaded, gui='qt')
