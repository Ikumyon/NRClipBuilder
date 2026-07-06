from typing import Any, Iterable

def iter_lines_from_geometry(geom: dict[str, Any]) -> Iterable[list[list[float]]]:
    """Extract LineString coordinates from various GeoJSON geometry types."""
    if not geom:
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype == "LineString":
        yield coords
    elif gtype == "MultiLineString":
        for line in coords or []:
            yield line
    elif gtype == "GeometryCollection":
        for g in geom.get("geometries", []) or []:
            yield from iter_lines_from_geometry(g)

def coord_key(lon: float, lat: float, ndigits: int = 7) -> tuple[float, float]:
    return (round(float(lon), ndigits), round(float(lat), ndigits))

def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-8", "cp932", "shift_jis"):
            try:
                return value.decode(enc)
            except Exception:
                pass
        return value.decode("latin1", errors="replace")
    return str(value)

def geojson_to_overpass(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert filtered GeoJSON lines to Overpass JSON-like data for Turnout import_orm."""
    node_ids: dict[tuple[float, float], int] = {}
    nodes: list[dict[str, Any]] = []
    ways: list[dict[str, Any]] = []
    next_node_id = 1
    next_way_id = 10_000_000

    for feat in features:
        props = feat.get("properties") or {}
        tags: dict[str, str] = {"railway": safe_str(props.get("railway") or "rail") or "rail"}

        # Preserve common railway tags.
        for k in ["name", "usage", "service", "maxspeed", "electrified", "gauge", "layer", "bridge", "tunnel"]:
            if k in props and safe_str(props.get(k)).strip():
                tags[k] = safe_str(props[k]).strip()

        # N05 name mapping.
        if "name" not in tags:
            for n05_name_field in ("N05_002", "N05_011", "路線名", "name"):
                if safe_str(props.get(n05_name_field)).strip():
                    tags["name"] = safe_str(props.get(n05_name_field)).strip()
                    break
        if "operator" not in tags and safe_str(props.get("N05_003")).strip():
            tags["operator"] = safe_str(props.get("N05_003")).strip()

        for line in iter_lines_from_geometry(feat.get("geometry") or {}):
            way_node_ids: list[int] = []
            for c in line or []:
                if not isinstance(c, (list, tuple)) or len(c) < 2:
                    continue
                lon = float(c[0])
                lat = float(c[1])
                key = coord_key(lon, lat)
                if key not in node_ids:
                    node_ids[key] = next_node_id
                    nodes.append({"type": "node", "id": next_node_id, "lat": lat, "lon": lon})
                    next_node_id += 1
                way_node_ids.append(node_ids[key])

            compact: list[int] = []
            for nid in way_node_ids:
                if not compact or compact[-1] != nid:
                    compact.append(nid)
            if len(compact) >= 2:
                ways.append({"type": "way", "id": next_way_id, "nodes": compact, "tags": dict(tags)})
                next_way_id += 1

    return {
        "version": 0.6,
        "generator": "n05_map_filter_exporter",
        "elements": nodes + ways,
    }
