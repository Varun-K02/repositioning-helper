import os
import json
import time
import threading

from config import WEB_ROOT
from geometry.extraction import (
    extract_analytic_circular_edges,
    extract_cylindrical_faces,
    extract_fitted_circles_from_edges,
    combine_and_group
)
from geometry.mesh import extract_mesh_from_shape, load_step_shape

# Global progress/mapping stores
PROGRESS = {}   # uid -> {'percent': int, 'status': str}
MESH_FILES = {} # uid -> path to mesh json file
HOLE_DATA = {}  # uid -> list of detected holes
SELECTED_HOLES = {}  # uid -> set of selected hole IDs

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