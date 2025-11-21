import math
import numpy as np
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder

from config import (
    RADIUS_MIN, RADIUS_MAX, CIRCLE_GROUPING_DISTANCE,
    MIN_VERTICAL_ALIGNMENT, MAX_CANDIDATES, MIN_SCORE_THRESHOLD,
    Z_TOLERANCE, ARC_MIN_SPAN_RAD
)
from geometry.utils import sample_edge_points
from geometry.circle_fitting import fit_circle_3d
from sklearn.cluster import DBSCAN

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