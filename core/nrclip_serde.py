import struct
import zstandard as zstd
from core.wyhash import wyhash_nrc1_checksum

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

def decode_nrc1_container(data: bytes) -> tuple[bytes, int]:
    """Return the decompressed payload and model version from an NRC1 file."""
    magic, version, raw_size, compressed_size, checksum = struct.unpack_from("<4sIQQQ", data, 0)
    if magic != b"NRC1":
        raise ValueError("NRC1ヘッダーではありません。")
    compressed = data[32:32 + compressed_size]
    if len(compressed) != compressed_size:
        raise ValueError("NRC1圧縮データのサイズが不正です。")
    payload = zstd.ZstdDecompressor().decompress(compressed, max_output_size=raw_size)
    if len(payload) != raw_size:
        raise ValueError("NRC1展開後データのサイズが不正です。")
    actual_checksum = wyhash_nrc1_checksum(payload)
    if actual_checksum != checksum:
        raise ValueError("NRC1チェックサムが一致しません。")
    return payload, version

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

class PayloadReader:
    """Wire-format primitive reader matching PayloadWriter."""
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read_varint(self) -> int:
        shift = 0
        result = 0
        while True:
            if self.pos >= len(self.data):
                raise ValueError("varintの途中でデータ末尾に達しました。")
            byte = self.data[self.pos]
            self.pos += 1
            result |= (byte & 0x7F) << shift
            if byte < 0x80:
                return result
            shift += 7
            if shift >= 64:
                raise ValueError("varintが長すぎます。")

    def read_i64z(self) -> int:
        encoded = self.read_varint()
        if encoded & 1:
            return -((encoded >> 1) + 1)
        return encoded >> 1

    def read_i32z(self) -> int:
        return self.read_i64z()

    def read_raw_u8(self) -> int:
        if self.pos >= len(self.data):
            raise ValueError("u8の読み込みでデータ末尾に達しました。")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def read_f32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def read_f64(self) -> float:
        value = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return value

    def read_string(self) -> str:
        size = self.read_varint()
        raw = self.data[self.pos:self.pos + size]
        if len(raw) != size:
            raise ValueError("文字列の途中でデータ末尾に達しました。")
        self.pos += size
        return raw.decode("utf-8")

    def read_vec_set_i64(self) -> list[int]:
        return [self.read_i64z() for _ in range(self.read_varint())]

    def expect_end(self) -> None:
        if self.pos != len(self.data):
            raise ValueError(f"未読データがあります: {len(self.data) - self.pos} bytes")

def serialize_track_kind(w: PayloadWriter, tk: dict, ver: int) -> None:
    w.write_string(tk['display_name'])
    w.write_raw_u8(tk['speed_class_flag'])
    w.write_i64z(tk['speed_class'])
    w.write_string(tk['internal_name'])
    w.write_string(tk['secondary_name'])
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
        for tex in h['textures']:
            w.write_i64z(tex['speed_class'])
            for f in tex['files']:
                w.write_i64z(f['workshop_id'])
                w.write_string(f['path'])
                w.write_string(f['name'])

def deserialize_track_kind(r: PayloadReader, ver: int) -> dict:
    tk = {
        'display_name': r.read_string(),
        'speed_class_flag': r.read_raw_u8(),
        'speed_class': r.read_i64z(),
        'internal_name': r.read_string(),
        'secondary_name': r.read_string(),
        'horizons': [],
    }
    for _ in range(3):
        horizon = {
            'speed_class': r.read_i64z(),
            'gauge': r.read_f64(),
            'height': r.read_f64(),
            'max_speed': r.read_f64(),
            'width_a': r.read_f64(),
            'width_b': r.read_f64(),
            'spacing': r.read_f64(),
            'offset_a': r.read_f64(),
            'offset_b': r.read_f64(),
            'visual_distance': r.read_i64z(),
            'flags': [r.read_raw_u8() for _ in range(5)],
            'textures': [],
        }
        for _ in range(6):
            texture = {'speed_class': r.read_i64z(), 'files': []}
            for _ in range(4):
                texture['files'].append({
                    'workshop_id': r.read_i64z(),
                    'path': r.read_string(),
                    'name': r.read_string(),
                })
            horizon['textures'].append(texture)
        tk['horizons'].append(horizon)
    return tk

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

def deserialize_track(r: PayloadReader, ver: int) -> dict:
    t = {'node_id': r.read_i64z()}
    t['node_type'] = r.read_raw_u8() if ver >= 30 else 1
    if ver < 30: r.read_i64z()
    t['track_type'] = r.read_i32z() if ver >= 30 else 3
    if ver < 30: r.read_i64z()
    t['layer'] = r.read_i32z() if ver >= 45 else 0
    t['winding'] = r.read_raw_u8() if ver >= 122 else 1
    t['prev_node'] = r.read_i64z()
    t['next_node'] = r.read_i64z()
    t['group_id'] = r.read_i64z() if ver >= 13 else 0
    t['user_max_speed'] = r.read_f32() if ver >= 72 else 0.0
    t['x'] = r.read_f64()
    t['y'] = r.read_f64()
    if 102 <= ver <= 105: r.read_f32()
    t['user_tangent_delta'] = r.read_f32() if ver >= 102 else 0.0
    t['next_spline_t'] = r.read_f32() if ver >= 141 else 0.5
    t['station_group_id'] = r.read_i64z()
    t['blueprint'] = r.read_i32z() if ver >= 108 else 0
    if ver >= 63:
        t['name'] = r.read_string()
        t['station_platform_auto_name'] = r.read_raw_u8()
    else:
        t['name'] = ''
        t['station_platform_auto_name'] = 0
    if 170 <= ver <= 181: r.read_f32()
    if 15 <= ver <= 91: r.read_raw_u8()
    t['straight'] = r.read_raw_u8() if ver >= 62 else 0
    t['tangential'] = r.read_raw_u8() if ver >= 143 else 0
    t['limited_shapes'] = r.read_raw_u8() if ver >= 144 else 0
    if ver >= 28:
        for _ in range(4): r.read_varint()
    if 32 <= ver <= 197: r.read_varint()
    if ver >= 198: r.read_varint()
    t['attached_to_id'] = r.read_i64z()
    t['attached_to_t'] = r.read_f64()
    t['attached_to_direction'] = r.read_i32z() if ver >= 30 else 0
    t['attached_by'] = r.read_vec_set_i64()
    t['building_attached_by'] = r.read_vec_set_i64() if ver >= 62 else []
    if ver >= 33:
        t['parallel_to_id'] = r.read_i64z()
        t['parallel_kind'] = r.read_i64z()
        t['parallel_to_t'] = r.read_f32()
        t['parallel_to_direction'] = r.read_i32z()
        t['parallel_to_offset'] = r.read_f32()
    else:
        t['parallel_to_id'] = 0
        t['parallel_kind'] = 0
        t['parallel_to_t'] = 0.0
        t['parallel_to_direction'] = 0
        t['parallel_to_offset'] = 0.0
    t['parallel_to_disp'] = r.read_f32() if ver >= 60 else 0.0
    t['parallel_by'] = r.read_vec_set_i64() if ver >= 33 else []
    t['proximity_diamond'] = r.read_f32() if ver >= 192 else 0.0
    return t

def serialize_station_group(w: PayloadWriter, sg: dict, ver: int) -> None:
    w.write_i64z(sg['id'])
    if ver >= 11: w.write_i32z(sg.get('created_on', 0))
    if ver >= 182: w.write_raw_u8(sg.get('use_automatic_point', 0))
    if ver >= 182:
        px, py = sg.get('position', (0.0, 0.0))
        w.write_f64(px)
        w.write_f64(py)
    w.write_string(sg['name'])
    w.write_raw_u8(sg.get('use_automatic_name', 0))
    if ver >= 57: w.write_i32z(sg.get('geo_name_pick', 0))
    if ver >= 182: w.write_vec_set_i64(sg.get('tags', []))
    w.write_vec_set_i64(sg.get('track_ids', []))
    if ver >= 167: w.write_vec_set_i64(sg.get('building_ids', []))
    if ver >= 195: w.write_vec_set_i64(sg.get('extra_ids', []))
    if ver >= 4: w.write_f32(sg.get('size_factor', 1.0))
    if ver >= 163: w.write_f32(sg.get('walk_factor', 1.0))
    if ver >= 165:
        w.write_varint(sg.get('max_platform_pax', 0))
        w.write_varint(sg.get('transfer_overflow_into_hall', 0))
    if ver >= 94: w.write_i32z(sg.get('label_mode', 0))
    if ver >= 208: w.write_varint(sg.get('scripts', 0))

def deserialize_station_group(r: PayloadReader, ver: int) -> dict:
    sg = {'id': r.read_i64z()}
    sg['created_on'] = r.read_i32z() if ver >= 11 else 0
    sg['use_automatic_point'] = r.read_raw_u8() if ver >= 182 else 0
    sg['position'] = (r.read_f64(), r.read_f64()) if ver >= 182 else (0.0, 0.0)
    sg['name'] = r.read_string()
    sg['use_automatic_name'] = r.read_raw_u8()
    sg['geo_name_pick'] = r.read_i32z() if ver >= 57 else 0
    sg['tags'] = r.read_vec_set_i64() if ver >= 182 else []
    sg['track_ids'] = r.read_vec_set_i64()
    sg['building_ids'] = r.read_vec_set_i64() if ver >= 167 else []
    sg['extra_ids'] = r.read_vec_set_i64() if ver >= 195 else []
    sg['size_factor'] = r.read_f32() if ver >= 4 else 1.0
    sg['walk_factor'] = r.read_f32() if ver >= 163 else 1.0
    if ver >= 165:
        sg['max_platform_pax'] = r.read_varint()
        sg['transfer_overflow_into_hall'] = r.read_varint()
    else:
        sg['max_platform_pax'] = 0
        sg['transfer_overflow_into_hall'] = 0
    sg['label_mode'] = r.read_i32z() if ver >= 94 else 0
    sg['scripts'] = r.read_varint() if ver >= 208 else 0
    return sg

def serialize_collection(w: PayloadWriter, coll: dict, ver: int) -> None:
    if ver >= 71:
        w.write_varint(coll['id_a'])
        w.write_varint(coll['id_b'])
        w.write_raw_u8(0)
    if ver >= 66: w.write_string(coll['name'])
    if ver >= 66: w.write_varint(len(coll['clips']))
    for clip in coll['clips']:
        serialize_clip(w, clip, ver)

def deserialize_collection(r: PayloadReader, ver: int) -> dict:
    coll = {}
    if ver >= 71:
        coll['id_a'] = r.read_varint()
        coll['id_b'] = r.read_varint()
        r.read_raw_u8()
    else:
        coll['id_a'] = 0
        coll['id_b'] = 0
    coll['name'] = r.read_string() if ver >= 66 else ''
    clip_count = r.read_varint() if ver >= 66 else 0
    coll['clips'] = [deserialize_clip(r, ver) for _ in range(clip_count)]
    return coll

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
    if ver >= 198: w.write_varint(0) # signals
    if ver >= 66:
        station_groups = clip.get('station_groups', [])
        w.write_varint(len(station_groups))
        for sg in station_groups:
            serialize_station_group(w, sg, ver)
    if ver >= 66: w.write_varint(0) # buildings
    if ver >= 66:
        w.write_varint(len(clip['track_kinds']))
        for key, tk in clip['track_kinds']:
            w.write_i32z(key)
            serialize_track_kind(w, tk, ver)
    if ver >= 66: w.write_varint(0) # building_kinds
    if ver >= 158: w.write_varint(0) # demands
    if ver >= 66: w.write_varint(0) # mod_metas

def deserialize_clip(r: PayloadReader, ver: int) -> dict:
    clip = {}
    clip['guid'] = r.read_string() if ver >= 66 else ''
    clip['clip_id'] = r.read_varint() if ver >= 66 else 0
    if ver >= 147:
        clip['center_x'] = r.read_f64()
        clip['center_y'] = r.read_f64()
    else:
        clip['center_x'] = 0.0
        clip['center_y'] = 0.0
    if ver >= 66:
        clip['tracks'] = [deserialize_track(r, ver) for _ in range(r.read_varint())]
    else:
        clip['tracks'] = []
    if ver >= 198: r.read_varint()
    if ver >= 66:
        clip['station_groups'] = [deserialize_station_group(r, ver) for _ in range(r.read_varint())]
    else:
        clip['station_groups'] = []
    if ver >= 66:
        building_count = r.read_varint()
        if building_count:
            raise ValueError("建物データは現在のnrclip方式では扱いません。")
        clip['buildings'] = []
    else:
        clip['buildings'] = []
    if ver >= 66:
        clip['track_kinds'] = [(r.read_i32z(), deserialize_track_kind(r, ver)) for _ in range(r.read_varint())]
    else:
        clip['track_kinds'] = []
    if ver >= 66:
        building_kind_count = r.read_varint()
        if building_kind_count:
            raise ValueError("建物種類データは現在のnrclip方式では扱いません。")
        clip['building_kinds'] = []
    else:
        clip['building_kinds'] = []
    if ver >= 158: r.read_varint()
    if ver >= 66: r.read_varint()
    return clip

def deserialize_clip_tracks_and_stations(r: PayloadReader, ver: int) -> dict:
    clip = {}
    clip['guid'] = r.read_string() if ver >= 66 else ''
    clip['clip_id'] = r.read_varint() if ver >= 66 else 0
    if ver >= 147:
        clip['center_x'] = r.read_f64()
        clip['center_y'] = r.read_f64()
    else:
        clip['center_x'] = 0.0
        clip['center_y'] = 0.0
    if ver >= 66:
        clip['tracks'] = [deserialize_track(r, ver) for _ in range(r.read_varint())]
    else:
        clip['tracks'] = []
    if ver >= 198: r.read_varint()
    if ver >= 66:
        clip['station_groups'] = [deserialize_station_group(r, ver) for _ in range(r.read_varint())]
    else:
        clip['station_groups'] = []
    return clip

def decode_first_clip_tracks_and_stations(data: bytes) -> dict:
    payload, version = decode_nrc1_container(data)
    r = PayloadReader(payload)
    collection_count = r.read_varint()
    if collection_count < 1:
        raise ValueError("collectionがありません。")
    coll = {}
    if version >= 71:
        coll['id_a'] = r.read_varint()
        coll['id_b'] = r.read_varint()
        r.read_raw_u8()
    else:
        coll['id_a'] = 0
        coll['id_b'] = 0
    coll['name'] = r.read_string() if version >= 66 else ''
    clip_count = r.read_varint() if version >= 66 else 0
    if clip_count < 1:
        raise ValueError("clipがありません。")
    clip = deserialize_clip_tracks_and_stations(r, version)
    return {'version': version, 'collection': coll, 'clip': clip}

def deserialize_collections_payload(payload: bytes, ver: int) -> dict:
    r = PayloadReader(payload)
    file_struct = {
        'collections': [deserialize_collection(r, ver) for _ in range(r.read_varint())],
    }
    r.expect_end()
    return file_struct

def decode_collections_nrclip(data: bytes) -> dict:
    payload, version = decode_nrc1_container(data)
    file_struct = deserialize_collections_payload(payload, version)
    file_struct['version'] = version
    return file_struct
