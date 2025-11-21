import json
import os
from config import WEB_ROOT

def generate_export_json(holes, selected_ids, uid):
    export_data = {"repositionPointDataArray": []}

    selected = [h for h in holes if h["id"] in selected_ids]

    for i, h in enumerate(selected, start=1):
        cx, cy, cz = h["center"]
        r = h["radius"]
        off = r * 0.7

        export_data["repositionPointDataArray"].append({
            "HoleID": f"BS-{i}",
            "Shape": 2,
            "group": 0,
            "radius": round(r, 4),
            "num_circles": h.get("num_circles", 0),
            "score": round(h.get("score", 0), 2),
            "point1": {"x": round(cx+off,2), "y": round(cy+off,2), "z": round(cz,2)},
            "point2": {"x": round(cx-off,2), "y": round(cy+off,2), "z": round(cz,2)},
            "point3": {"x": round(cx-off,2), "y": round(cy-off,2), "z": round(cz,2)},
            "point4": {"x": round(cx+off,2), "y": round(cy-off,2), "z": round(cz,2)},
        })

    filename = f"holes_export_{uid}.json"
    with open(os.path.join(WEB_ROOT, filename), "w") as f:
        json.dump(export_data, f, indent=2)

    return export_data, filename