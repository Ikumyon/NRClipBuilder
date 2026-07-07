import re
from typing import Any, Optional
from core.geojson import iter_lines_from_geometry


def split_csv_like(text: str) -> list[str]:
    parts = re.split(r"[,、\n\t]+", text)
    return [p.strip() for p in parts if p.strip()]


def feature_text(props: dict[str, Any], fields: list[str]) -> str:
    from core.geo_loader import safe_str
    if not fields or fields == ["*"]:
        vals = props.values()
    else:
        vals = [props.get(f, "") for f in fields]
    return "\n".join(safe_str(v) for v in vals)


def filter_features(
    features: list[dict[str, Any]],
    keywords_text: str,
    fields_text: str,
    regex: bool,
    match_all: bool,
    exclude_text: str,
) -> list[dict[str, Any]]:
    keywords = split_csv_like(keywords_text)
    excludes = split_csv_like(exclude_text)
    fields = split_csv_like(fields_text)
    if not fields:
        fields = ["*"]

    def matches_one(needle: str, haystack: str) -> bool:
        if regex:
            try:
                return re.search(needle, haystack, flags=re.IGNORECASE) is not None
            except re.error:
                return False
        return needle.lower() in haystack.lower()

    result: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties") or {}
        text = feature_text(props, fields)

        if excludes and any(matches_one(ex, text) for ex in excludes):
            continue

        if not keywords:
            result.append(feat)
            continue

        checks = [matches_one(k, text) for k in keywords]
        if (all(checks) if match_all else any(checks)):
            result.append(feat)

    return result


def bounds_from_features(features: list[dict[str, Any]]) -> Optional[tuple[float, float, float, float]]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, (list, tuple)):
            if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
                xs.append(float(obj[0]))
                ys.append(float(obj[1]))
            else:
                for v in obj:
                    walk(v)

    for feat in features:
        geom = feat.get("geometry") or {}
        if "coordinates" in geom:
            walk(geom["coordinates"])
        elif geom.get("type") == "GeometryCollection":
            for g in geom.get("geometries", []) or []:
                if "coordinates" in g:
                    walk(g["coordinates"])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def geometry_is_line(geom: dict[str, Any]) -> bool:
    if not geom:
        return False
    if geom.get("type") in ("LineString", "MultiLineString"):
        return True
    if geom.get("type") == "GeometryCollection":
        return any(geometry_is_line(g) for g in geom.get("geometries", []) or [])
    return False


def feature_intersects_bbox(feat: dict, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    geom = feat.get("geometry") or {}
    for line in iter_lines_from_geometry(geom):
        for pt in line:
            lon, lat = pt[0], pt[1]
            if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                return True
    return False


def get_intersection(p1: list[float], p2: list[float], bbox: tuple[float, float, float, float]) -> Optional[list[float]]:
    xmin, ymin, xmax, ymax = bbox
    x1, y1 = p1[0], p1[1]
    x2, y2 = p2[0], p2[1]
    t_candidates = []
    if x2 != x1:
        t = (xmin - x1) / (x2 - x1)
        if 0 <= t <= 1:
            y = y1 + t * (y2 - y1)
            if ymin <= y <= ymax:
                t_candidates.append(t)
        t = (xmax - x1) / (x2 - x1)
        if 0 <= t <= 1:
            y = y1 + t * (y2 - y1)
            if ymin <= y <= ymax:
                t_candidates.append(t)
    if y2 != y1:
        t = (ymin - y1) / (y2 - y1)
        if 0 <= t <= 1:
            x = x1 + t * (x2 - x1)
            if xmin <= x <= xmax:
                t_candidates.append(t)
        t = (ymax - y1) / (y2 - y1)
        if 0 <= t <= 1:
            x = x1 + t * (x2 - x1)
            if xmin <= x <= xmax:
                t_candidates.append(t)
    if t_candidates:
        best_t = min(t_candidates)
        return [x1 + best_t * (x2 - x1), y1 + best_t * (y2 - y1)]
    return None


def clip_line_to_bbox(line: list[list[float]], bbox: tuple[float, float, float, float]) -> list[list[list[float]]]:
    xmin, ymin, xmax, ymax = bbox
    clipped_lines = []
    current_sub_line = []
    def is_inside(pt):
        return xmin <= pt[0] <= xmax and ymin <= pt[1] <= ymax
    for i in range(len(line)):
        pt = line[i]
        if is_inside(pt):
            if i > 0 and not is_inside(line[i - 1]):
                inter_pt = get_intersection(line[i - 1], pt, bbox)
                if inter_pt:
                    current_sub_line.append(inter_pt)
            current_sub_line.append(pt)
        else:
            if i > 0 and is_inside(line[i - 1]):
                inter_pt = get_intersection(line[i - 1], pt, bbox)
                if inter_pt:
                    current_sub_line.append(inter_pt)
                if len(current_sub_line) >= 2:
                    clipped_lines.append(current_sub_line)
                current_sub_line = []
            elif i > 0:
                x1, y1 = line[i-1][0], line[i-1][1]
                x2, y2 = line[i][0], line[i][1]
                t_candidates = []
                if x2 != x1:
                    for xm in (xmin, xmax):
                        t = (xm - x1) / (x2 - x1)
                        if 0 <= t <= 1:
                            y = y1 + t * (y2 - y1)
                            if ymin <= y <= ymax:
                                t_candidates.append(t)
                if y2 != y1:
                    for ym in (ymin, ymax):
                        t = (ym - y1) / (y2 - y1)
                        if 0 <= t <= 1:
                            x = x1 + t * (x2 - x1)
                            if xmin <= x <= xmax:
                                t_candidates.append(t)
                t_candidates = sorted(list(set(t_candidates)))
                if len(t_candidates) >= 2:
                    t1, t2 = t_candidates[0], t_candidates[1]
                    pt1 = [x1 + t1 * (x2 - x1), y1 + t1 * (y2 - y1)]
                    pt2 = [x1 + t2 * (x2 - x1), y1 + t2 * (y2 - y1)]
                    clipped_lines.append([pt1, pt2])
    if len(current_sub_line) >= 2:
        clipped_lines.append(current_sub_line)
    return [l for l in clipped_lines if len(l) >= 2]


def clip_geometry_to_bbox(geom: dict[str, Any], bbox: tuple[float, float, float, float]) -> Optional[dict[str, Any]]:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not gtype or coords is None:
        return None
    if gtype == "LineString":
        sub_lines = clip_line_to_bbox(coords, bbox)
        if not sub_lines:
            return None
        if len(sub_lines) == 1:
            return {"type": "LineString", "coordinates": sub_lines[0]}
        else:
            return {"type": "MultiLineString", "coordinates": sub_lines}
    elif gtype == "MultiLineString":
        all_sub_lines = []
        for line in coords:
            all_sub_lines.extend(clip_line_to_bbox(line, bbox))
        if not all_sub_lines:
            return None
        return {"type": "MultiLineString", "coordinates": all_sub_lines}
    elif gtype == "GeometryCollection":
        clipped_geoms = []
        for g in geom.get("geometries", []) or []:
            cg = clip_geometry_to_bbox(g, bbox)
            if cg:
                clipped_geoms.append(cg)
        if not clipped_geoms:
            return None
        return {"type": "GeometryCollection", "geometries": clipped_geoms}
    elif gtype == "Point":
        xmin, ymin, xmax, ymax = bbox
        if xmin <= coords[0] <= xmax and ymin <= coords[1] <= ymax:
            return geom
    elif gtype == "MultiPoint":
        xmin, ymin, xmax, ymax = bbox
        pts = [p for p in coords if xmin <= p[0] <= xmax and ymin <= p[1] <= ymax]
        if pts:
            return {"type": "MultiPoint", "coordinates": pts}
    return None
