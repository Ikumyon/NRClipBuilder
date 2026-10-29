"""One atomic session transaction includes settings, history and recovery."""
import json
from pathlib import Path

from . import _native
from .state import AppState, Layer

SCHEMA = 1


class SessionRepository:
    def __init__(self, directory=None):
        self.path = Path(directory or _native.data_directory()) / "session-v1.json"

    def read(self):
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA:
            raise ValueError("Unsupported session schema")
        metadata = payload["state"]
        metadata["layers"] = [Layer(**layer) for layer in metadata["layers"]]
        state = AppState(**metadata)
        ids = [layer.id for layer in state.layers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate layer IDs in session")
        if any(layer.kind not in {"data", "tile", "history"} or not 0 <= layer.opacity <= 1 for layer in state.layers):
            raise ValueError("Invalid layer metadata in session")
        if any(layer.kind != "tile" and layer.id not in payload["datasets"] for layer in state.layers):
            raise ValueError("Session is missing a layer dataset")
        if not any(layer.id == "output-history" and layer.kind == "history" for layer in state.layers):
            raise ValueError("Session is missing the output history layer")
        if state.bbox:
            _native.validate_bbox(state.bbox)
        return state, payload["datasets"]

    def write(self, metadata, datasets, token):
        token.check()
        data = json.dumps({"schema": SCHEMA, "state": metadata, "datasets": datasets}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        token.check()
        _native.atomic_write(str(self.path), data)
