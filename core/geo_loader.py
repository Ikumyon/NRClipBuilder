import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import shapefile  # pyshp
except Exception:  # pragma: no cover
    shapefile = None

try:
    from pyproj import CRS, Transformer
except Exception:  # pragma: no cover
    CRS = None
    Transformer = None


@dataclass
class FeatureStore:
    features: list[dict[str, Any]] = field(default_factory=list)
    source_path: Optional[Path] = None
    temp_dir: Optional[Path] = None
    fields: list[str] = field(default_factory=list)
    crs_note: str = ""

    def cleanup(self) -> None:
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None

    def feature_collection(self, features: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": features if features is not None else self.features,
        }


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


def normalize_feature(feature: dict[str, Any]) -> Optional[dict[str, Any]]:
    geom = feature.get("geometry")
    if not geom:
        return None
    props = feature.get("properties") or {}
    return {"type": "Feature", "properties": props, "geometry": geom}


def collect_fields(features: list[dict[str, Any]]) -> list[str]:
    keys: set[str] = set()
    for feat in features:
        props = feat.get("properties") or {}
        keys.update(str(k) for k in props.keys())
    def sort_key(k: str) -> tuple[int, str]:
        return (0 if k.startswith("N05_") else 1, k)
    return sorted(keys, key=sort_key)


def load_geojson(path: Path) -> FeatureStore:
    data = json.loads(path.read_text(encoding="utf-8"))
    features: list[dict[str, Any]] = []

    if data.get("type") == "FeatureCollection":
        raw = data.get("features") or []
        for feat in raw:
            norm = normalize_feature(feat)
            if norm:
                features.append(norm)
    elif data.get("type") == "Feature":
        norm = normalize_feature(data)
        if norm:
            features.append(norm)
    elif "type" in data and "coordinates" in data:
        features.append({"type": "Feature", "properties": {}, "geometry": data})
    else:
        raise ValueError("GeoJSONとして読めませんでした。FeatureCollection / Feature / Geometry が必要です。")

    return FeatureStore(features=features, source_path=path, fields=collect_fields(features), crs_note="GeoJSONとして読み込みました。座標はEPSG:4326想定です。")


def find_shapefile_in_zip(zip_path: Path) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="n05_exporter_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(temp_dir)

    shp_files = list(temp_dir.rglob("*.shp"))
    if not shp_files:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise ValueError("ZIP内に .shp が見つかりませんでした。")

    # Prefer N05 line layer.
    preferred = [p for p in shp_files if "RailroadSection" in p.name]
    if preferred:
        return preferred[0], temp_dir

    # Then any line-like layer.
    preferred = [p for p in shp_files if any(s in p.name.lower() for s in ("rail", "line", "section"))]
    if preferred:
        return preferred[0], temp_dir

    return shp_files[0], temp_dir


def read_prj_for(path: Path) -> Optional[str]:
    prj = path.with_suffix(".prj")
    if prj.exists():
        try:
            return prj.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return prj.read_text(encoding="cp932", errors="ignore")
    return None


def make_transformer_to_wgs84(path: Path) -> tuple[Optional[Any], str]:
    if CRS is None or Transformer is None:
        return None, "pyprojが無いため、SHPの座標変換は行っていません。N05の経緯度データなら通常そのまま使えます。"

    prj_text = read_prj_for(path)
    if not prj_text:
        return None, "PRJが無いため座標変換なしで読み込みました。N05は通常このままで問題ありません。"

    try:
        source_crs = CRS.from_wkt(prj_text)
        target_crs = CRS.from_epsg(4326)
        if source_crs == target_crs or source_crs.to_epsg() in (4326, 4612, 6668):
            return None, f"CRS: {source_crs.to_string()}。経緯度として読み込みました。"
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        return transformer, f"CRS: {source_crs.to_string()} → EPSG:4326 に変換しました。"
    except Exception as exc:
        return None, f"PRJ解析に失敗したため座標変換なしで読み込みました: {exc}"


def transform_coords(obj: Any, transformer: Any) -> Any:
    if transformer is None:
        return obj
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            x, y = transformer.transform(float(obj[0]), float(obj[1]))
            rest = list(obj[2:])
            return [x, y] + rest
        return [transform_coords(v, transformer) for v in obj]
    return obj


def transform_geometry(geom: dict[str, Any], transformer: Any) -> dict[str, Any]:
    if transformer is None:
        return geom
    g = dict(geom)
    if g.get("type") == "GeometryCollection":
        g["geometries"] = [transform_geometry(x, transformer) for x in g.get("geometries", [])]
    elif "coordinates" in g:
        g["coordinates"] = transform_coords(g["coordinates"], transformer)
    return g


def load_shapefile(path: Path, temp_dir: Optional[Path] = None) -> FeatureStore:
    if shapefile is None:
        raise RuntimeError("pyshpがインストールされていません。pip install pyshp を実行してください。")

    last_error: Optional[Exception] = None
    reader = None
    for enc in ("utf-8", "cp932", "shift_jis", "latin1"):
        try:
            reader = shapefile.Reader(str(path), encoding=enc)
            _ = reader.fields
            if len(reader) > 0:
                _ = reader.record(0)
            break
        except Exception as exc:
            last_error = exc
            reader = None
    if reader is None:
        raise RuntimeError(f"SHP/DBFを読めませんでした: {last_error}")

    fields = [f[0] for f in reader.fields[1:]]
    transformer, crs_note = make_transformer_to_wgs84(path)
    features: list[dict[str, Any]] = []

    for sr in reader.iterShapeRecords():
        props = {name: safe_str(value).strip() for name, value in zip(fields, sr.record)}
        try:
            geom = sr.shape.__geo_interface__
        except Exception:
            continue
        if not geom:
            continue
        geom = transform_geometry(geom, transformer)
        features.append({"type": "Feature", "properties": props, "geometry": geom})

    return FeatureStore(features=features, source_path=path, temp_dir=temp_dir, fields=collect_fields(features), crs_note=crs_note)


def load_any(path: Path) -> FeatureStore:
    suffix = path.suffix.lower()
    if suffix in (".geojson", ".json"):
        return load_geojson(path)
    if suffix == ".shp":
        return load_shapefile(path)
    if suffix == ".zip":
        shp, temp = find_shapefile_in_zip(path)
        store = load_shapefile(shp, temp_dir=temp)
        store.source_path = path
        return store
    raise ValueError("対応形式は .zip / .shp / .geojson / .json です。")


def write_json(path: Path, data: Any, pretty: bool = False) -> None:
    indent = 2 if pretty else None
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
