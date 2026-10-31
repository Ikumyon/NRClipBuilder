from pathlib import Path
import json

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView

from conftest import javascript, wait_until


@pytest.fixture
def browser(app):
    root = Path(__file__).resolve().parents[1]
    script = root / "build" / "browser-test.js"
    assert script.exists(), "Build src/web/tests/browser-entry.ts first"
    view = QWebEngineView()
    view.resize(900, 600)
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.setHtml(f'<html><head><link rel="stylesheet" href="{script.with_suffix(".css").as_uri()}"></head><body><div id="map"></div><script src="{script.as_uri()}"></script></body></html>', QUrl.fromLocalFile(str(root / "build") + "/"))
    view.show()
    wait_until(lambda: loaded and javascript(view.page(), "typeof mapTest !== 'undefined'"))
    wait_until(lambda: javascript(view.page(), "mapTest.pixel([139,35])[0] > 100 && mapTest.pixel([139,35])[1] > 100"))
    yield view
    javascript(view.page(), "mapTest.destroy()")
    view.close()


def command(view, value):
    javascript(view.page(), f"mapTest.command({json.dumps(value)})")


def point(view, position):
    return QPoint(*json.loads(javascript(view.page(), f"JSON.stringify(mapTest.pixel({json.dumps(position)}))")))


def state(view):
    return json.loads(javascript(view.page(), "JSON.stringify(mapTest.inspect())"))


def move(view, start, end):
    target = view.focusProxy()
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=start)
    for step in range(1, 11):
        QTest.mouseMove(target, start + (end - start) * step / 10, delay=20)
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=end)
    QTest.qWait(100)


def test_real_leaflet_snap_extend_gradient_and_vertex_edit(browser):
    existing = [[139, 35], [139.002, 35]]
    command(browser, {"type": "update", "payload": {"selected": "rail", "bbox": None, "history": [], "layers": [{"id": "rail", "name": "Rail", "kind": "data", "visible": True, "opacity": 1, "revision": 0, "url": "", "attribution": "", "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "source", "properties": {"railway": "rail"}, "geometry": {"type": "LineString", "coordinates": existing}}]}}]}})
    command(browser, {"type": "draw", "active": True})
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=point(browser, existing[0]) + QPoint(0, 6))
    wait_until(lambda: len(state(browser)["points"]) == 1)
    assert state(browser)["points"][0] == existing[0]
    cursor = point(browser, [139.001, 35.001])
    QTest.mouseMove(browser.focusProxy(), cursor)
    wait_until(lambda: "‰" in javascript(browser.page(), "mapTest.controls.at(-1).elevation"))
    assert javascript(browser.page(), "mapTest.requested.every(request => !request.signal.aborted)")
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=cursor)
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=point(browser, existing[1]) + QPoint(0, 6))
    wait_until(lambda: len(state(browser)["points"]) == 3)
    assert state(browser)["points"][-1] == existing[1]
    middle = state(browser)["points"][1]
    move(browser, point(browser, middle), point(browser, [139.0012, 35.0013]))
    after = state(browser)
    assert after["points"][1] != middle
    assert after["segments"][0]["coordinates"][1] == after["points"][1]
    assert after["segments"][1]["coordinates"][0] == after["points"][1]
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.RightButton, pos=point(browser, after["points"][1]))
    wait_until(lambda: len(state(browser)["points"]) == 2)
    assert state(browser)["segments"][0]["coordinates"] == existing


def test_mode_switch_and_empty_right_click_end_drawing(browser):
    command(browser, {"type": "draw", "active": True})
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=point(browser, [139, 35]))
    wait_until(lambda: len(state(browser)["points"]) == 1)
    javascript(browser.page(), "mapTest.toggleBBox()")
    assert javascript(browser.page(), "mapTest.events.some(event => event.type === 'draw_end')")
    command(browser, {"type": "draw", "active": True})
    assert not javascript(browser.page(), "mapTest.controls.at(-1).selecting")
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.RightButton, pos=point(browser, [139, 35]))
    wait_until(lambda: len(state(browser)["points"]) == 0)
    QTest.mouseClick(browser.focusProxy(), Qt.MouseButton.RightButton, pos=point(browser, [139, 35]))
    wait_until(lambda: javascript(browser.page(), "mapTest.events.at(-1).type") == "draw_end")


def test_bbox_tracks_drag_and_normal_double_click_zooms(browser):
    command(browser, {"type": "update", "payload": {"selected": None, "layers": [], "history": [], "bbox": [138.999, 34.999, 139.001, 35.001]}})
    start = point(browser, [138.999, 34.999])
    end = start + QPoint(-30, 30)
    QTest.mousePress(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=start)
    for step in range(1, 6):
        QTest.mouseMove(browser.focusProxy(), start + (end-start)*step/5, delay=30)
    rectangles = javascript(browser.page(), "document.querySelectorAll('.bbox-handle').length")
    assert rectangles == 4
    assert state(browser)["bbox"][0] < 138.999
    QTest.mouseRelease(browser.focusProxy(), Qt.MouseButton.LeftButton, pos=end)
    wait_until(lambda: javascript(browser.page(), "mapTest.events.some(event => event.type === 'bbox')"))
    assert javascript(browser.page(), "mapTest.events.findLast(event => event.type === 'bbox').bbox[0]") < 138.999
    javascript(browser.page(), "document.getElementById('map').dispatchEvent(new MouseEvent('dblclick', {bubbles:true,clientX:450,clientY:300,button:0}))")
    wait_until(lambda: javascript(browser.page(), "mapTest.events.some(event => event.type === 'view' && event.view.zoom > 16)"))


def test_saved_manual_segment_hover_includes_attributes_and_measurements(browser):
    command(browser, {"type": "update", "payload": {"selected": "rail", "bbox": None, "history": [], "layers": [{"id": "rail", "name": "Rail", "kind": "data", "visible": True, "opacity": 1, "revision": 0, "url": "", "attribution": "", "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "id": "saved", "properties": {"name": "Extension", "_manual_segment": True, "bridge": "yes"}, "geometry": {"type": "LineString", "coordinates": [[139,35],[139.002,35]]}}]}}]}})
    QTest.mouseMove(browser.focusProxy(), point(browser, [139.001,35]))
    wait_until(lambda: javascript(browser.page(), "document.querySelector('.leaflet-tooltip')?.textContent.includes('‰') ?? false"))
    tooltip = javascript(browser.page(), "document.querySelector('.leaflet-tooltip').textContent")
    assert "Extension" in tooltip and "距離" in tooltip and "標高" in tooltip
