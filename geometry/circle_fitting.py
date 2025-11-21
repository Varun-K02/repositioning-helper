import numpy as np
import math

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