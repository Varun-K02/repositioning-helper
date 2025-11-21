from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.TopLoc import TopLoc_Location
from OCP.BRep import BRep_Tool
from OCP.STEPControl import STEPControl_Reader

import os
import json
from config import WEB_ROOT

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

def load_step_shape(filepath):
    reader = STEPControl_Reader()
    status = reader.ReadFile(filepath)
    if status != 1:
        raise RuntimeError("Failed to read STEP file")
    reader.TransferRoots()
    shape = reader.OneShape()
    return shape