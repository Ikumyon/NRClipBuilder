import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .. import _native
from .localisation import resource


class Bridge(QObject):
    event = Signal(object)
    command = Signal(str)

    @Slot(str)
    def receive(self, raw):
        try:
            event = json.loads(raw)
            if isinstance(event, dict) and isinstance(event.get("type"), str):
                self.event.emit(event)
        except (ValueError, TypeError):
            return


class MapPage(QWebEnginePage):
    def __init__(self, view):
        super().__init__(view)
        self.map_url = QUrl.fromLocalFile(str(Path(str(resource("map/index.html"))).resolve()))
        if not Path(self.map_url.toLocalFile()).is_file():
            raise RuntimeError("Map assets are missing. Run npm ci and npm run build in src/web/.")

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return not is_main_frame or url == self.map_url

    def javaScriptConsoleMessage(self, level, message, line_number, source):
        logging.getLogger(__name__).warning("Map JavaScript %s:%s: %s", source, line_number, message)


class MapView(QWebEngineView):
    event = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.map_page = MapPage(self)
        self.setPage(self.map_page)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.bridge.event.connect(self._event)
        self.loadFinished.connect(lambda ok: self.failed.emit("Map resources could not be loaded") if not ok else None)
        self.pending = {}
        self.map_ready = False
        self.last_payload = None
        self.geometry_cache = {}
        self.load(self.map_page.map_url)

    def _event(self, event):
        if event["type"] == "ready":
            self.map_ready = True
            for command in self.pending.values():
                self.send(command)
            self.pending.clear()
        self.event.emit(event)

    def send(self, command):
        if not self.map_ready:
            self.pending[command["type"]] = command
            return
        self.bridge.command.emit(json.dumps(command, ensure_ascii=False))

    def update_layers(self, payload):
        ids = {layer["id"] for layer in payload["layers"]}
        self.geometry_cache = {key: value for key, value in self.geometry_cache.items() if key in ids}
        for layer in payload["layers"]:
            if layer["geojson"] is not None:
                self.geometry_cache[layer["id"]] = layer["geojson"]
        self.last_payload = {**payload, "layers": [
            {**layer, "geojson": self.geometry_cache.get(layer["id"]) if layer["visible"] else None}
            for layer in payload["layers"]
        ]}
        self.send({"type": "update", "payload": payload if self.map_ready else self.last_payload})

    def open_preview(self, path):
        if self.last_payload is None:
            raise ValueError("No map preview is available")
        folder = Path(str(resource("map"))).resolve()
        html = resource("map/index.html").read_text(encoding="utf-8")
        html = html.replace('<script src="qrc:///qtwebchannel/qwebchannel.js"></script>', "")
        for name in ("app.css", "app.js"):
            html = html.replace(f'"{name}"', f'"{(folder / name).as_uri()}"')
        payload = json.dumps(self.last_payload, ensure_ascii=False).replace("<", "\\u003c")
        html = html.replace('<script src=', f'<script>window.NRCLIP_PREVIEW={payload};</script><script src=', 1)
        _native.atomic_write(str(path), html.encode("utf-8"))
        _native.open_external(str(path))
