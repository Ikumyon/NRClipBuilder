"""One atomic session transaction includes settings, history and recovery."""
import json
from pathlib import Path

from . import _native
from .state import HISTORY_ID, AppState, Layer

SCHEMA = 1
SESSION_FILENAME = "session-v1.json"


class SessionRepository:
    def __init__(self, directory=None):
        self.path = Path(directory or _native.data_directory()) / SESSION_FILENAME

    def read(self):
        if not self.path.exists():
            return None
        return self.decode(self.read_json(self.path))

    @staticmethod
    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def is_session(payload):
        return (isinstance(payload, dict) and "type" not in payload
                and len({"schema", "state", "datasets"}.intersection(payload)) >= 2)

    @staticmethod
    def decode(payload):
        if not isinstance(payload, dict) or type(payload.get("schema")) is not int or payload["schema"] != SCHEMA:
            raise ValueError("Unsupported session schema")
        metadata = payload["state"]
        state = AppState(**{**metadata, "layers": [Layer(**layer) for layer in metadata["layers"]]})
        datasets = payload["datasets"]
        if not isinstance(datasets, dict):
            raise ValueError("Invalid session datasets")
        ids = [layer.id for layer in state.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate layer IDs in session")
        if any(layer.kind not in {"data", "tile", "history"} or not 0 <= layer.opacity <= 1 for layer in state.layers):
            raise ValueError("Invalid layer metadata in session")
        if any(layer.kind != "tile" and not isinstance(datasets.get(layer.id), list) for layer in state.layers):
            raise ValueError("Missing or invalid layer dataset in session")
        if not any(layer.id == HISTORY_ID and layer.kind == "history" for layer in state.layers):
            raise ValueError("Session is missing the output history layer")
        if state.bbox is not None:
            _native.validate_bbox(state.bbox)
        if not isinstance(state.language, str) or not all(isinstance(value, dict) for value in (
            state.view, state.filter, state.draft, state.export_options,
        )):
            raise ValueError("Invalid session settings")
        if not all(type(state.view.get(key)) in {float, int} for key in ("lat", "lng", "zoom")):
            raise ValueError("Invalid session map view")
        for key in ("keywords", "fields", "excludes", "railway_types"):
            value = state.filter.get(key, [] if key != "railway_types" else None)
            if value is None and key == "railway_types":
                continue
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Invalid session filter: {key}")
        if not isinstance(state.history, list):
            raise ValueError("Invalid session history")
        for item in state.history:
            if not isinstance(item, dict) or not {"id", "name", "bbox", "timestamp", "geojson"} <= item.keys():
                raise ValueError("Invalid session history entry")
            _native.validate_bbox(item["bbox"])
            geojson = item["geojson"]
            if (not isinstance(geojson, dict) or not isinstance(geojson.get("features"), list)
                    or any(not isinstance(feature, dict) or "id" not in feature for feature in geojson["features"])):
                raise ValueError("Invalid session history features")
        return state, datasets

    def write(self, metadata, datasets, token):
        token.check()
        data = json.dumps({"schema": SCHEMA, "state": metadata, "datasets": datasets}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        token.check()
        _native.atomic_write(str(self.path), data)
