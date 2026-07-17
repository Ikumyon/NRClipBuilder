import math
from typing import Any

from core.geo import EARTH_RADIUS, latlon_to_mercator, merc_y_to_lat_rad
from core.geojson import coord_key, iter_lines_from_geometry

ALIGNMENT_THRESHOLD = 2.5
BRANCH_OFFSET = 5.0

# --- Hobby Spline Algorithm & Simplification Implementation ---

class BezierSegment:
    def __init__(self, p0: tuple[float, float], c0: tuple[float, float], c1: tuple[float, float], p1: tuple[float, float]):
        self.p0 = p0
        self.c0 = c0
        self.c1 = c1
        self.p1 = p1

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

def _subdivide_long_segments(
    simplified: list[list[tuple[int | None, float, float]]],
    route_coords: list[list[tuple[float, float]]],
    max_spacing: float,
) -> list[list[tuple[int | None, float, float]]]:
    subdivided_routes: list[list[tuple[int | None, float, float]]] = []

    for route, coords in zip(simplified, route_coords):
        result: list[tuple[int | None, float, float]] = []
        for i, node in enumerate(route):
            result.append(node)
            if i + 1 >= len(route):
                continue

            start_index, start_x, start_y = node
            end_index, end_x, end_y = route[i + 1]
            segment_distance = math.hypot(end_x - start_x, end_y - start_y)
            if segment_distance <= max_spacing or max_spacing <= 0.0:
                continue

            if (
                start_index is not None
                and end_index is not None
                and start_index < end_index
            ):
                for x, y in interpolate_along_polyline(coords, start_index, end_index, max_spacing):
                    result.append((None, x, y))
            else:
                count = math.ceil(segment_distance / max_spacing)
                for j in range(1, count):
                    t = j / count
                    result.append((None, start_x + (end_x - start_x) * t, start_y + (end_y - start_y) * t))
        subdivided_routes.append(result)

    return subdivided_routes

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

def get_node_type(props: dict) -> int:
    if props.get('tunnel') in ('yes', 'true', '1'):
        return 3
    if props.get('bridge') in ('yes', 'true', '1'):
        return 2
    return 1

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
    seen_ways: set[tuple[int, ...]] = set()
    node_layer: dict[int, int] = {}
    node_track_type: dict[int, int] = {}
    node_speed: dict[int, float] = {}
    node_type: dict[int, int] = {}
    
    next_node_id = 1

    for feat in features:
        props = feat.get('properties') or {}
        track_type = get_track_type(props)
        speed = get_speed_limit(props)
        n_type = get_node_type(props)
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
            way_key = tuple(way)
            reverse_key = tuple(reversed(way))
            canonical_key = way_key if way_key <= reverse_key else reverse_key
            if canonical_key in seen_ways:
                continue
            seen_ways.add(canonical_key)

            ways.append(way)
            
            for node_id in way:
                existing_layer = node_layer.get(node_id)
                if existing_layer is None or abs(layer) > abs(existing_layer):
                    node_layer[node_id] = layer
                node_track_type.setdefault(node_id, track_type)
                if speed > 0.0:
                    node_speed.setdefault(node_id, speed)
                
                existing_type = node_type.get(node_id, 1)
                if n_type != 1 and (existing_type == 1 or n_type > existing_type):
                    node_type[node_id] = n_type

    return {
        'nodes': nodes,
        'ways': ways,
        'node_layer': node_layer,
        'node_track_type': node_track_type,
        'node_speed': node_speed,
        'node_type': node_type,
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
        best = _find_best_continuation(
            last, heading, ways, nodes, node_ways, way_used
        )
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
        best = _find_best_continuation(
            first, heading, ways, nodes, node_ways, way_used
        )
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
    osm: dict[str, Any],
    spline_tolerance: float,
    junction_spacing: float,
    max_spacing: float,
) -> list[list[tuple[int | None, float, float]]]:
    simplified_routes: list[list[tuple[int | None, float, float]]] = []
    junction_nodes: set[int] = route_data['junction_nodes']
    
    node_layer = osm.get('node_layer', {})
    node_track_type = osm.get('node_track_type', {})
    node_type = osm.get('node_type', {})

    for route, coords in zip(route_data['routes'], route_data['route_coords']):
        keep = [False] * len(coords)
        keep[0] = True
        keep[-1] = True

        for i, node_id in enumerate(route):
            if node_id in junction_nodes:
                keep[i] = True
            # レイヤー境界を保護
            if i > 0 and node_layer.get(route[i - 1], 0) != node_layer.get(node_id, 0):
                keep[i - 1] = True
                keep[i] = True
            # トラック種別境界を保護
            if i > 0 and node_track_type.get(route[i - 1], 3) != node_track_type.get(node_id, 3):
                keep[i - 1] = True
                keep[i] = True
            # ノード構造境界を保護
            if i > 0 and node_type.get(route[i - 1], 1) != node_type.get(node_id, 1):
                keep[i - 1] = True
                keep[i] = True

        keep_near_junction_endpoints(route, coords, junction_nodes, keep, junction_spacing)
        enforce_max_spacing(coords, keep, max_spacing)
        spline_simplify(coords, keep, spline_tolerance)

        kept_indices = [i for i, should_keep in enumerate(keep) if should_keep]
        simplified_routes.append([(i, coords[i][0], coords[i][1]) for i in kept_indices])

    return simplified_routes

def _make_track_node(
    node_id: int,
    x: float,
    y: float,
    layer: int,
    track_type: int,
    speed: float,
    tangent_mode: bool,
    node_type: int = 1,
) -> dict[str, Any]:
    return {
        'node_id': node_id, 'node_type': node_type, 'track_type': track_type,
        'layer': layer, 'winding': 1, 'prev_node': 0, 'next_node': 0, 'group_id': 0,
        'user_max_speed': speed, 'x': x, 'y': y,
        'user_tangent_delta': 0.0, 'next_spline_t': 0.5, 'station_group_id': 0,
        'blueprint': 0, 'name': '', 'station_platform_auto_name': 0, 'straight': 0,
        'tangential': 1 if tangent_mode else 0, 'limited_shapes': 0, 'attached_to_id': 0, 'attached_to_t': 0.0,
        'attached_to_direction': 0, 'attached_by': [],
    }

def _build_track_nodes(
    simplified: list[list[tuple[int | None, float, float]]],
    route_data: dict[str, Any],
    osm: dict[str, Any],
    tangent_mode: bool,
) -> list[dict[str, Any]]:
    track_nodes: list[dict[str, Any]] = []
    next_node_id = 100
    
    for route_index, route in enumerate(simplified):
        line_nodes: list[dict[str, Any]] = []
        last_layer = 0
        last_track_type = 3
        last_speed = 0.0
        last_node_type = 1
        
        for original_index, x, y in route:
            if original_index is not None:
                osm_node_id = route_data['routes'][route_index][original_index]
                last_layer = osm['node_layer'].get(osm_node_id, 0)
                last_track_type = osm['node_track_type'].get(osm_node_id, 3)
                last_speed = osm['node_speed'].get(osm_node_id, 0.0)
                last_node_type = osm['node_type'].get(osm_node_id, 1)
            node = _make_track_node(
                next_node_id, x, y, last_layer, last_track_type, last_speed, tangent_mode, last_node_type,
            )

            next_node_id += 100
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
            parent['attached_by'].append(branch_id)
