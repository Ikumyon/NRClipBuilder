import html
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

    def set_geojson(self, geojson: dict[str, Any], title: str, lang: str = "ja") -> None:
        self.last_geojson = geojson
        self.last_title = title
        self.current_lang = lang
        self.reload_map()

    def reload_map(self) -> None:
        geojson = self.last_geojson if self.last_geojson is not None else {"type": "FeatureCollection", "features": []}
        html_text = make_leaflet_html(geojson, title=self.last_title, lang=self.current_lang)
        out = Path(tempfile.gettempdir()) / "n05_map_filter_exporter_preview.html"
        out.write_text(html_text, encoding="utf-8")
        self.last_html_path = out
        if HAS_WEBENGINE:
            self.web.load(QUrl.fromLocalFile(str(out)))
        else:
            self.web.setHtml(
                f"<p>地図プレビューHTMLを作成しました:</p><p><a href='{out.as_uri()}'>{html.escape(str(out))}</a></p>"
            )

    def retranslate_map(self, lang: str) -> None:
        self.current_lang = lang
        self.reload_map()



class UiLoader(QUiLoader):
    def __init__(self, baseinstance) -> None:
        super().__init__()
        self.baseinstance = baseinstance

    def createWidget(self, classname: str, parent: Optional[QWidget] = None, name: str = "") -> QWidget:
        if parent is None and self.baseinstance:
            return self.baseinstance
        return super().createWidget(classname, parent, name)
