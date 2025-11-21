import os

PORT = 8000
WEB_ROOT = os.path.join(os.getcwd(), "output")
os.makedirs(WEB_ROOT, exist_ok=True)

# Hole detection parameters
RADIUS_MIN = 1.5
RADIUS_MAX = 20.0
CIRCLE_GROUPING_DISTANCE = 4.0
MIN_VERTICAL_ALIGNMENT = 0.15
MAX_CANDIDATES = 800
MIN_SCORE_THRESHOLD = 20
Z_TOLERANCE = 12.0
ARC_MIN_SPAN_RAD = 1.0