import json
import urllib.parse
import urllib.request
from typing import Any

from core.geo_loader import FeatureStore, collect_fields

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

RAILWAY_VALUES = (
    "rail",
    "subway",
    "tram",
    "light_rail",
    "monorail",
    "funicular",
    "abandoned",
    "disused",
    "construction",
    "proposed",
)


def fetch_osm_railways(bbox: tuple[float, float, float, float]) -> FeatureStore:
    west, south, east, north = bbox
    railway_pattern = "|".join(RAILWAY_VALUES)
    query = f"""
[out:json][timeout:180];
(
  way["railway"~"^({railway_pattern})$"]({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        OVERPASS_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": "NRClipBuilder/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)

    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict[str, Any]] = []
    for element in data.get("elements", []):
        if element.get("type") == "node":
            node_id = element.get("id")
            lat = element.get("lat")
            lon = element.get("lon")
            if isinstance(node_id, int) and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                nodes[node_id] = (float(lon), float(lat))
        elif element.get("type") == "way":
            ways.append(element)

    features: list[dict[str, Any]] = []
    for way in ways:
        coords = [nodes[node_id] for node_id in way.get("nodes", []) if node_id in nodes]
        if len(coords) < 2:
            continue
        props = dict(way.get("tags") or {})
        props["osm_id"] = way.get("id")
        props["osm_type"] = "way"
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        })

    return FeatureStore(
        features=features,
        fields=collect_fields(features),
        crs_note=f"OSM Overpass APIから線路データを取得しました: {len(features):,} ways",
    )
