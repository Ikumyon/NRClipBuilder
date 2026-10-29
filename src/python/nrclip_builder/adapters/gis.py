from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

import shapefile
from pyproj import CRS, Transformer


def normalize_features(features, token):
    result = []
    for feature in features:
        token.check()
        if not feature.get("geometry"):
            continue
        result.append({"type": "Feature", "id": uuid4().hex,
                       "properties": feature.get("properties") or {}, "geometry": feature["geometry"]})
    return result


def load_geojson(path: Path, token):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    kind = data.get("type")
    if kind == "FeatureCollection":
        features = data["features"]
    elif kind == "Feature":
        features = [data]
    elif kind in {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection"}:
        features = [{"geometry": data}]
    else:
        raise ValueError("Expected a GeoJSON FeatureCollection, Feature or Geometry")
    return normalize_features(features, token)


def load_shapefile(path: Path, token):
    siblings = {p.suffix.lower(): p for p in path.parent.iterdir() if p.stem.lower() == path.stem.lower()}
    prj = siblings.get(".prj")
    transformer = None
    if prj:
        source = CRS.from_wkt(prj.read_text(encoding="utf-8-sig"))
        transformer = Transformer.from_crs(source, "EPSG:4326", always_xy=True)

    def transform_geometry(geometry):
        if geometry["type"] == "GeometryCollection":
            return {"type": "GeometryCollection", "geometries": [transform_geometry(g) for g in geometry["geometries"]]}

        def coords(value):
            token.check()
            if value and isinstance(value[0], (int, float)):
                if transformer:
                    x, y = transformer.transform(value[0], value[1], errcheck=True)
                    return [x, y, *value[2:]]
                return list(value)
            return [coords(p) for p in value]
        return {"type": geometry["type"], "coordinates": coords(geometry["coordinates"])}

    cpg = siblings.get(".cpg")
    encodings = [cpg.read_text(encoding="utf-8-sig").strip()] if cpg else ["utf-8", "cp932"]
    if encodings == ["65001"]:
        encodings = ["utf-8"]
    last_error = None
    for encoding in encodings:
        try:
            options = {suffix[1:]: str(siblings[suffix]) for suffix in (".shp", ".shx", ".dbf") if suffix in siblings}
            with shapefile.Reader(encoding=encoding, **options) as reader:
                fields = [f[0] for f in reader.fields[1:]]
                result = []
                for record in reader.iterShapeRecords():
                    token.check()
                    if record.shape.shapeType == shapefile.NULL:
                        continue
                    props = {name: str(value) if value is not None else "" for name, value in zip(fields, record.record)}
                    result.append({"type": "Feature", "id": uuid4().hex, "properties": props,
                                   "geometry": transform_geometry(record.shape.__geo_interface__)})
                return result
        except (UnicodeDecodeError, shapefile.dbfFileException) as exc:
            last_error = exc
    raise ValueError(f"Cannot decode DBF: {last_error}")


def load(path: Path, token):
    token.check()
    suffix = path.suffix.lower()
    if suffix in {".json", ".geojson"}:
        return load_geojson(path, token)
    if suffix == ".shp":
        return load_shapefile(path, token)
    if suffix == ".zip":
        with tempfile.TemporaryDirectory(prefix="nrclip-") as folder:
            root = Path(folder).resolve()
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    token.check()
                    # Interpret both separator spellings before validating archive paths.
                    relative = Path(info.filename.replace("\\", "/"))
                    destination = (root / relative).resolve()
                    if not destination.is_relative_to(root) or relative.is_absolute():
                        raise ValueError("ZIP contains a path outside its extraction directory")
                    archive.extract(info, root)
            files = sorted((p for p in root.rglob("*") if p.suffix.lower() == ".shp"),
                           key=lambda p: ("railroadsection" not in p.name.lower(), not any(w in p.name.lower() for w in ("rail", "line", "section")), str(p)))
            if not files:
                raise ValueError("ZIP contains no Shapefile")
            return load_shapefile(files[0], token)
    raise ValueError("Supported formats: ZIP, SHP, GeoJSON, JSON")
