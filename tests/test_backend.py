from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
import json

import pytest
import shapefile
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest

from nrclip_builder import _native
from nrclip_builder.adapters import gis, network
from conftest import wait_until


def line(identifier, name="Rail 東京", railway="rail"):
    return {"type": "Feature", "id": identifier, "properties": {"name": name, "railway": railway},
            "geometry": {"type": "LineString", "coordinates": [[139, 35], [139.001, 35]]}}


def idle(window):
    wait_until(lambda: not window.service.jobs.inflight and not window.service.edit_busy)


def test_regex_and_railway_filters_match_render_and_checked_multilayer_export(window, tmp_path):
    service = window.service
    service.add_features("First", [line("a"), line("b", "Rail 東京", "tram")])
    service.add_features("Second", [line("c"), line("d", "Else 東京")])
    idle(window)
    service.state.filter = {"keywords": [r"(?<=Rail )東京"], "regex": True, "railway_types": ["rail"]}
    service.set_bbox([138, 34, 140, 36])
    service.render()
    idle(window)
    data_layers = [layer for layer in service.state.layers if layer.kind == "data"]
    assert sum(service.selections[layer.id].count for layer in data_layers) == 2
    request = service.prepare_export()
    assert set(request.names) == {"First", "Second"}
    data, snapshot = request.batch.export("geojson", "{}", _native.CancelToken())
    assert len(json.loads(data)["features"]) == 2
    assert len(json.loads(snapshot)["features"]) == 2
    engine = service.engine
    for expression in [r"(?=Rail)Rail", r"(?-i:Rail)"]:
        assert engine.select(data_layers[0].id, json.dumps({"regex": True, "keywords": [expression]}), _native.CancelToken()).count == 2


def test_history_replace_keyboard_multiselect_delete_and_bbox(window, tmp_path):
    service = window.service
    service.add_features("Lines", [line("a")])
    service.set_bbox([138, 34, 140, 36])
    idle(window)
    for filename in ["same.geojson", "same.geojson", "other.geojson"]:
        service.export(tmp_path / filename, "geojson", {}, service.prepare_export())
        idle(window)
    assert len(service.state.history) == 2
    assert (tmp_path / "same.geojson").exists()
    window.tabs.setCurrentIndex(2)
    selection = window.history_table.selectionModel()
    selection.setCurrentIndex(window.history_model.index(0, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    QTest.keyClick(window.history_table, Qt.Key.Key_Down)
    wait_until(lambda: service.state.selected_history == service.state.history[1]["id"])
    assert service.state.bbox == service.state.history[1]["bbox"]
    window.history_table.selectAll()
    assert len(selection.selectedRows()) == 2
    window.delete_history()
    idle(window)
    assert service.state.history == []
    assert service.state.bbox is None
    assert service.state.selected_history is None
    assert window.history_model.rowCount() == 0
    assert service.engine.capture("output-history").count == 0


def test_delete_layer_prunes_undo_and_maps_survive_removal_and_session_restore(window):
    service = window.service
    service.add_features("Lines", [line("a")])
    idle(window)
    layer = next(layer for layer in service.state.layers if layer.kind == "data")
    changed = line("a", "Edited")
    service.edit(layer.id, changed)
    idle(window)
    assert service.undo
    service.remove_layer(layer.id)
    service.undo_edit()
    assert service.undo == []
    service.add_map("Custom", "https://example.invalid/{z}/{x}/{y}.png", "Test")
    tile = service.state.layers[-1]
    service.remove_layer(tile.id)
    assert service.state.registered_maps[0]["name"] == "Custom"
    service.state.draft = {"points": [[139, 35], [139.001, 35]], "segments": [{"structure": "bridge", "coordinates": [[139, 35], [139.001, 35]]}]}
    idle(window)
    service.save()
    idle(window)
    restored, datasets = service.repository.read()
    assert restored.registered_maps == service.state.registered_maps
    assert restored.draft == service.state.draft
    assert all(layer.kind != "data" for layer in restored.layers)


def test_preview_includes_view_and_language_without_opening_a_browser(window, tmp_path, monkeypatch):
    service = window.service
    service.add_features("Lines", [line("a")])
    service.render()
    idle(window)
    wait_until(lambda: window.map.last_payload is not None)
    window.locale.choose("ja-jp")
    service.state.view = {"lat": 43, "lng": 141, "zoom": 12}
    opened = []
    monkeypatch.setattr(_native, "open_external", opened.append)
    path = tmp_path / "preview.html"
    window.map.open_preview(path, service.state.view, window.locale.language, window.locale.strings)
    html = path.read_text(encoding="utf-8")
    context = json.loads(html.split("window.NRCLIP_PREVIEW=", 1)[1].split(";</script>", 1)[0])
    assert context["view"] == service.state.view
    assert context["strings"]["map_elevation"] == "標高"
    assert context["language"] == "ja-jp"
    assert "qrc:///qtwebchannel" not in html
    assert opened == [str(path)]


def test_latin1_dbf_without_cpg(tmp_path):
    path = tmp_path / "latin.shp"
    with shapefile.Writer(str(path), shapeType=shapefile.POLYLINE, encoding="latin1") as writer:
        writer.field("name", "C", 30)
        writer.line([[[139, 35], [139.001, 35]]])
        writer.record("Café")
    token = _native.CancelToken()
    assert gis.load_shapefile(path, token)[0]["properties"]["name"] == "Café"


def test_explicit_cpg_is_used_before_automatic_detection(monkeypatch):
    from types import SimpleNamespace
    requested = []
    class Entry:
        stem = "rail"
        def __init__(self, suffix): self.suffix = suffix
        def read_text(self, **kwargs): return "latin1"
        def __str__(self): return "rail" + self.suffix
    path = SimpleNamespace(stem="rail", parent=SimpleNamespace(iterdir=lambda: [Entry(suffix) for suffix in [".shp", ".shx", ".dbf", ".cpg"]]))
    class Reader:
        fields = [("DeletionFlag",), ("name",)]
        def __init__(self, **kwargs): requested.append(kwargs["encoding"])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iterShapeRecords(self): return []
    monkeypatch.setattr(gis.shapefile, "Reader", Reader)
    assert gis.load_shapefile(path, _native.CancelToken()) == []
    assert requested == ["latin1"]


def test_finish_draft_keeps_snap_coordinates_and_undo_restores_source(window):
    service = window.service
    service.add_features("Connected", [line("source")])
    idle(window)
    layer = next(layer for layer in service.state.layers if layer.kind == "data")
    start, end = [139.001, 35], [139.002, 35.001]
    service.state.draft = {"points": [start, end], "segments": [{"structure": "bridge", "coordinates": [start, end]}]}
    service.finish_draft("Extension")
    idle(window)
    features = json.loads(service.engine.snapshot(layer.id))
    assert len(features) == 2
    extension = next(item for item in features if item["id"] != "source")
    assert extension["geometry"]["coordinates"][0] == features[0]["geometry"]["coordinates"][-1]
    assert extension["properties"]["_manual_segment"] is True
    assert extension["properties"]["bridge"] == "yes"
    assert service.state.draft == {"points": [], "segments": []}
    service.undo_edit()
    idle(window)
    assert service.engine.capture(layer.id).count == 1


def test_removing_target_during_draft_commit_does_not_leave_drawing_locked(window):
    service = window.service
    service.add_features("Target", [line("a")])
    idle(window)
    layer = next(layer for layer in service.state.layers if layer.kind == "data")
    service.state.draft = {"points": [[139,35],[139.001,35]], "segments": [{"structure":"ground","coordinates":[[139,35],[139.001,35]]}]}
    service.finish_draft("Extension")
    service.remove_layer(layer.id)
    idle(window)
    assert not service.draft_committing
    assert service.state.draft["points"]
    assert not service.undo


def test_osm_cancel_aborts_a_server_that_has_not_sent_headers(app, monkeypatch):
    entered, release = Event(), Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            release.wait(5)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"elements":[]}')
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(network, "OVERPASS_URL", f"http://127.0.0.1:{server.server_port}")
    token = _native.CancelToken()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(network.fetch, [138, 34, 140, 36], token)
            try:
                wait_until(lambda: entered.is_set() or future.done())
                if future.done():
                    future.result()
                assert entered.is_set()
                token.cancel()
                wait_until(future.done, seconds=2)
                with pytest.raises(ValueError, match="cancelled"):
                    future.result()
            finally:
                token.cancel()
                release.set()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
    assert network.SERVER_TIMEOUT == 180
    assert network.COMMUNICATION_TIMEOUT == 240
