import struct
import zstandard as zstd
from typing import Any

from core.geo import EARTH_RADIUS, latlon_to_mercator, merc_y_to_lat_rad, inverse_geodesic
from core.wyhash import wyhash_nrc1_checksum
from core.geojson import iter_lines_from_geometry

MODEL_VERSION = 226

def encode_nrc1_container(payload: bytes, version: int = MODEL_VERSION) -> bytes:
    """Compress payload and package with NRC1 header + checksum."""
    cctx = zstd.ZstdCompressor(level=3)
    compressed = cctx.compress(payload)
    checksum = wyhash_nrc1_checksum(payload)
    header = struct.pack(
        "<4sIQQQ",
        b"NRC1",
        version,
        len(payload),
        len(compressed),
        checksum
    )
    return header + compressed

class PayloadWriter:
    """Wire-format primitive writer matching the game's serde vtable."""
    def __init__(self) -> None:
        self.buf = bytearray()

    def write_varint(self, v: int) -> None:
        v &= 0xFFFFFFFFFFFFFFFF
        while True:
            byte = v & 0x7F
            v >>= 7
            if v == 0:
                self.buf.append(byte)
                return
            self.buf.append(byte | 0x80)

    def write_i64z(self, v: int) -> None:
        if v >= 0:
            encoded = v << 1
        else:
            encoded = (-v << 1) - 1
        self.write_varint(encoded)

    def write_i32z(self, v: int) -> None:
        self.write_i64z(v)

    def write_raw_u8(self, v: int) -> None:
        self.buf.append(v & 0xFF)

    def write_f32(self, v: float) -> None:
        self.buf.extend(struct.pack("<f", v))

    def write_f64(self, v: float) -> None:
        self.buf.extend(struct.pack("<d", v))

    def write_string(self, s: str) -> None:
        data = s.encode("utf-8")
        self.write_varint(len(data))
        self.buf.extend(data)

    def write_vec_set_i64(self, v: list[int]) -> None:
        self.write_varint(len(v))
        for val in v:
            self.write_i64z(val)

    def to_bytes(self) -> bytes:
        return bytes(self.buf)

def serialize_track_kind(w: PayloadWriter, tk: dict, ver: int) -> None:
    w.write_string(tk['display_name'])
    w.write_raw_u8(tk['speed_class_flag'])
    w.write_i64z(tk['speed_class'])
    w.write_string(tk['internal_name'])
    w.write_string(tk['secondary_name'])
    w.write_varint(len(tk['horizons']))
    for h in tk['horizons']:
        w.write_i64z(h['speed_class'])
        w.write_f64(h['gauge'])
        w.write_f64(h['height'])
        w.write_f64(h['max_speed'])
        w.write_f64(h['width_a'])
        w.write_f64(h['width_b'])
        w.write_f64(h['spacing'])
        w.write_f64(h['offset_a'])
        w.write_f64(h['offset_b'])
        w.write_i64z(h['visual_distance'])
        for f in h['flags']:
            w.write_raw_u8(f)
        w.write_varint(len(h['textures']))
        for tex in h['textures']:
            w.write_i64z(tex['speed_class'])
            for f in tex['files']:
                w.write_i64z(f['workshop_id'])
                w.write_string(f['path'])
                w.write_string(f['name'])

def make_default_track_kinds() -> list:
    def vanilla_tex():
        return {'workshop_id': 0, 'path': 'tracks', 'name': ''}
    def empty_file():
        return {'workshop_id': 0, 'path': '', 'name': ''}
    def make_textures():
        textures = []
        for sc in range(6):
            files = [vanilla_tex(), vanilla_tex(), vanilla_tex(), vanilla_tex()] if sc <= 3 else [empty_file(), vanilla_tex(), empty_file(), empty_file()]
            textures.append({'speed_class': sc, 'files': files})
        return textures
    def make_kind(key: int, display: str, internal: str, max_speeds: list) -> dict:
        horizons = [
            {
                'speed_class': 0, 'gauge': 97.22222222222221, 'height': 5.21,
                'max_speed': max_speeds[0], 'width_a': 10.0, 'width_b': 25.0,
                'spacing': 15.0, 'offset_a': 2.5, 'offset_b': 2.0,
                'visual_distance': 125000, 'flags': [0, 0, 0, 1, 0],
                'textures': make_textures()
            },
            {
                'speed_class': 0, 'gauge': 97.22222222222221, 'height': 5.21,
                'max_speed': max_speeds[1], 'width_a': 10.0, 'width_b': 25.0,
                'spacing': 25.0, 'offset_a': 2.5, 'offset_b': 2.0,
                'visual_distance': 125000, 'flags': [1, 1, 1, 1, 0],
                'textures': make_textures()
            },
            {
                'speed_class': 0, 'gauge': 97.22222222222221, 'height': 5.21,
                'max_speed': max_speeds[2], 'width_a': 10.0, 'width_b': 25.0,
                'spacing': 15.0, 'offset_a': 2.5, 'offset_b': 2.0,
                'visual_distance': 125000, 'flags': [0, 0, 0, 0, 0],
                'textures': make_textures()
            }
        ]
        return {
            'display_name': display, 'speed_class_flag': 1, 'speed_class': key,
            'internal_name': internal, 'secondary_name': f"{internal}_name", 'horizons': horizons
        }
    return [
        (1, make_kind(1, "waw_track_hs_1", "High speed", [3300.0, 500.0, 4000.0])),
        (2, make_kind(2, "waw_track_tram_1", "Tram", [500.0, 200.0, 700.0])),
        (3, make_kind(3, "waw_track_med_1", "Medium", [1600.0, 500.0, 2200.0])),
    ]

def serialize_track(w: PayloadWriter, t: dict, ver: int) -> None:
    w.write_i64z(t.get('node_id', 0))
    if ver >= 30: w.write_raw_u8(t.get('node_type', 1))
    if ver < 30: w.write_i64z(0)
    if ver >= 30: w.write_i32z(t.get('track_type', 3))
    if ver < 30: w.write_i64z(0)
    if ver >= 45: w.write_i32z(t.get('layer', 0))
    if ver >= 122: w.write_raw_u8(t.get('winding', 1))
    w.write_i64z(t.get('prev_node', 0))
    w.write_i64z(t.get('next_node', 0))
    if ver >= 13: w.write_i64z(t.get('group_id', 0))
    if ver >= 72: w.write_f32(t.get('user_max_speed', 0.0))
    w.write_f64(t.get('x', 0.0))
    w.write_f64(t.get('y', 0.0))
    if 102 <= ver <= 105: w.write_f32(0.0)
    if ver >= 102: w.write_f32(t.get('user_tangent_delta', 0.0))
    if ver >= 141: w.write_f32(t.get('next_spline_t', 0.5))
    w.write_i64z(t.get('station_group_id', 0))
    if ver >= 108: w.write_i32z(t.get('blueprint', 0))
    if ver >= 63:
        w.write_string(t.get('name', ''))
        w.write_raw_u8(t.get('station_platform_auto_name', 0))
    if 170 <= ver <= 181: w.write_f32(0.0)
    if 15 <= ver <= 91: w.write_raw_u8(0)
    if ver >= 62: w.write_raw_u8(t.get('straight', 0))
    if ver >= 143: w.write_raw_u8(t.get('tangential', 0))
    if ver >= 144: w.write_raw_u8(t.get('limited_shapes', 0))
    if ver >= 28:
        for _ in range(4): w.write_varint(0)
    if 32 <= ver <= 197: w.write_varint(0)
    if ver >= 198: w.write_varint(0)
    w.write_i64z(t.get('attached_to_id', 0))
    w.write_f64(t.get('attached_to_t', 0.0))
    if ver >= 30: w.write_i32z(t.get('attached_to_direction', 0))
    w.write_vec_set_i64(t.get('attached_by', []))
    if ver >= 62: w.write_vec_set_i64(t.get('building_attached_by', []))
    if ver >= 33:
        w.write_i64z(t.get('parallel_to_id', 0))
        w.write_i64z(t.get('parallel_kind', 0))
        w.write_f32(t.get('parallel_to_t', 0.0))
        w.write_i32z(t.get('parallel_to_direction', 0))
        w.write_f32(t.get('parallel_to_offset', 0.0))
    if ver >= 60: w.write_f32(t.get('parallel_to_disp', 0.0))
    if ver >= 33: w.write_vec_set_i64(t.get('parallel_by', []))
    if ver >= 192: w.write_f32(t.get('proximity_diamond', 0.0))

def serialize_collection(w: PayloadWriter, coll: dict, ver: int) -> None:
    if ver >= 71:
        w.write_varint(coll['id_a'])
        w.write_varint(coll['id_b'])
        w.write_raw_u8(0)
    if ver >= 66: w.write_string(coll['name'])
    if ver >= 66: w.write_varint(len(coll['clips']))
    for clip in coll['clips']:
        serialize_clip(w, clip, ver)

def serialize_clip(w: PayloadWriter, clip: dict, ver: int) -> None:
    if ver >= 66: w.write_string(clip['guid'])
    if ver >= 66: w.write_varint(clip['clip_id'])
    if ver >= 147:
        w.write_f64(clip['center_x'])
        w.write_f64(clip['center_y'])
    if ver >= 66:
        w.write_varint(len(clip['tracks']))
        for t in clip['tracks']:
            serialize_track(w, t, ver)
    if ver >= 198: w.write_varint(0)
    if ver >= 66: w.write_varint(0)
    if ver >= 66: w.write_varint(0)
    if ver >= 66:
        w.write_varint(len(clip['track_kinds']))
        for key, tk in clip['track_kinds']:
            w.write_i32z(key)
            serialize_track_kind(w, tk, ver)
    if ver >= 66: w.write_varint(0)
    if ver >= 158: w.write_varint(0)
    if ver >= 66: w.write_varint(0)

def get_track_type(props: dict) -> int:
    railway = props.get('railway', '')
    if railway in ('tram', 'light_rail'):
        return 2
    n05_001 = props.get('N05_001', '')
    if n05_001 == '2' or '新幹線' in props.get('N05_002', '') or '新幹線' in props.get('name', ''):
        return 1
    if props.get('highspeed') == 'yes' or props.get('usage') == 'highspeed':
        return 1
    return 3

def get_speed_limit(props: dict) -> float:
    maxspeed = props.get('maxspeed', '')
    if maxspeed:
        try:
            return float(maxspeed) / 3.6
        except ValueError:
            pass
    return 0.0

def geojson_to_nrclip_bytes(features: list[dict[str, Any]], name: str, scale_x: float = 1.0, scale_y: float = 1.0) -> bytes:
    """Convert geojson features to NIMBY Rails .nrclip bytes."""
    track_nodes = []
    next_node_id = 1
    
    for feat in features:
        props = feat.get('properties') or {}
        track_type = get_track_type(props)
        speed = get_speed_limit(props)
        layer = 0
        try:
            layer = int(props.get('layer', 0))
        except (ValueError, TypeError):
            pass
        
        for line in iter_lines_from_geometry(feat.get('geometry') or {}):
            line_nodes = []
            for c in line:
                if not isinstance(c, (list, tuple)) or len(c) < 2:
                    continue
                lon = float(c[0])
                lat = float(c[1])
                x, y = latlon_to_mercator(lat, lon)
                
                node = {
                    'node_id': next_node_id, 'node_type': 1, 'track_type': track_type,
                    'layer': layer, 'winding': 1, 'prev_node': 0, 'next_node': 0, 'group_id': 0,
                    'user_max_speed': speed, 'x': x, 'y': y, 'raw_lon': lon, 'raw_lat': lat,
                    'user_tangent_delta': 0.0, 'next_spline_t': 0.5, 'station_group_id': 0,
                    'blueprint': 0, 'name': '', 'station_platform_auto_name': 0, 'straight': 0,
                    'tangential': 1, 'limited_shapes': 0, 'attached_to_id': 0, 'attached_to_t': 0.0,
                    'attached_to_direction': 0, 'attached_by': [],
                }
                line_nodes.append(node)
                track_nodes.append(node)
                next_node_id += 1
                
            for i in range(len(line_nodes)):
                if i > 0: line_nodes[i]['prev_node'] = line_nodes[i - 1]['node_id']
                if i < len(line_nodes) - 1: line_nodes[i]['next_node'] = line_nodes[i + 1]['node_id']

    if not track_nodes:
        raise ValueError("トラックノードが作成されませんでした。")

    coord_to_gids = {}
    for node in track_nodes:
        key = (round(node['raw_lon'], 7), round(node['raw_lat'], 7))
        if key not in coord_to_gids:
            coord_to_gids[key] = []
        coord_to_gids[key].append(node['node_id'])
        
    node_map = {n['node_id']: n for n in track_nodes}
    for key, gids in coord_to_gids.items():
        if len(gids) > 1:
            parent_id = gids[0]
            parent_node = node_map[parent_id]
            for child_id in gids[1:]:
                child_node = node_map[child_id]
                child_node['attached_to_id'] = parent_id
                child_node['attached_to_t'] = 0.5
                child_node['attached_to_direction'] = 1
                parent_node['attached_by'].append(child_id)

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
