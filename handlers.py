import os
import io
import json
import tempfile
import shutil
import threading
import cgi
from urllib.parse import urlparse, parse_qs
from http.server import SimpleHTTPRequestHandler

from processing.pipeline import (
    process_step_file_async,
    PROGRESS, MESH_FILES, HOLE_DATA, SELECTED_HOLES
)
from processing.export import generate_export_json
from config import WEB_ROOT
from geometry.utils import make_uid

class UploadHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/upload_step":
            ctype, pdict = cgi.parse_header(self.headers.get('Content-Type', ''))
            if ctype != 'multipart/form-data':
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Expected multipart/form-data')
                return

            pdict['boundary'] = bytes(pdict['boundary'], "utf-8")
            pdict['CONTENT-LENGTH'] = int(self.headers.get('Content-Length', 0))
            fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={'REQUEST_METHOD':'POST'}, keep_blank_values=True)

            if 'stepfile' not in fs:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing stepfile field')
                return

            fileitem = fs['stepfile']
            if not fileitem.filename:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'No filename provided')
                return

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fileitem.filename)[1] or ".stp")
            try:
                shutil.copyfileobj(fileitem.file, tmp)
                tmp.close()
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'Failed to save upload: {e}'.encode())
                return

            uid = make_uid()
            PROGRESS[uid] = {'percent': 0, 'status': 'Queued'}

            t = threading.Thread(target=process_step_file_async, args=(uid, tmp.name), daemon=True)
            t.start()

            resp = {'uid': uid}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
            return

        return SimpleHTTPRequestHandler.do_POST(self)

    def do_GET(self):
        parsed = urlparse(self.path)

        # progress endpoint
        if parsed.path == "/progress":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]
            if not uid or uid not in PROGRESS:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"uid not found"}')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(PROGRESS[uid]).encode())
            return

        # mesh file serving
        if parsed.path.startswith("/mesh/"):
            parts = parsed.path.split("/")
            if len(parts) >= 3:
                filename = parts[-1]
                mesh_path = os.path.join(WEB_ROOT, filename)
                if os.path.isfile(mesh_path):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    with open(mesh_path, 'rb') as f:
                        shutil.copyfileobj(f, self.wfile)
                    return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')
            return

        # holes file serving
        if parsed.path.startswith("/holes/"):
            parts = parsed.path.split("/")
            if len(parts) >= 3:
                filename = parts[-1]
                hole_path = os.path.join(WEB_ROOT, filename)
                if os.path.isfile(hole_path):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    with open(hole_path, 'rb') as f:
                        shutil.copyfileobj(f, self.wfile)
                    return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not found')
            return

        # toggle selection
        if parsed.path == "/api/toggle":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]
            # if user sends id=0 as a noop, return current selection
            try:
                hole_id = int(qs.get('id', [0])[0])
            except:
                hole_id = 0

            if uid not in SELECTED_HOLES:
                SELECTED_HOLES[uid] = set()

            if hole_id == 0:
                # return current selection
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'selected': list(SELECTED_HOLES[uid])}).encode())
                return

            if hole_id in SELECTED_HOLES[uid]:
                SELECTED_HOLES[uid].remove(hole_id)
            else:
                SELECTED_HOLES[uid].add(hole_id)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'selected': list(SELECTED_HOLES[uid])}).encode())
            return

        # export
        if parsed.path == "/api/export":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]

            if not uid or uid not in HOLE_DATA or uid not in SELECTED_HOLES:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"No data for uid"}')
                return

            selected_ids = SELECTED_HOLES[uid]
            holes = HOLE_DATA[uid]
            export_data, filename = generate_export_json(holes, selected_ids, uid)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'success', 'count': len(export_data['repositionPointDataArray']), 'file': filename}).encode())
            return

        # delete
        if parsed.path == "/delete":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]
            if not uid:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing uid')
                return
            mesh_path = os.path.join(WEB_ROOT, f"mesh_{uid}.json")
            hole_path = os.path.join(WEB_ROOT, f"holes_{uid}.json")
            if os.path.exists(mesh_path):
                try:
                    os.remove(mesh_path)
                except:
                    pass
            if os.path.exists(hole_path):
                try:
                    os.remove(hole_path)
                except:
                    pass
            PROGRESS.pop(uid, None)
            MESH_FILES.pop(uid, None)
            HOLE_DATA.pop(uid, None)
            SELECTED_HOLES.pop(uid, None)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"deleted"}')
            return

        # Fallback: serve static files from WEB_ROOT (viewer.html etc.)
        cwd = os.getcwd()
        try:
            os.chdir(WEB_ROOT)
            return SimpleHTTPRequestHandler.do_GET(self)
        finally:
            os.chdir(cwd)