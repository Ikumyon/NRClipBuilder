import base64
import html
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QUrl, Qt

from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextBrowser

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    QWebEngineSettings = None
    QWebEnginePage = None
    HAS_WEBENGINE = False

from core.leaflet import make_leaflet_html
from core.utils import get_resource_path


class CustomWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level: int, message: str, lineNumber: int, sourceID: str) -> None:
        print(f"JS Console message: {message} at line {lineNumber} (source: {sourceID})")


class MapWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_WEBENGINE:
            self.web = QWebEngineView()
            self.page = CustomWebPage(self.web)
            self.web.setPage(self.page)
            if QWebEngineSettings is not None:
                settings = self.web.settings()
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            layout.addWidget(self.web)
        else:
            self.web = QTextBrowser()
            self.web.setOpenExternalLinks(True)
            self.web.setHtml("<p>PySide6 QtWebEngine が見つかりません。<br>requirements.txt の依存関係を入れてください。</p>")
            layout.addWidget(self.web)
        self.last_html_path: Optional[Path] = None
        self.last_geojson: Optional[dict[str, Any]] = None
        self.last_title: str = "NRClipBuilder"
        self.current_lang: str = "ja"
        self.current_translation: Optional[dict[str, Any]] = None
        self.tile_configs: list[dict[str, str]] = []
        self.current_view: Optional[dict[str, Any]] = None
        self.layer_opacities: dict[str, float] = {}

    def set_tile_configs(self, configs: list[dict[str, str]]) -> None:
        self.tile_configs = configs

    def set_layer_opacities(self, opacities: dict[str, float]) -> None:
        self.layer_opacities = opacities




    def set_geojson(
        self,
        geojson: dict[str, Any],
        title: str,
        lang: str = "ja",
        translation: dict[str, Any] = None,
        preserve_view: bool = True,
    ) -> None:
        self.last_geojson = geojson
        self.last_title = title
        self.current_lang = lang
        self.current_translation = translation
        self.reload_map(preserve_view=preserve_view)

    def reload_map(self, preserve_view: bool = True) -> None:
        initial_view = self.current_view if preserve_view else None
        self._reload_map_with_view(initial_view)

    def _reload_map_with_view(self, initial_view: Optional[dict[str, Any]]) -> None:
        geojson = self.last_geojson if self.last_geojson is not None else {"type": "FeatureCollection", "features": []}
        
        svg_path = get_resource_path("assets/icons/layers.svg")
        svg_base64 = ""
        if svg_path.exists():
            try:
                svg_base64 = base64.b64encode(svg_path.read_bytes()).decode("utf-8")
            except Exception as e:
                print(f"Error loading layers.svg: {e}")

        html_text = make_leaflet_html(
            geojson,
            title=self.last_title,
            lang=self.current_lang,
            translation=self.current_translation,
            tile_configs=self.tile_configs,
            layers_svg_base64=svg_base64,
            initial_view=initial_view,
            layer_opacities=self.layer_opacities,
        )

        out = Path(tempfile.gettempdir()) / "n05_map_filter_exporter_preview.html"
        out.write_text(html_text, encoding="utf-8")
        self.last_html_path = out
        if HAS_WEBENGINE:
            self.web.load(QUrl.fromLocalFile(str(out)))
        else:
            self.web.setHtml(
                f"<p>地図プレビューHTMLを作成しました:</p><p><a href='{out.as_uri()}'>{html.escape(str(out))}</a></p>"
            )

    def retranslate_map(self, lang: str, translation: dict[str, Any] = None) -> None:
        self.current_lang = lang
        self.current_translation = translation
        self.reload_map(preserve_view=True)



class UiLoader(QUiLoader):
    def __init__(self, baseinstance) -> None:
        super().__init__()
        self.baseinstance = baseinstance

    def createWidget(self, classname: str, parent: Optional[QWidget] = None, name: str = "") -> QWidget:
        if parent is None and self.baseinstance:
            return self.baseinstance
        return super().createWidget(classname, parent, name)
