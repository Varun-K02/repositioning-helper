import uuid
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh

def make_uid():
    return uuid.uuid4().hex

def sample_edge_points(edge, n_samples=100):
    pts = []
    # Try analytic sampling from curve adaptor
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

    # Fallback: triangulate / polygon extraction
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