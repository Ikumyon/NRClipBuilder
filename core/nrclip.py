import math
import struct
import zstandard as zstd
from typing import Any

from core.geo import EARTH_RADIUS, latlon_to_mercator, merc_y_to_lat_rad, inverse_geodesic
from core.wyhash import wyhash_nrc1_checksum
from core.geojson import coord_key, iter_lines_from_geometry

MODEL_VERSION = 226
ALIGNMENT_THRESHOLD = 2.5
BRANCH_OFFSET = 5.0

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
    route: list[int],
    merc_coords: list[tuple[float, float]],
    junction_nodes: set[int],
    keep: list[bool],
    junction_spacing: float
) -> None:
    if len(merc_coords) <= 2 or junction_spacing <= 0.0:
        return

    start_is_junction = route[0] in junction_nodes
    end_is_junction = route[-1] in junction_nodes

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


def enforce_max_spacing(
    coords: list[tuple[float, float]],
    keep: list[bool],
    max_spacing: float,
) -> None:
    if max_spacing <= 0.0:
        return

    spacing_sq = max_spacing * max_spacing
    last_kept = 0
    for i in range(1, len(coords)):
        if keep[i]:
            last_kept = i
            continue
        dx = coords[i][0] - coords[last_kept][0]
        dy = coords[i][1] - coords[last_kept][1]
        if dx * dx + dy * dy >= spacing_sq:
            keep[i] = True
            last_kept = i

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

def mercator_to_latlon(x: float, y: float) -> tuple[float, float]:
    """Inverse Mercator X/Y to lat/lon degrees."""
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(merc_y_to_lat_rad(y))
    return lon, lat

def is_nearly_straight(coords: list[tuple[float, float]], start_idx: int, end_idx: int, straight_tolerance: float) -> bool:
    """Check if all coordinates between start_idx and end_idx are within straight_tolerance meters from the straight line connecting them."""
    if end_idx - start_idx <= 1:
        return True

    xa, ya = coords[start_idx]
    xb, yb = coords[end_idx]

    dx = xb - xa
    dy = yb - ya
    ab_len = math.hypot(dx, dy)

    if ab_len < 1e-5:
        for i in range(start_idx + 1, end_idx):
            px, py = coords[i]
            if math.hypot(px - xa, py - ya) > straight_tolerance:
                return False
        return True

    for i in range(start_idx + 1, end_idx):
        px, py = coords[i]
        dist = abs(dx * (py - ya) - dy * (px - xa)) / ab_len
        if dist > straight_tolerance:
            return False

    return True

def interpolate_along_polyline(
    coords: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    max_spacing: float
) -> list[tuple[float, float]]:
    """Interpolate coordinates along the polyline connecting start_idx and end_idx with max_spacing interval (in Mercator meters)."""
    cum_len = [0.0]
    for k in range(start_idx, end_idx):
        dx = coords[k + 1][0] - coords[k][0]
        dy = coords[k + 1][1] - coords[k][1]
        cum_len.append(cum_len[-1] + math.hypot(dx, dy))

    total_len = cum_len[-1]
    if total_len < 1.0 or max_spacing <= 0.0:
        return []

    n = math.ceil(total_len / max_spacing)
    interpolated = []
    for j in range(1, n):
        target = total_len * j / n
        seg = 0
        while seg < len(cum_len) - 1 and cum_len[seg + 1] < target:
            seg += 1
        seg_start_len = cum_len[seg]
        seg_end_len = cum_len[seg + 1]
        if seg_end_len > seg_start_len:
            local_t = (target - seg_start_len) / (seg_end_len - seg_start_len)
        else:
            local_t = 0.0
        oi = start_idx + seg
        px = coords[oi][0] + (coords[oi + 1][0] - coords[oi][0]) * local_t
        py = coords[oi][1] + (coords[oi + 1][1] - coords[oi][1]) * local_t
        interpolated.append((px, py))
    return interpolated

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


def _build_turnout_input(features: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[int, tuple[float, float]] = {}
    node_ids: dict[tuple[float, float], int] = {}
    ways: list[list[int]] = []
    node_layer: dict[int, int] = {}
    node_track_type: dict[int, int] = {}
    node_speed: dict[int, float] = {}
    next_node_id = 1

    for feat in features:
        props = feat.get('properties') or {}
        track_type = get_track_type(props)
        speed = get_speed_limit(props)
        try:
            layer = int(props.get('layer', 0))
        except (ValueError, TypeError):
            layer = 0

        for line in iter_lines_from_geometry(feat.get('geometry') or {}):
            way: list[int] = []
            for coord in line or []:
                if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                    continue
                lon = float(coord[0])
                lat = float(coord[1])
                key = coord_key(lon, lat)
                node_id = node_ids.get(key)
                if node_id is None:
                    node_id = next_node_id
                    next_node_id += 1
                    node_ids[key] = node_id
                    nodes[node_id] = (lon, lat)
                if not way or way[-1] != node_id:
                    way.append(node_id)

            if len(way) < 2:
                continue

            ways.append(way)
            for node_id in way:
                existing_layer = node_layer.get(node_id)
                if existing_layer is None or abs(layer) > abs(existing_layer):
                    node_layer[node_id] = layer
                node_track_type.setdefault(node_id, track_type)
                if speed > 0.0:
                    node_speed.setdefault(node_id, speed)

    return {
        'nodes': nodes,
        'ways': ways,
        'node_layer': node_layer,
        'node_track_type': node_track_type,
        'node_speed': node_speed,
    }


def _find_best_continuation(
    node_id: int,
    current_heading: float,
    ways: list[list[int]],
    nodes: dict[int, tuple[float, float]],
    node_ways: dict[int, list[tuple[int, int]]],
    way_used: list[bool],
) -> tuple[int, int, float] | None:
    best: tuple[int, int, float] | None = None
    for way_index, point_index in node_ways.get(node_id, []):
        if way_used[way_index]:
            continue
        way = ways[way_index]
        continuation_id = way[1] if point_index == 0 else way[-2]
        lon, lat = nodes[node_id]
        next_lon, next_lat = nodes[continuation_id]
        heading = math.atan2(next_lat - lat, next_lon - lon)
        difference = abs(heading - current_heading)
        if difference > math.pi:
            difference = math.tau - difference
        if best is None or difference < best[2]:
            best = (way_index, point_index, difference)
    return best


def _extend_route_forward(
    route: list[int],
    ways: list[list[int]],
    nodes: dict[int, tuple[float, float]],
    shared_nodes: set[int],
    node_ways: dict[int, list[tuple[int, int]]],
    way_used: list[bool],
) -> None:
    route_set = set(route)
    while route[-1] in shared_nodes:
        last = route[-1]
        prev_lon, prev_lat = nodes[route[-2]]
        lon, lat = nodes[last]
        heading = math.atan2(lat - prev_lat, lon - prev_lon)
        best = _find_best_continuation(last, heading, ways, nodes, node_ways, way_used)
        if best is None or best[2] > ALIGNMENT_THRESHOLD:
            break
        way_index, point_index, _ = best
        way = ways[way_index]
        new_nodes = way[1:] if point_index == 0 else list(reversed(way[:-1]))
        if any(node_id in route_set for node_id in new_nodes):
            break
        way_used[way_index] = True
        route_set.update(new_nodes)
        route.extend(new_nodes)


def _extend_route_backward(
    route: list[int],
    ways: list[list[int]],
    nodes: dict[int, tuple[float, float]],
    shared_nodes: set[int],
    node_ways: dict[int, list[tuple[int, int]]],
    way_used: list[bool],
) -> None:
    route_set = set(route)
    while route[0] in shared_nodes:
        first = route[0]
        next_lon, next_lat = nodes[route[1]]
        lon, lat = nodes[first]
        heading = math.atan2(lat - next_lat, lon - next_lon)
        best = _find_best_continuation(first, heading, ways, nodes, node_ways, way_used)
        if best is None or best[2] > ALIGNMENT_THRESHOLD:
            break
        way_index, point_index, _ = best
        way = ways[way_index]
        new_nodes = way[:-1] if point_index == len(way) - 1 else list(reversed(way[1:]))
        if any(node_id in route_set for node_id in new_nodes):
            break
        way_used[way_index] = True
        route_set.update(new_nodes)
        route[:] = new_nodes + route


def _merge_ways_into_routes(osm: dict[str, Any]) -> dict[str, Any]:
    ways: list[list[int]] = osm['ways']
    nodes: dict[int, tuple[float, float]] = osm['nodes']
    node_ways: dict[int, list[tuple[int, int]]] = {}
    for way_index, way in enumerate(ways):
        for point_index, node_id in enumerate(way):
            node_ways.setdefault(node_id, []).append((way_index, point_index))

    shared_nodes: set[int] = set()
    junction_nodes: set[int] = set()
    for node_id, refs in node_ways.items():
        way_count = len({way_index for way_index, _ in refs})
        if way_count >= 2:
            shared_nodes.add(node_id)
        if way_count >= 3 or (
            way_count == 2
            and any(0 < point_index < len(ways[way_index]) - 1 for way_index, point_index in refs)
        ):
            junction_nodes.add(node_id)

    way_used = [False] * len(ways)
    routes: list[list[int]] = []
    for start_index in sorted(range(len(ways)), key=lambda index: len(ways[index]), reverse=True):
        if way_used[start_index]:
            continue
        way_used[start_index] = True
        route = list(ways[start_index])
        _extend_route_forward(route, ways, nodes, shared_nodes, node_ways, way_used)
        _extend_route_backward(route, ways, nodes, shared_nodes, node_ways, way_used)
        routes.append(route)

    routes.sort(key=len, reverse=True)
    route_coords = [
        [latlon_to_mercator(nodes[node_id][1], nodes[node_id][0]) for node_id in route]
        for route in routes
    ]
    junction_owner: dict[int, int] = {}
    for route_index, route in enumerate(routes):
        for node_id in route:
            if node_id in junction_nodes:
                junction_owner.setdefault(node_id, route_index)

    return {
        'routes': routes,
        'route_coords': route_coords,
        'junction_nodes': junction_nodes,
        'junction_owner': junction_owner,
    }


def _simplify_turnout_routes(
    route_data: dict[str, Any],
    node_layer: dict[int, int],
    spline_tolerance: float,
    junction_spacing: float,
    max_spacing: float,
    straight_tolerance: float,
) -> list[list[tuple[int | None, float, float]]]:
    simplified_routes: list[list[tuple[int | None, float, float]]] = []
    junction_nodes: set[int] = route_data['junction_nodes']

    for route, coords in zip(route_data['routes'], route_data['route_coords']):
        keep = [False] * len(coords)
        keep[0] = True
        keep[-1] = True

        for i, node_id in enumerate(route):
            if node_id in junction_nodes:
                keep[i] = True
            if i > 0 and node_layer.get(route[i - 1], 0) != node_layer.get(node_id, 0):
                keep[i - 1] = True
                keep[i] = True

        keep_near_junction_endpoints(route, coords, junction_nodes, keep, junction_spacing)
        enforce_max_spacing(coords, keep, max_spacing)
        spline_simplify(coords, keep, spline_tolerance)

        kept_indices = [i for i, should_keep in enumerate(keep) if should_keep]
        result: list[tuple[int | None, float, float]] = []
        for kept_position, start_index in enumerate(kept_indices):
            start_x, start_y = coords[start_index]
            result.append((start_index, start_x, start_y))
            if kept_position + 1 >= len(kept_indices):
                continue
            end_index = kept_indices[kept_position + 1]
            end_x, end_y = coords[end_index]
            segment_distance = math.hypot(end_x - start_x, end_y - start_y)
            if segment_distance <= max_spacing or max_spacing <= 0.0:
                continue
            if is_nearly_straight(coords, start_index, end_index, straight_tolerance):
                continue
            for x, y in interpolate_along_polyline(coords, start_index, end_index, max_spacing):
                result.append((None, x, y))
        simplified_routes.append(result)

    return simplified_routes


def _make_track_node(
    node_id: int,
    x: float,
    y: float,
    layer: int,
    track_type: int,
    speed: float,
) -> dict[str, Any]:
    return {
        'node_id': node_id, 'node_type': 1, 'track_type': track_type,
        'layer': layer, 'winding': 1, 'prev_node': 0, 'next_node': 0, 'group_id': 0,
        'user_max_speed': speed, 'x': x, 'y': y,
        'user_tangent_delta': 0.0, 'next_spline_t': 0.5, 'station_group_id': 0,
        'blueprint': 0, 'name': '', 'station_platform_auto_name': 0, 'straight': 0,
        'tangential': 1, 'limited_shapes': 0, 'attached_to_id': 0, 'attached_to_t': 0.0,
        'attached_to_direction': 0, 'attached_by': [],
    }


def _build_track_nodes(
    simplified: list[list[tuple[int | None, float, float]]],
    route_data: dict[str, Any],
    osm: dict[str, Any],
) -> list[dict[str, Any]]:
    track_nodes: list[dict[str, Any]] = []
    next_node_id = 1
    for route_index, route in enumerate(simplified):
        line_nodes: list[dict[str, Any]] = []
        last_layer = 0
        last_track_type = 3
        last_speed = 0.0
        for original_index, x, y in route:
            if original_index is not None:
                osm_node_id = route_data['routes'][route_index][original_index]
                last_layer = osm['node_layer'].get(osm_node_id, 0)
                last_track_type = osm['node_track_type'].get(osm_node_id, 3)
                last_speed = osm['node_speed'].get(osm_node_id, 0.0)
            node = _make_track_node(
                next_node_id, x, y, last_layer, last_track_type, last_speed,
            )
            next_node_id += 1
            if line_nodes:
                node['prev_node'] = line_nodes[-1]['node_id']
                line_nodes[-1]['next_node'] = node['node_id']
            line_nodes.append(node)
            track_nodes.append(node)
    return track_nodes


def _attach_turnout_branches(
    track_nodes: list[dict[str, Any]],
    simplified: list[list[tuple[int | None, float, float]]],
    route_data: dict[str, Any],
) -> None:
    route_game_nodes: list[list[int]] = []
    junction_game_ids: dict[int, int] = {}
    track_index = 0
    for route_index, route in enumerate(simplified):
        chain: list[int] = []
        for original_index, _, _ in route:
            game_id = track_nodes[track_index]['node_id']
            track_index += 1
            chain.append(game_id)
            if original_index is None:
                continue
            osm_node_id = route_data['routes'][route_index][original_index]
            if (
                osm_node_id in route_data['junction_nodes']
                and route_data['junction_owner'].get(osm_node_id) == route_index
            ):
                junction_game_ids[osm_node_id] = game_id
        route_game_nodes.append(chain)

    node_map = {node['node_id']: node for node in track_nodes}
    for route_index, route in enumerate(simplified):
        if len(route) < 2:
            continue
        for is_start in (True, False):
            endpoint_index = 0 if is_start else -1
            original_index = route[endpoint_index][0]
            if original_index is None:
                continue
            osm_node_id = route_data['routes'][route_index][original_index]
            if osm_node_id not in route_data['junction_nodes']:
                continue
            if route_data['junction_owner'].get(osm_node_id) == route_index:
                continue
            parent_id = junction_game_ids.get(osm_node_id)
            if parent_id is None:
                continue

            chain = route_game_nodes[route_index]
            branch_id = chain[0] if is_start else chain[-1]
            neighbor_id = chain[1] if is_start else chain[-2]
            branch = node_map[branch_id]
            branch_neighbor = node_map[neighbor_id]
            parent = node_map[parent_id]
            parent_neighbor_id = parent['next_node'] or parent['prev_node']
            if not parent_neighbor_id:
                continue
            parent_neighbor = node_map[parent_neighbor_id]

            parent_dx = parent_neighbor['x'] - parent['x']
            parent_dy = parent_neighbor['y'] - parent['y']
            branch_dx = branch_neighbor['x'] - branch['x']
            branch_dy = branch_neighbor['y'] - branch['y']
            direction = 1 if branch_dx * parent_dx + branch_dy * parent_dy >= 0.0 else -1

            branch_length = max(math.hypot(branch_dx, branch_dy), 1e-10)
            mercator_offset = BRANCH_OFFSET / math.cos(merc_y_to_lat_rad(branch['y']))
            branch['x'] += branch_dx / branch_length * mercator_offset
            branch['y'] += branch_dy / branch_length * mercator_offset
            branch['attached_to_id'] = parent_id
            branch['attached_to_t'] = 0.5
            branch['attached_to_direction'] = direction
            branch['tangential'] = 0
            parent['attached_by'].append(branch_id)


def geojson_to_nrclip_bytes(
    features: list[dict[str, Any]],
    name: str,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    spline_tolerance: float = 5.0,
    junction_spacing: float = 30.0,
    max_spacing: float = 200.0,
    straight_tolerance: float = 0.5
) -> bytes:
    """Convert GeoJSON features to .nrclip using Turnout route topology."""
    osm = _build_turnout_input(features)
    if not osm['ways']:
        raise ValueError("トラックノードが作成されませんでした。")
    route_data = _merge_ways_into_routes(osm)
    simplified = _simplify_turnout_routes(
        route_data,
        osm['node_layer'],
        spline_tolerance,
        junction_spacing,
        max_spacing,
        straight_tolerance,
    )
    track_nodes = _build_track_nodes(simplified, route_data, osm)
    if not track_nodes:
        raise ValueError("トラックノードが作成されませんでした。")
    _attach_turnout_branches(track_nodes, simplified, route_data)

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
