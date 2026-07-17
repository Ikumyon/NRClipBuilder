from typing import Any

from core.geo import EARTH_RADIUS, merc_y_to_lat_rad, inverse_geodesic
from core.nrclip_serde import (
    MODEL_VERSION,
    PayloadWriter,
    encode_nrc1_container,
    make_default_track_kinds,
    serialize_collection,
)
from core.topology import (
    _build_turnout_input,
    _merge_ways_into_routes,
    _simplify_turnout_routes,
    _subdivide_long_segments,
    _build_track_nodes,
    _attach_turnout_branches,
)

def geojson_to_nrclip_bytes(
    features: list[dict[str, Any]],
    name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    spline_tolerance: float = 5.0,
    junction_spacing: float = 30.0,
    max_spacing: float = 200.0,
    tangent_mode: bool = False,
) -> bytes:
    """Convert GeoJSON features to .nrclip using Turnout route topology."""
    osm = _build_turnout_input(features)
    if not osm['ways']:
        raise ValueError("トラックノードが作成されませんでした。")
    route_data = _merge_ways_into_routes(osm)
    simplified = _simplify_turnout_routes(
        route_data,
        osm,
        spline_tolerance,
        junction_spacing,
        max_spacing,
    )
    simplified = _subdivide_long_segments(simplified, route_data['route_coords'], max_spacing)
    track_nodes = _build_track_nodes(simplified, route_data, osm, tangent_mode)
    if not track_nodes:
        raise ValueError("トラックノードが作成されませんでした。")
    _attach_turnout_branches(track_nodes, simplified, route_data)
    if tangent_mode:
        for track in track_nodes:
            if track['attached_to_id'] != 0:
                track['tangential'] = 0

    cx = sum(t['x'] for t in track_nodes) / len(track_nodes)
    cy = sum(t['y'] for t in track_nodes) / len(track_nodes)
    center_lat = merc_y_to_lat_rad(cy)
    center_lon = cx / EARTH_RADIUS

    for t in track_nodes:
        node_lat = merc_y_to_lat_rad(t['y'])
        node_lon = t['x'] / EARTH_RADIUS
        dx, dy = inverse_geodesic(center_lat, center_lon, node_lat, node_lon)
        t['x'] = dx * scale_x
        t['y'] = dy * scale_y

    name_hash = 0x001234567890
    for b in name.encode('utf-8'):
        name_hash = ((name_hash * 31) + b) & 0xFFFFFFFFFFFFFFFF

    file_struct = {
        'collections': [
            {
                'id_a': name_hash,
                'id_b': (name_hash * 7) & 0xFFFFFFFFFFFFFFFF,
                'name': name,
                'clips': [
                    {
                        'guid': name,
                        'clip_id': (name_hash * 13) & 0xFFFFFFFFFFFFFFFF,
                        'center_x': cx,
                        'center_y': cy,
                        'tracks': track_nodes,
                        'track_kinds': make_default_track_kinds(),
                    }
                ]
            }
        ]
    }

    w = PayloadWriter()
    w.write_varint(len(file_struct['collections']))
    for coll in file_struct['collections']:
        serialize_collection(w, coll, MODEL_VERSION)

    payload = w.to_bytes()
    return encode_nrc1_container(payload, MODEL_VERSION)
