import math
import struct
import zstandard as zstd
from typing import Any

from core.geo import EARTH_RADIUS, latlon_to_mercator, merc_y_to_lat_rad, inverse_geodesic
from core.wyhash import wyhash_nrc1_checksum
from core.geojson import iter_lines_from_geometry

MODEL_VERSION = 226

# --- Hobby Spline Algorithm & Simplification Implementation ---

class BezierSegment:
    def __init__(self, p0: tuple[float, float], c0: tuple[float, float], c1: tuple[float, float], p1: tuple[float, float]):
        self.p0 = p0
        self.c0 = c0
        self.c1 = c1
        self.p1 = p1

# Hobby's rho velocity parameter: `(3 - √5) / 2` ≈ 0.38196601125
DELTA = (3.0 - math.sqrt(5.0)) / 2.0
EPSILON = 1e-10

def normalize_angle(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a

def rotate_unit(angle: float) -> tuple[float, float]:
    return math.cos(angle), math.sin(angle)

def hobby_rho(alpha: float, beta: float) -> tuple[float, float]:
    sa, sb = math.sin(alpha), math.sin(beta)
    ca, cb = math.cos(alpha), math.cos(beta)

    a = sa - sb / 16.0
    b = sb - sa / 16.0
    c = ca - cb
    f = math.sqrt(2.0) * a * b * c

    denom_a = 3.0 * (1.0 + (1.0 - DELTA) * ca + DELTA * cb)
    denom_b = 3.0 * (1.0 + (1.0 - DELTA) * cb + DELTA * ca)

    rho_a = (2.0 + f) / denom_a if abs(denom_a) > EPSILON else 1.0 / 3.0
    rho_b = (2.0 - f) / denom_b if abs(denom_b) > EPSILON else 1.0 / 3.0

    return max(rho_a, 0.0), max(rho_b, 0.0)

def bezier_point(seg: BezierSegment, t: float) -> tuple[float, float]:
    t2 = t * t
    t3 = t2 * t
    mt = 1.0 - t
    mt2 = mt * mt
    mt3 = mt2 * mt
    x = mt3 * seg.p0[0] + 3.0 * mt2 * t * seg.c0[0] + 3.0 * mt * t2 * seg.c1[0] + t3 * seg.p1[0]
    y = mt3 * seg.p0[1] + 3.0 * mt2 * t * seg.c0[1] + 3.0 * mt * t2 * seg.c1[1] + t3 * seg.p1[1]
    return x, y

def hobby_spline(points: list[tuple[float, float]]) -> list[BezierSegment]:
    if len(points) < 2:
        return []
    if len(points) == 2:
        x0, y0 = points[0]
        x1, y1 = points[1]
        angle = math.atan2(y1 - y0, x1 - x0)
        d = math.hypot(x1 - x0, y1 - y0) / 3.0
        ca, sa = math.cos(angle), math.sin(angle)
        return [BezierSegment(
            p0=(x0, y0),
            c0=(x0 + ca * d, y0 + sa * d),
            c1=(x1 - ca * d, y1 - sa * d),
            p1=(x1, y1)
        )]

    n = len(points)
    chords = []
    chord_lens = []
    for i in range(n - 1):
        dx = points[i + 1][0] - points[i][0]
        dy = points[i + 1][1] - points[i][1]
        length = max(math.hypot(dx, dy), EPSILON)
        chords.append((dx, dy))
        chord_lens.append(length)

    angles = []
    for i in range(n):
        if i == 0:
            angle = math.atan2(chords[0][1], chords[0][0])
        elif i == n - 1:
            angle = math.atan2(chords[n - 2][1], chords[n - 2][0])
        else:
            in_angle = math.atan2(chords[i - 1][1], chords[i - 1][0])
            out_angle = math.atan2(chords[i][1], chords[i][0])
            turn = normalize_angle(out_angle - in_angle)
            angle = in_angle + turn / 2.0
        angles.append(angle)

    segments = []
    for i in range(n - 1):
        chord_angle = math.atan2(chords[i][1], chords[i][0])
        d = chord_lens[i]
        alpha = normalize_angle(angles[i] - chord_angle)
        beta = normalize_angle(chord_angle - angles[i + 1])
        rho_a, rho_b = hobby_rho(alpha, beta)
        c0_dx, c0_dy = rotate_unit(chord_angle + alpha)
        c1_dx, c1_dy = rotate_unit(chord_angle - beta)
        segments.append(BezierSegment(
            p0=points[i],
            c0=(points[i][0] + rho_a * d * c0_dx, points[i][1] + rho_a * d * c0_dy),
            c1=(points[i + 1][0] - rho_b * d * c1_dx, points[i + 1][1] - rho_b * d * c1_dy),
            p1=points[i + 1]
        ))
    return segments

def keep_near_junction_endpoints(
    merc_coords: list[tuple[float, float]],
    keep: list[bool],
    orig_coords: list[tuple[float, float]],
    junction_coords: set[tuple[float, float]],
    junction_spacing: float
) -> None:
    if len(merc_coords) <= 2 or junction_spacing <= 0.0:
        return

    start_key = (round(orig_coords[0][0], 7), round(orig_coords[0][1], 7))
    start_is_junction = start_key in junction_coords

    end_key = (round(orig_coords[-1][0], 7), round(orig_coords[-1][1], 7))
    end_is_junction = end_key in junction_coords

    spacing_sq = junction_spacing ** 2

    if start_is_junction:
        for i in range(1, len(merc_coords) - 1):
            dx = merc_coords[i][0] - merc_coords[0][0]
            dy = merc_coords[i][1] - merc_coords[0][1]
            if dx * dx + dy * dy >= spacing_sq:
                keep[i] = True
                break

    if end_is_junction:
        last = len(merc_coords) - 1
        for i in range(last - 1, 0, -1):
            dx = merc_coords[i][0] - merc_coords[last][0]
            dy = merc_coords[i][1] - merc_coords[last][1]
            if dx * dx + dy * dy >= spacing_sq:
                keep[i] = True
                break

def spline_simplify(coords: list[tuple[float, float]], keep: list[bool], spline_tolerance: float) -> None:
    for _ in range(20):
        kept_pts = [coords[i] for i in range(len(coords)) if keep[i]]
        kept_idx = [i for i in range(len(coords)) if keep[i]]

        if len(kept_pts) < 2:
            break

        segs = hobby_spline(kept_pts)
        added = False

        for si, seg in enumerate(segs):
            orig_start = kept_idx[si]
            orig_end = kept_idx[si + 1]
            if orig_end - orig_start <= 1:
                continue

            worst_dev = 0.0
            worst_orig = orig_start
            for oi in range(orig_start + 1, orig_end):
                ox, oy = coords[oi]
                best_d = float("inf")
                for s in range(33):
                    t = s / 32.0
                    pt = bezier_point(seg, t)
                    d = math.hypot(ox - pt[0], oy - pt[1])
                    if d < best_d:
                        best_d = d
                if best_d > worst_dev:
                    worst_dev = best_d
                    worst_orig = oi

            if worst_dev > spline_tolerance:
                keep[worst_orig] = True
                added = True

        if not added:
            break

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

def geojson_to_nrclip_bytes(
    features: list[dict[str, Any]],
    name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    spline_tolerance: float = 5.0,
    junction_spacing: float = 30.0
) -> bytes:
    """Convert geojson features to NIMBY Rails .nrclip bytes with Hobby Spline simplification."""
    # 1. 路線データを routes リストとして集約
    routes = []
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
            valid_line = []
            for c in line:
                if not isinstance(c, (list, tuple)) or len(c) < 2:
                    continue
                valid_line.append((float(c[0]), float(c[1])))  # (lon, lat)
            if len(valid_line) >= 2:
                routes.append({
                    'coords': valid_line,
                    'track_type': track_type,
                    'speed': speed,
                    'layer': layer
                })

    if not routes:
        raise ValueError("トラックノードが作成されませんでした。")

    # 2. ジャンクション（共有ノード）の検出
    coord_counts = {}
    for ri, r in enumerate(routes):
        for pi, pt in enumerate(r['coords']):
            key = (round(pt[0], 7), round(pt[1], 7))
            if key not in coord_counts:
                coord_counts[key] = []
            coord_counts[key].append((ri, pi))
            
    junction_coords = set()
    for key, refs in coord_counts.items():
        unique_routes = set(ri for ri, pi in refs)
        if len(unique_routes) >= 2 or len(refs) >= 2:
            junction_coords.add(key)

    # 3. 各 route の簡素化 (spline_simplify)
    for r in routes:
        coords = r['coords']
        # メルカトル座標の計算
        merc_coords = []
        for lon, lat in coords:
            mx, my = latlon_to_mercator(lat, lon)
            merc_coords.append((mx, my))
            
        keep = [False] * len(coords)
        keep[0] = True
        keep[-1] = True
        
        for i, pt in enumerate(coords):
            key = (round(pt[0], 7), round(pt[1], 7))
            if key in junction_coords:
                keep[i] = True
                
        keep_near_junction_endpoints(merc_coords, keep, coords, junction_coords, junction_spacing)
        spline_simplify(merc_coords, keep, spline_tolerance)
        
        # 簡素化された座標リスト
        r['simplified_coords'] = [coords[i] for i in range(len(keep)) if keep[i]]

    # 4. トラックノードの構築
    track_nodes = []
    next_node_id = 1
    
    for r in routes:
        line_nodes = []
        for lon, lat in r['simplified_coords']:
            x, y = latlon_to_mercator(lat, lon)
            node = {
                'node_id': next_node_id, 'node_type': 1, 'track_type': r['track_type'],
                'layer': r['layer'], 'winding': 1, 'prev_node': 0, 'next_node': 0, 'group_id': 0,
                'user_max_speed': r['speed'], 'x': x, 'y': y, 'raw_lon': lon, 'raw_lat': lat,
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

    # 5. 合流・接続処理
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

    # 6. 中心点を基準としたメートル平面座標系 (x, y) への変換
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
