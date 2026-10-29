import json
import urllib.parse
import urllib.request
from uuid import uuid4

from .. import _native

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def parse_overpass(data, token):
    nodes = {e["id"]: [e["lon"], e["lat"]] for e in data["elements"] if e.get("type") == "node"}
    features = []
    for way in data["elements"]:
        token.check()
        if way.get("type") != "way":
            continue
        # Missing nodes split the geometry rather than inventing connecting segments.
        lines, line = [], []
        for node in way.get("nodes", []):
            if node in nodes:
                line.append(nodes[node])
            else:
                if len(line) >= 2:
                    lines.append(line)
                line = []
        if len(line) >= 2:
            lines.append(line)
        if lines:
            features.append({"type": "Feature", "id": uuid4().hex,
                             "properties": {**way.get("tags", {}), "osm_id": way["id"]},
                             "geometry": {"type": "MultiLineString", "coordinates": lines}})
    return features


def fetch(bbox, token):
    _native.validate_bbox(bbox)
    w, s, e, n = bbox
    query = f'[out:json][timeout:25];way["railway"~"^(rail|subway|tram|light_rail|monorail|funicular|abandoned|disused|construction|proposed)$"]({s},{w},{n},{e});out body;>;out skel qt;'
    request = urllib.request.Request(OVERPASS_URL, data=urllib.parse.urlencode({"data": query}).encode(),
                                     headers={"User-Agent": "NRClipBuilder/0.2", "Content-Type": "application/x-www-form-urlencoded"})
    token.check()
    with urllib.request.urlopen(request, timeout=35) as response:
        chunks = []
        while chunk := response.read(65536):
            token.check()
            chunks.append(chunk)
    token.check()
    return parse_overpass(json.loads(b"".join(chunks)), token)
