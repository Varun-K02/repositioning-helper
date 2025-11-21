import os
import sys
import io
import json
import time
import uuid
import tempfile
import threading
import shutil
import math
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
import cgi

# --- CAD / geometry imports (OCCT python bindings) ---
from OCP.STEPControl import STEPControl_Reader
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_BSplineCurve
from OCP.BRep import BRep_Tool
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopLoc import TopLoc_Location

# numpy + clustering
import numpy as np
from sklearn.cluster import DBSCAN

# ----------------------------
# CONFIG
# ----------------------------
PORT = 8000
WEB_ROOT = os.path.join(os.getcwd(), "output")
os.makedirs(WEB_ROOT, exist_ok=True)
ORIGINAL_DIR = os.getcwd()

# Hole detection parameters
RADIUS_MIN = 1.5
RADIUS_MAX = 20.0
CIRCLE_GROUPING_DISTANCE = 4.0
MIN_VERTICAL_ALIGNMENT = 0.15
MAX_CANDIDATES = 800
MIN_SCORE_THRESHOLD = 20
Z_TOLERANCE = 12.0
ARC_MIN_SPAN_RAD = 1.0

# Global progress/mapping stores
PROGRESS = {}   # uid -> {'percent': int, 'status': str}
MESH_FILES = {} # uid -> path to mesh json file
HOLE_DATA = {}  # uid -> list of detected holes
SELECTED_HOLES = {}  # uid -> set of selected hole IDs

# Utilities
def make_uid():
    return uuid.uuid4().hex

# Geometry functions
def sample_edge_points(edge, n_samples=100):
    pts = []
    try:
        adaptor = BRepAdaptor_Curve(edge)
        f = adaptor.FirstParameter()
        l = adaptor.LastParameter()
        if f is not None and l is not None and abs(l - f) > 1e-7:
            for t in np.linspace(f, l, n_samples):
                try:
                    p = adaptor.Value(t)
                    pts.append([p.X(), p.Y(), p.Z()])
                except:
                    continue
            if len(pts) >= 4:
                return pts
    except:
        pass

    try:
        BRepMesh_IncrementalMesh(edge, 0.08, False, 0.5, True)
        location = BRep_Tool.Location_s(edge)
        poly = BRep_Tool.Polygon3D_s(edge, location)
        if poly:
            transform = location.Transformation()
            for i in range(1, poly.NbNodes() + 1):
                pnt = poly.Nodes().Value(i)
                pnt.Transform(transform)
                pts.append([pnt.X(), pnt.Y(), pnt.Z()])
            if len(pts) >= 4:
                return pts
    except:
        pass

    return pts

def fit_circle_3d(points, min_points=4):
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < min_points:
        return None
    pts = np.unique(pts, axis=0)
    if pts.shape[0] < min_points:
        return None

    centroid = pts.mean(axis=0)
    X = pts - centroid

    try:
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
    except:
        return None

    normal = Vt.T[:, 2]
    x_axis = Vt.T[:, 0]
    y_axis = np.cross(normal, x_axis)
    y_norm = np.linalg.norm(y_axis)
    if y_norm < 1e-10:
        return None
    y_axis = y_axis / y_norm

    pts2 = np.stack([X.dot(x_axis), X.dot(y_axis)], axis=1)
    x = pts2[:,0]
    y = pts2[:,1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x*x + y*y

    try:
        coeffs, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
        if residuals.size > 0 and residuals[0] > 5e4:
            return None
    except:
        return None

    a, b_c, c = coeffs
    cx = 0.5 * a
    cy = 0.5 * b_c
    radius_sq = c + cx*cx + cy*cy
    if radius_sq <= 0:
        return None

    radius = math.sqrt(radius_sq)
    center3d = centroid + cx * x_axis + cy * y_axis

    rel_x = x - cx
    rel_y = y - cy
    angles = np.arctan2(rel_y, rel_x)
    angles_unwrapped = np.unwrap(angles)
    span = angles_unwrapped.max() - angles_unwrapped.min()
    span = min(abs(span), 2*math.pi)

    return np.array(center3d), normal / np.linalg.norm(normal), float(radius), float(span)

def extract_analytic_circular_edges(shape):
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    circles = []

    while exp.More():
        try:
            edge = TopoDS.Edge_s(exp.Current())
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() == GeomAbs_Circle:
                c = curve.Circle()
                center = c.Location()
                axis = c.Axis().Direction()
                radius = c.Radius()

                if RADIUS_MIN <= radius <= RADIUS_MAX:
                    alignment = abs(axis.Z())
                    if alignment >= MIN_VERTICAL_ALIGNMENT:
                        circles.append({
                            'source': 'analytic_edge',
                            'center': np.array([center.X(), center.Y(), center.Z()]),
                            'radius': float(radius),
                            'axis': np.array([axis.X(), axis.Y(), axis.Z()])
                        })
        except:
            pass
        exp.Next()

    return circles

def extract_cylindrical_faces(shape):
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    cylinders = []

    while exp.More():
        try:
            face = TopoDS.Face_s(exp.Current())
            surf = BRepAdaptor_Surface(face, True)
            if surf.GetType() == GeomAbs_Cylinder:
                cyl = surf.Cylinder()
                axis = cyl.Axis().Direction()
                loc = cyl.Axis().Location()
                radius = cyl.Radius()

                if RADIUS_MIN <= radius <= RADIUS_MAX:
                    alignment = abs(axis.Z())
                    if alignment >= MIN_VERTICAL_ALIGNMENT:
                        cylinders.append({
                            'source': 'cylindrical_face',
                            'center': np.array([loc.X(), loc.Y(), loc.Z()]),
                            'radius': float(radius),
                            'axis': np.array([axis.X(), axis.Y(), axis.Z()])
                        })
        except:
            pass
        exp.Next()

    return cylinders

def extract_fitted_circles_from_edges(shape):
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    fitted = []

    while exp.More():
        try:
            edge = TopoDS.Edge_s(exp.Current())
            adaptor = BRepAdaptor_Curve(edge)

            if adaptor.GetType() == GeomAbs_Circle:
                exp.Next()
                continue

            pts = sample_edge_points(edge, n_samples=120)

            if len(pts) < 4:
                exp.Next()
                continue

            fit = fit_circle_3d(pts, min_points=4)
            if fit is None:
                exp.Next()
                continue

            center3d, normal, radius, span = fit

            if not (RADIUS_MIN <= radius <= RADIUS_MAX):
                exp.Next()
                continue

            if span < ARC_MIN_SPAN_RAD:
                exp.Next()
                continue

            fitted.append({
                'source': 'fitted_edge',
                'center': np.array(center3d),
                'radius': float(radius),
                'axis': np.array(normal),
                'arc_span': float(span)
            })
        except:
            pass
        exp.Next()

    return fitted

def calculate_hole_score(group, representative, avg_radius, alignment):
    score = 0.0
    ideal = 5.5
    penalty = 1.5
    score += max(0, 25 - abs(avg_radius - ideal) * penalty)

    if alignment >= MIN_VERTICAL_ALIGNMENT:
        score += (alignment - MIN_VERTICAL_ALIGNMENT) / (1 - MIN_VERTICAL_ALIGNMENT) * 15

    n = len(group)
    if n >= 4:
        score += 40
    elif n == 3:
        score += 32
    elif n == 2:
        score += 24
    else:
        score += 15

    sources = {g['source'] for g in group}
    if 'cylindrical_face' in sources:
        score += 15
    if 'analytic_edge' in sources:
        score += 10
    if 'fitted_edge' in sources:
        score += 5 if n >= 2 else 3

    return max(0, min(100, score))

def combine_and_group(all_circles, shape=None):
    if len(all_circles) == 0:
        return []

    filtered = [c for c in all_circles if RADIUS_MIN <= c['radius'] <= RADIUS_MAX]

    if len(filtered) == 0:
        return []

    centers = np.array([c['center'] for c in filtered])
    z_scale = Z_TOLERANCE / CIRCLE_GROUPING_DISTANCE
    features = np.column_stack([
        centers[:, 0],
        centers[:, 1],
        centers[:, 2] / z_scale
    ])

    clustering = DBSCAN(eps=CIRCLE_GROUPING_DISTANCE, min_samples=1).fit(features)
    labels = clustering.labels_
    unique_labels = sorted(set(labels))

    holes = []
    for lbl in unique_labels:
        group_idx = np.where(labels == lbl)[0]
        group = [filtered[i] for i in group_idx]

        analytic = [g for g in group if g['source'] in ['analytic_edge', 'cylindrical_face']]
        if analytic:
            analytic.sort(key=lambda g: g['center'][2], reverse=True)
            best = analytic[0]
        else:
            group.sort(key=lambda g: g['center'][2], reverse=True)
            best = group[0]

        centers_arr = np.array([g['center'] for g in group])
        hole_center = np.median(centers_arr, axis=0)

        radii = [g['radius'] for g in group]
        avg_radius = float(np.median(radii))

        axes = [g.get('axis', np.array([0,0,1])) for g in group]
        weights = [3.0 if g['source']=='cylindrical_face' else 2.0 if g['source']=='analytic_edge' else 1.0
                   for g in group]
        avg_axis = np.average(axes, axis=0, weights=weights)
        avg_axis = avg_axis / (np.linalg.norm(avg_axis) + 1e-10)

        alignment = abs(avg_axis[2])

        if alignment < MIN_VERTICAL_ALIGNMENT:
            continue

        z_vals = [g['center'][2] for g in group]
        z_depth = float(max(z_vals) - min(z_vals))

        sources = list({g['source'] for g in group})
        num_circles = len(group)

        score = calculate_hole_score(group, best, avg_radius, alignment)

        holes.append({
            'id': len(holes) + 1,
            'center': hole_center.tolist() if isinstance(hole_center, np.ndarray) else list(hole_center),
            'radius': avg_radius,
            'num_circles': num_circles,
            'z_depth': z_depth,
            'vertical_alignment': alignment,
            'score': score,
            'sources': sources
        })

    holes.sort(key=lambda h: h['score'], reverse=True)
    holes = [h for h in holes if h['score'] >= MIN_SCORE_THRESHOLD]
    holes = holes[:MAX_CANDIDATES]

    for i, h in enumerate(holes, 1):
        h['id'] = i

    return holes

# Mesh extraction function
def extract_mesh_from_shape(shape, quality=2.0):
    try:
        BRepMesh_IncrementalMesh(shape, quality)
        raw_vertices = []
        raw_faces = []
        exp = TopExp_Explorer(shape, TopAbs_FACE)

        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)

            if tri:
                trans = loc.Transformation()
                base_idx = len(raw_vertices)
                for i in range(1, tri.NbNodes() + 1):
                    p = tri.Node(i)
                    p.Transform(trans)
                    raw_vertices.append((float(p.X()), float(p.Y()), float(p.Z())))
                for i in range(1, tri.NbTriangles() + 1):
                    t = tri.Triangle(i)
                    a, b, c = t.Get()
                    raw_faces.append((base_idx + (a - 1), base_idx + (b - 1), base_idx + (c - 1)))
            exp.Next()

        if len(raw_vertices) == 0 or len(raw_faces) == 0:
            return [], []

        # Deduplicate vertices and remap faces
        vert_map = {}
        vertices = []
        new_index = 0
        for vi, v in enumerate(raw_vertices):
            if v not in vert_map:
                vert_map[v] = new_index
                vertices.append([v[0], v[1], v[2]])
                new_index += 1

        faces = []
        for f in raw_faces:
            try:
                faces.append([vert_map[raw_vertices[f[0]]],
                              vert_map[raw_vertices[f[1]]],
                              vert_map[raw_vertices[f[2]]]])
            except KeyError:
                continue

        return vertices, faces
    except Exception as e:
        print("Mesh extraction failed:", e)
        return [], []

# Load STEP file helper
def load_step_shape(filepath):
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != 1:
        raise RuntimeError("Failed to read STEP file")
    reader.TransferRoots()
    shape = reader.OneShape()
    return shape

# Background processing function: triangulate, detect holes and save
def process_step_file_async(uid, step_path, quality=1.5):
    try:
        PROGRESS[uid] = {'percent': 5, 'status': 'Loading STEP file'}
        shape = load_step_shape(step_path)

        PROGRESS[uid] = {'percent': 15, 'status': 'Detecting holes'}
        
        # Detect holes
        analytic = extract_analytic_circular_edges(shape)
        cyls = extract_cylindrical_faces(shape)
        fitted = extract_fitted_circles_from_edges(shape)
        
        all_circles = analytic + cyls + fitted
        holes = combine_and_group(all_circles, shape)
        
        PROGRESS[uid] = {'percent': 40, 'status': f'Found {len(holes)} holes, triangulating mesh'}
        time.sleep(0.1)

        vertices, faces = extract_mesh_from_shape(shape, quality=quality)
        if not vertices or not faces:
            PROGRESS[uid] = {'percent': 100, 'status': 'No mesh produced'}
            MESH_FILES[uid] = None
            HOLE_DATA[uid] = holes
            SELECTED_HOLES[uid] = set()
            return

        PROGRESS[uid] = {'percent': 75, 'status': 'Saving data'}

        # Save mesh JSON
        mesh_path = os.path.join(WEB_ROOT, f"mesh_{uid}.json")
        with open(mesh_path, 'w') as f:
            json.dump({'vertices': vertices, 'faces': faces}, f)

        # Save hole data
        hole_path = os.path.join(WEB_ROOT, f"holes_{uid}.json")
        with open(hole_path, 'w') as f:
            json.dump(holes, f)

        MESH_FILES[uid] = mesh_path
        HOLE_DATA[uid] = holes
        SELECTED_HOLES[uid] = set()
        PROGRESS[uid] = {'percent': 100, 'status': f'Done - {len(holes)} holes detected'}

    except Exception as e:
        PROGRESS[uid] = {'percent': 100, 'status': f'Error: {str(e)}'}
        MESH_FILES[uid] = None
        HOLE_DATA[uid] = []
        SELECTED_HOLES[uid] = set()

# Threaded HTTP server
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# Request handler
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

        if parsed.path == "/api/toggle":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]
            hole_id = int(qs.get('id', [0])[0])
            
            if uid not in SELECTED_HOLES:
                SELECTED_HOLES[uid] = set()
                
            if hole_id in SELECTED_HOLES[uid]:
                SELECTED_HOLES[uid].remove(hole_id)
            else:
                SELECTED_HOLES[uid].add(hole_id)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'selected': list(SELECTED_HOLES[uid])}).encode())
            return

        if parsed.path == "/api/export":
            qs = parse_qs(parsed.query)
            uid = qs.get('uid', [None])[0]
            
            if uid not in HOLE_DATA or uid not in SELECTED_HOLES:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"No data for uid"}')
                return
                
            selected_data = [h for h in HOLE_DATA[uid] if h['id'] in SELECTED_HOLES[uid]]
            export_data = {"repositionPointDataArray": []}

            for i, h in enumerate(selected_data, 1):
                cx, cy, cz = h['center']
                r = h['radius']
                off = r * 0.7
                export_data["repositionPointDataArray"].append({
                    "HoleID": f"BS-{i}",
                    "Shape": 2,
                    "group": 0,
                    "radius": round(r, 4),
                    "num_circles": h['num_circles'],
                    "score": round(h['score'], 2),
                    "point1": {"x": round(cx+off,2), "y": round(cy+off,2), "z": round(cz,2)},
                    "point2": {"x": round(cx-off,2), "y": round(cy+off,2), "z": round(cz,2)},
                    "point3": {"x": round(cx-off,2), "y": round(cy-off,2), "z": round(cz,2)},
                    "point4": {"x": round(cx+off,2), "y": round(cy-off,2), "z": round(cz,2)},
                })

            output_file = f"holes_export_{uid}.json"
            with open(os.path.join(WEB_ROOT, output_file), 'w') as f:
                json.dump(export_data, f, indent=2)

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'success', 'count': len(selected_data), 'file': output_file}).encode())
            return

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

        cwd = os.getcwd()
        try:
            os.chdir(WEB_ROOT)
            return SimpleHTTPRequestHandler.do_GET(self)
        finally:
            os.chdir(cwd)

# Write viewer.html into output folder
def write_viewer_html():
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Interactive Hole Detector</title>
  <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
  <style>
    body { font-family: Arial, sans-serif; background:#f7f7fb; margin:10px; }
    #controls { padding:10px; background:white; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.06); width:100%; max-width:1200px; margin-bottom:12px; }
    #progressWrapper { display:flex; align-items:center; gap:12px; }
    #progressBar { width: 400px; height:14px; background:#e9ecef; border-radius:8px; overflow:hidden; }
    #progressFill { height:100%; width:0%; background:#28a745; transition: width 0.2s ease; }
    #statusText { min-width:220px; }
    #plotContainer { width: 100%; max-width:1400px; }
    button { padding:8px 12px; border-radius:6px; border:none; cursor:pointer; }
    button.primary { background:#007bff; color:white; }
    button.green { background:#28a745; color:white; }
    button.red { background:#dc3545; color:white; }
    button:hover { opacity:0.9; transform:translateY(-1px); }
    input[type=file] { padding:6px; }
    #selectionPanel {
      position: fixed;
      top: 20px;
      right: 20px;
      background: white;
      padding: 20px;
      border-radius: 10px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
      width: 320px;
      z-index: 1000;
      display: none;
    }
    #selectionPanel h3 { margin: 0 0 15px 0; }
    #selectionCount { font-weight: bold; color: #0066cc; margin-bottom: 10px; }
    #selectionList {
      padding: 10px;
      background: #f5f5f5;
      border-radius: 5px;
      margin-bottom: 15px;
      min-height: 40px;
      max-height: 150px;
      overflow-y: auto;
      font-size: 13px;
    }
    #exportStatus {
      margin-top: 15px;
      padding: 10px;
      border-radius: 5px;
      display: none;
    }
  </style>
</head>
<body>
  <div id="controls">
    <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
      <input id="stepFile" type="file" accept=".stp,.step"/>
      <button id="btnLoad" class="primary">Load STEP & Detect Holes</button>
      <button id="btnDelete" class="red">Clear All</button>
      <div id="progressWrapper" style="margin-left:20px;">
        <div id="progressBar"><div id="progressFill"></div></div>
        <div id="statusText">Idle</div>
      </div>
      <div style="margin-left:auto; font-size:13px; color:#666;">Status: <span id="globalStatus">Ready</span></div>
    </div>
  </div>

  <div id="selectionPanel">
    <h3>Hole Selection</h3>
    <div id="selectionCount">Selected: 0</div>
    <div id="selectionList">Click on hole markers to select</div>
    <button onclick="exportHoles()" style="width:100%;padding:12px;background:#28a745;color:white;border:none;border-radius:5px;cursor:pointer;font-weight:bold;margin-bottom:8px">Export Selected</button>
    <button onclick="selectAll()" style="width:100%;padding:8px;background:#007bff;color:white;border:none;border-radius:5px;cursor:pointer;margin-bottom:8px">Select All</button>
    <button onclick="clearSelection()" style="width:100%;padding:8px;background:#dc3545;color:white;border:none;border-radius:5px;cursor:pointer">Clear Selection</button>
    <div id="exportStatus"></div>
  </div>

  <div id="plotContainer">
    <div id="plot" style="width:100%;height:760px;"></div>
  </div>

<script>
let currentUID = null;
let meshLoaded = false;
let holesLoaded = false;
let allHoles = [];
let selectedHoles = new Set();

function createInitialPlot() {
  const data = [];
  const layout = {
    title: 'CAD Viewer with Hole Detection',
    scene: {
      xaxis: {title:'X'}, yaxis: {title:'Y'}, zaxis: {title:'Z'}, aspectmode:'data',
    },
    height:760,
    autosize:true
  };
  Plotly.newPlot('plot', data, layout);
}

createInitialPlot();

function setProgress(percent, status) {
  document.getElementById('progressFill').style.width = percent + '%';
  document.getElementById('statusText').textContent = status + ' (' + percent + '%)';
}

function pollProgress(uid) {
  return fetch('/progress?uid=' + uid)
    .then(r => r.json())
    .catch(e => { return {percent:100, status:'Error polling'}; });
}

async function waitForProcessing(uid) {
  currentUID = uid;
  setProgress(1, 'Queued');
  
  while (true) {
    const p = await pollProgress(uid);
    setProgress(p.percent || 0, p.status || 'Working');
    if ((p.percent || 0) >= 100) break;
    await new Promise(r => setTimeout(r, 500));
  }

  // Load mesh and holes
  try {
    await loadMesh(uid);
    await loadHoles(uid);
    document.getElementById('globalStatus').textContent = 'Ready - Click holes to select';
    document.getElementById('selectionPanel').style.display = 'block';
  } catch (e) {
    alert('Failed to load data: ' + e);
  }
}

async function loadMesh(uid) {
  const meshUrl = `/mesh/mesh_${uid}.json`;
  try {
    const r = await fetch(meshUrl);
    if (!r.ok) {
      console.warn('Mesh file not available');
      return;
    }
    const mesh = await r.json();
    
    if (!mesh || !mesh.vertices || mesh.vertices.length === 0) {
      console.warn('Empty mesh data');
      return;
    }

    const xs = mesh.vertices.map(v => v[0]);
    const ys = mesh.vertices.map(v => v[1]);
    const zs = mesh.vertices.map(v => v[2]);
    const i = mesh.faces.map(f => f[0]);
    const j = mesh.faces.map(f => f[1]);
    const k = mesh.faces.map(f => f[2]);

    const trace = {
      type:'mesh3d',
      x: xs, y: ys, z: zs,
      i: i, j: j, k: k,
      opacity: 0.5,
      color: 'lightgray',
      name: 'CAD Model',
      showscale: false,
      flatshading: true,
      lighting: {
        ambient: 0.8,
        diffuse: 0.8,
        specular: 0.2,
        roughness: 0.5,
        fresnel: 0.2
      },
      hoverinfo: 'skip'
    };

    Plotly.addTraces('plot', trace);
    meshLoaded = true;
    autoFitCamera(xs, ys, zs);
  } catch (e) {
    console.error('Failed to load mesh:', e);
  }
}

async function loadHoles(uid) {
  const holesUrl = `/holes/holes_${uid}.json`;
  try {
    const r = await fetch(holesUrl);
    if (!r.ok) {
      throw new Error('Holes file not available');
    }
    allHoles = await r.json();
    
    if (!allHoles || allHoles.length === 0) {
      alert('No holes detected in this model');
      return;
    }

    displayHoles();
    holesLoaded = true;
  } catch (e) {
    console.error('Failed to load holes:', e);
    alert('Failed to load hole data');
  }
}

function displayHoles() {
  const x = allHoles.map(h => h.center[0]);
  const y = allHoles.map(h => h.center[1]);
  const z = allHoles.map(h => h.center[2]);
  const ids = allHoles.map(h => h.id);
  
  const colors = allHoles.map(h => {
    if (selectedHoles.has(h.id)) return 'rgb(0,255,0)';
    return h.num_circles >= 3 ? 'rgb(30,144,255)' : 
           h.num_circles == 2 ? 'rgb(255,69,0)' : 'rgb(255,215,0)';
  });

  const hover = allHoles.map(h => 
    `<b>ID: ${h.id}</b><br>Score: ${h.score.toFixed(0)}<br>Circles: ${h.num_circles}<br>` +
    `X: ${h.center[0].toFixed(0)} Y: ${h.center[1].toFixed(0)} Z: ${h.center[2].toFixed(0)}<br>` +
    `Radius: ${h.radius.toFixed(2)}mm`
  );

  const trace = {
    type: 'scatter3d',
    x: x, y: y, z: z,
    mode: 'markers+text',
    text: ids.map(i => String(i)),
    textposition: 'top center',
    textfont: {size: 10, color: 'white'},
    marker: {
      size: 8,
      color: colors,
      line: {color: 'white', width: 1}
    },
    hovertext: hover,
    hoverinfo: 'text',
    name: 'Holes',
    customdata: ids
  };

  Plotly.addTraces('plot', trace);
  
  // Add click handler
  const plot = document.getElementById('plot');
  plot.on('plotly_click', handleHoleClick);
}

function handleHoleClick(data) {
  try {
    if (!data || !data.points || data.points.length === 0) return;
    const pt = data.points[0];
    if (!pt || !pt.data || pt.data.name !== 'Holes') return;
    
    const holeId = pt.customdata;
    toggleHole(holeId);
  } catch (e) {
    console.error('Click handler error:', e);
  }
}

async function toggleHole(holeId) {
  try {
    const r = await fetch(`/api/toggle?uid=${currentUID}&id=${holeId}`);
    const resp = await r.json();
    selectedHoles = new Set(resp.selected);
    updateHoleColors();
    updateSelectionPanel();
  } catch (e) {
    console.error('Toggle error:', e);
  }
}

function updateHoleColors() {
  const colors = allHoles.map(h => {
    if (selectedHoles.has(h.id)) return 'rgb(0,255,0)';
    return h.num_circles >= 3 ? 'rgb(30,144,255)' : 
           h.num_circles == 2 ? 'rgb(255,69,0)' : 'rgb(255,215,0)';
  });

  const holeTraceIndex = meshLoaded ? 1 : 0;
  Plotly.restyle('plot', {'marker.color': [colors]}, [holeTraceIndex]);
}

function updateSelectionPanel() {
  document.getElementById('selectionCount').textContent = `Selected: ${selectedHoles.size}`;
  const sortedIds = Array.from(selectedHoles).sort((a,b) => a - b);
  document.getElementById('selectionList').textContent = sortedIds.length > 0 ? 
    sortedIds.join(', ') : 'Click on hole markers to select';
}

async function selectAll() {
  const promises = allHoles.map(h => {
    if (!selectedHoles.has(h.id)) {
      return fetch(`/api/toggle?uid=${currentUID}&id=${h.id}`);
    }
  }).filter(Boolean);
  
  await Promise.all(promises);
  
  // Reload selection state
  const r = await fetch(`/api/toggle?uid=${currentUID}&id=0`);
  await fetch(`/api/toggle?uid=${currentUID}&id=0`); // toggle back
  
  // Simpler approach: just select all locally
  selectedHoles = new Set(allHoles.map(h => h.id));
  
  // Update server state for each
  for (const h of allHoles) {
    await fetch(`/api/toggle?uid=${currentUID}&id=${h.id}`);
  }
  
  updateHoleColors();
  updateSelectionPanel();
}

async function clearSelection() {
  const promises = Array.from(selectedHoles).map(id =>
    fetch(`/api/toggle?uid=${currentUID}&id=${id}`)
  );
  await Promise.all(promises);
  selectedHoles.clear();
  updateHoleColors();
  updateSelectionPanel();
}

async function exportHoles() {
  if (selectedHoles.size === 0) {
    showExportStatus('Please select at least one hole', 'error');
    return;
  }

  try {
    const r = await fetch(`/api/export?uid=${currentUID}`);
    const resp = await r.json();
    showExportStatus(`Exported ${resp.count} holes to ${resp.file}`, 'success');
  } catch (e) {
    console.error('Export error:', e);
    showExportStatus('Export failed', 'error');
  }
}

function showExportStatus(msg, type) {
  const status = document.getElementById('exportStatus');
  status.style.display = 'block';
  status.style.background = type === 'success' ? '#d4edda' : '#f8d7da';
  status.style.color = type === 'success' ? '#155724' : '#721c24';
  status.textContent = msg;
  setTimeout(() => status.style.display = 'none', 3000);
}

function autoFitCamera(xs, ys, zs) {
  let minX = Infinity, maxX = -Infinity;
  let minY = Infinity, maxY = -Infinity;
  let minZ = Infinity, maxZ = -Infinity;
  
  for (let i = 0; i < xs.length; i++) {
    if (xs[i] < minX) minX = xs[i];
    if (xs[i] > maxX) maxX = xs[i];
    if (ys[i] < minY) minY = ys[i];
    if (ys[i] > maxY) maxY = ys[i];
    if (zs[i] < minZ) minZ = zs[i];
    if (zs[i] > maxZ) maxZ = zs[i];
  }
  
  const center = [(minX+maxX)/2, (minY+maxY)/2, (minZ+maxZ)/2];
  const rangeX = maxX-minX || 1;
  const rangeY = maxY-minY || 1;
  const rangeZ = maxZ-minZ || 1;
  const maxRange = Math.max(rangeX, rangeY, rangeZ);

  const eye = { x: center[0] + maxRange*1.5, y: center[1] + maxRange*1.5, z: center[2] + maxRange*1.0 };
  Plotly.relayout('plot', {
    'scene.camera.eye': eye,
    'scene.camera.center': {x:center[0], y:center[1], z:center[2]},
  });
}

document.getElementById('btnLoad').addEventListener('click', async () => {
  const fileInput = document.getElementById('stepFile');
  if (!fileInput.files || fileInput.files.length === 0) {
    alert('Please select a STEP file (.stp/.step)');
    return;
  }
  
  // Reset state
  meshLoaded = false;
  holesLoaded = false;
  allHoles = [];
  selectedHoles.clear();
  document.getElementById('selectionPanel').style.display = 'none';
  Plotly.purge('plot');
  createInitialPlot();
  
  const fd = new FormData();
  fd.append('stepfile', fileInput.files[0]);

  setProgress(0, 'Uploading');
  document.getElementById('globalStatus').textContent = 'Uploading...';

  const resp = await fetch('/upload_step', { method: 'POST', body: fd });
  if (!resp.ok) {
    const txt = await resp.text();
    alert('Upload failed: ' + txt);
    return;
  }
  const body = await resp.json();
  document.getElementById('globalStatus').textContent = 'Processing...';
  await waitForProcessing(body.uid);
});

document.getElementById('btnDelete').addEventListener('click', async () => {
  if (!currentUID) {
    alert('No data loaded');
    return;
  }
  
  await fetch('/delete?uid=' + currentUID);
  
  currentUID = null;
  meshLoaded = false;
  holesLoaded = false;
  allHoles = [];
  selectedHoles.clear();
  document.getElementById('selectionPanel').style.display = 'none';
  Plotly.purge('plot');
  createInitialPlot();
  setProgress(0, 'Deleted');
  document.getElementById('globalStatus').textContent = 'Ready';
});

</script>
</body>
</html>
"""
    with open(os.path.join(WEB_ROOT, "viewer.html"), "w", encoding="utf-8") as f:
        f.write(html)

# Start server
def run_server():
    write_viewer_html()
    os.chdir(WEB_ROOT)
    server = ThreadingHTTPServer(('0.0.0.0', PORT), UploadHandler)
    print(f"Server running at http://localhost:{PORT}/viewer.html")
    print("Upload a STEP file to detect holes automatically")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()

if __name__ == "__main__":
    run_server()
