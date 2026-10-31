from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from . import _native
from .adapters import gis, network
from .jobs import Jobs
from .state import HISTORY_ID, Layer, initial_state
from .storage import SessionRepository


@dataclass(frozen=True)
class ExportRequest:
    batch: _native.ExportBatch
    names: tuple[str, ...]
    bbox: tuple[float, ...] | None
    filters: str
    history: bool = False


class ApplicationService(QObject):
    changed = Signal()
    rendered = Signal(object)
    failed = Signal(str)
    message = Signal(str)
    ready = Signal()

    def __init__(self, parent=None, repository=None):
        super().__init__(parent)
        self.engine = _native.Engine()
        self.state = initial_state()
        self.repository = repository or SessionRepository()
        self.jobs = Jobs(self)
        self.jobs.failed.connect(self.failed)
        self.selections = {}
        self.render_cache = {}
        self.undo = []
        self.edits = deque()
        self.edit_busy = False
        self.draft_committing = False
        self.save_pending = False
        self.initialized = False
        self.strings = {}
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(750)
        self.save_timer.timeout.connect(self.save)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(180)
        self.render_timer.timeout.connect(self.render)

    def text(self, key, **values):
        return self.strings.get(key, key).format(**values)

    def start(self):
        def read(token):
            loaded = self.repository.read()
            state = loaded[0] if loaded else self.state
            datasets = loaded[1] if loaded else {}
            for layer in state.layers:
                if layer.kind != "tile":
                    self.engine.register(layer.id, json.dumps(datasets.get(layer.id, []), ensure_ascii=False), token)
            return state

        def done(state):
            self.state = state
            self.initialized = True
            self.changed.emit()
            self.ready.emit()
            self.render()

        def failure(error):
            self.failed.emit(f"{self.text('session_restore_failed')}\n{error}")
            # Do not overwrite an unreadable session with a new empty session.
            self.ready.emit()
        self.jobs.submit("startup", read, done, failure)

    def touch(self, render=True, notify=True):
        if notify:
            self.changed.emit()
        if self.initialized:
            self.save_pending = True
            self.save_timer.start()
        if render:
            self.render_timer.start()

    def save(self):
        if not self.initialized:
            return
        if self.jobs.running("save") or self.edit_busy:
            self.save_timer.start()
            return
        metadata = self.state.metadata()
        datasets = {layer.id: self.engine.capture(layer.id) for layer in self.state.layers if layer.kind != "tile"}
        self.save_pending = False

        def write(token):
            features = {key: json.loads(snapshot.geojson(token))["features"] for key, snapshot in datasets.items()}
            features[HISTORY_ID] = [
                {**feature, "id": f'{item["id"]}:{feature["id"]}'}
                for item in metadata["history"] for feature in item["geojson"]["features"]
            ]
            self.repository.write(metadata, features, token)

        def failed(error):
            self.save_pending = True
            self.failed.emit(f"{self.text('session_save_failed')}\n{error}")
        self.jobs.submit("save", write, lambda _: self.save_timer.start() if self.save_pending else None, failed)

    def add_features(self, name, features, source=None):
        layer = Layer(name=name, source=source)

        def register(token):
            self.engine.register(layer.id, json.dumps(features, ensure_ascii=False), token)
            return layer
        self.jobs.submit(f"add:{layer.id}", register, self._added)

    def load(self, path):
        path = Path(path)
        layer = Layer(name=path.name, source=str(path.resolve()))

        def read(token):
            features = gis.load(path, token)
            self.engine.register(layer.id, json.dumps(features, ensure_ascii=False), token)
            return layer
        self.jobs.submit(f"add:{layer.id}", read, self._added)

    def _added(self, layer):
        self.state.layers.append(layer)
        self.state.selected = layer.id
        if layer.source:
            self.state.registered_sources = [layer.source, *[s for s in self.state.registered_sources if s != layer.source]]
            self.state.last_directory = str(Path(layer.source).parent)
        self.touch()
        if layer.kind == "data":
            self.message.emit(self.text("msg_load_success", count=self.engine.capture(layer.id).count))

    def fetch_osm(self):
        if not self.state.bbox:
            raise ValueError(self.text("msg_bbox_warn"))
        bbox = list(self.state.bbox)
        layer = Layer("OSM")

        def fetch(token):
            features = network.fetch(bbox, token)
            self.engine.register(layer.id, json.dumps(features, ensure_ascii=False), token)
            return layer
        if not self.jobs.running("osm"):
            self.jobs.submit("osm", fetch, self._added)

    def render(self):
        if not self.initialized:
            return
        layers = [Layer(**vars(layer)) for layer in self.state.layers]
        snapshots = {layer.id: self.engine.capture(layer.id) for layer in layers if layer.kind != "tile"}
        selected = self.state.selected
        options = dict(self.state.filter)
        filter_key = json.dumps(options, sort_keys=True)
        previous_cache = dict(self.render_cache)
        previous_selections = dict(self.selections)
        bbox = self.state.bbox
        history = [{"id": h["id"], "name": h["name"], "bbox": h["bbox"]} for h in self.state.history]

        def compute(token):
            selections, rendered, cache = {}, [], {}
            for layer in layers:
                token.check()
                if layer.kind == "tile":
                    rendered.append({**vars(layer), "geojson": None})
                    continue
                key = (layer.revision, filter_key)
                previous = previous_cache.get(layer.id)
                unchanged = previous is not None and previous[:2] == key and layer.id in previous_selections
                selection = previous_selections[layer.id] if unchanged else snapshots[layer.id].select(filter_key, token)
                selections[layer.id] = selection
                send_geometry = layer.visible and (not unchanged or not previous[2])
                rendered.append({**vars(layer), "geojson": selection.geojson(token) if send_geometry else None})
                cache[layer.id] = (*key, layer.visible)
            token.check()
            return selections, cache, {"layers": rendered, "selected": selected, "bbox": bbox, "history": history}

        def done(result):
            self.selections, self.render_cache, payload = result
            self.rendered.emit(payload)
        self.jobs.submit("render", compute, done)

    def set_bbox(self, bbox):
        if bbox is not None:
            _native.validate_bbox(bbox)
        self.state.bbox = bbox
        if self.state.filter.get("bbox") is not None:
            self.state.filter["bbox"] = bbox
        self.touch()

    def remove_layer(self, layer_id):
        layer = self.state.layer(layer_id)
        if not layer or layer.kind == "history":
            return
        self.state.layers.remove(layer)
        self.undo = [(key, old) for key, old in self.undo if key != layer_id]
        self.edits = deque(edit for edit in self.edits if edit[0] != layer_id)
        if layer.kind != "tile":
            self.engine.remove(layer_id)
        self.selections.pop(layer_id, None)
        if self.state.selected == layer_id:
            self.state.selected = None
        self.touch()

    def edit(self, layer_id, feature, record_undo=True, committed=None):
        layer = self.state.layer(layer_id)
        if not layer or layer.kind != "data":
            raise ValueError(self.text("editable_layer_required"))
        self.edits.append((layer_id, [feature] if isinstance(feature, dict) else feature, record_undo, committed))
        self._next_edit()

    def _next_edit(self):
        if self.edit_busy or not self.edits:
            return
        self.edit_busy = True
        layer_id, features, record_undo, committed = self.edits.popleft()

        def update(token):
            return json.loads(self.engine.edit_many(layer_id, json.dumps(features, ensure_ascii=False), token))

        def finish(old):
            layer = self.state.layer(layer_id)
            if layer:
                layer.revision += 1
                if record_undo:
                    self.undo.append((layer_id, old))
                if committed:
                    committed()
                self.touch()
            elif committed:
                self.draft_committing = False
                self.changed.emit()
            self.edit_busy = False
            self._next_edit()

        def failure(error):
            self.edit_busy = False
            self.draft_committing = False
            if str(error) == "cancelled":
                self.edits.clear()
                self.message.emit(self.text("operation_cancelled"))
            elif self.state.layer(layer_id) is not None:
                self.failed.emit(str(error))
            self.changed.emit()
            self._next_edit()
        self.jobs.submit("edit", update, finish, failure, lambda: failure("cancelled"))

    def undo_edit(self):
        if self.edit_busy or self.edits:
            return
        while self.undo:
            layer_id, feature = self.undo.pop()
            layer = self.state.layer(layer_id)
            if layer and layer.kind == "data":
                self.edit(layer_id, feature, False)
                break

    def add_map(self, name, url, attribution):
        item = {"name": name, "url": url, "attribution": attribution}
        self.state.registered_maps = [item, *[value for value in self.state.registered_maps if value["url"] != url]]
        self.state.layers.append(Layer(name, kind="tile", url=url, attribution=attribution))
        self.touch()

    def delete_history(self, ids):
        self.state.history = [item for item in self.state.history if item["id"] not in ids]
        self.state.selected_history = None
        self.state.bbox = None
        self.state.filter["bbox"] = None
        self.rebuild_history()

    def finish_draft(self, name):
        if self.draft_committing:
            return
        segments = self.state.draft.get("segments", [])
        if not segments:
            return
        self.draft_committing = True
        layer = self.state.layer(self.state.selected)
        if not layer or layer.kind != "data":
            layer = Layer(name)
            # Register the empty dataset through the same worker path, then enqueue edits.
            def initialized(_):
                self._added(layer)
                self._draft_features(layer, name, segments)
            def failed(error):
                self.draft_committing = False
                self.failed.emit(str(error))
            self.jobs.submit(f"add:{layer.id}", lambda token: self.engine.register(layer.id, "[]", token), initialized, failed, lambda: failed("cancelled"))
        else:
            self._draft_features(layer, name, segments)

    def _draft_features(self, layer, name, segments):
        features = []
        for segment in segments:
            structure = segment.get("structure", "ground")
            props = {"name": name, "railway": "rail", "_manual_segment": True, "layer": {"ground": "0", "bridge": "1", "tunnel": "-1"}[structure]}
            if structure != "ground":
                props["bridge" if structure == "bridge" else "tunnel"] = "yes"
            features.append({"type": "Feature", "id": uuid4().hex, "properties": props,
                             "geometry": {"type": "LineString", "coordinates": segment["coordinates"]}})
        original = self.state.draft
        def clear_committed_draft():
            self.draft_committing = False
            if self.state.draft is original:
                self.state.draft = {"points": [], "segments": []}
        self.edit(layer.id, features, committed=clear_committed_draft)

    def prepare_export(self, history_id=None):
        if self.edit_busy or self.edits:
            raise ValueError(self.text("pending_edits"))
        if history_id is not None:
            item = next((item for item in self.state.history if item["id"] == history_id), None)
            if item is None:
                raise ValueError(self.text("history_selection_required"))
            bbox = tuple(item["bbox"])
            batch = _native.ExportBatch.from_history(json.dumps(item["geojson"], ensure_ascii=False), bbox)
            return ExportRequest(batch, (item["name"],), bbox, "{}", True)
        layers = [layer for layer in self.state.layers if layer.kind == "data" and layer.visible]
        bbox = tuple(self.state.bbox) if self.state.bbox else None
        filters = json.dumps(self.state.filter, ensure_ascii=False)
        batch = self.engine.prepare_export([layer.id for layer in layers], filters, bbox)
        return ExportRequest(batch, tuple(layer.name for layer in layers), bbox, filters)

    def export(self, path, format, options, request):
        if not request.names:
            raise ValueError(self.text("export_no_targets"))
        if request.bbox is None:
            raise ValueError(self.text("export_bbox_missing"))
        if self.jobs.running("export"):
            raise ValueError(self.text("export_running"))
        options = {**options, "name": Path(path).stem}

        def convert(token):
            data, geojson = request.batch.export(format, json.dumps(options), token)
            geojson = json.loads(geojson)
            token.check()
            _native.atomic_write(str(path), data)
            # After the commit, deliver completion even if cancellation arrives.
            return geojson

        def done(geojson):
            item = {"id": uuid4().hex, "name": options["name"], "bbox": list(request.bbox),
                    "timestamp": datetime.now(timezone.utc).isoformat(), "geojson": geojson}
            self.state.history = [old for old in self.state.history if not (old["name"] == item["name"] and old["bbox"] == item["bbox"])]
            self.state.history.insert(0, item)
            self.state.last_directory = str(Path(path).parent)
            self.rebuild_history()
            self.message.emit(self.text("export_completed", path=path))
        self.jobs.submit("export", convert, done)

    def rebuild_history(self):
        features = []
        for item in self.state.history:
            for feature in item["geojson"]["features"]:
                features.append({**feature, "id": f'{item["id"]}:{feature["id"]}'})
        layer = self.state.layer(HISTORY_ID)
        if not layer:
            layer = Layer("過去の出力履歴", id=HISTORY_ID, kind="history", visible=False)
            self.state.layers.append(layer)

        def done(_):
            layer.revision += 1
            self.touch()
        self.jobs.submit("history", lambda token: self.engine.register(HISTORY_ID, json.dumps(features, ensure_ascii=False), token), done)
