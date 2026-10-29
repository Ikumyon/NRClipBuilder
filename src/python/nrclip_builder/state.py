from dataclasses import asdict, dataclass, field
from uuid import uuid4

HISTORY_ID = "output-history"


@dataclass
class Layer:
    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    kind: str = "data"
    visible: bool = True
    opacity: float = 1.0
    source: str | None = None
    revision: int = 0
    url: str = ""
    attribution: str = ""


@dataclass
class AppState:
    layers: list[Layer] = field(default_factory=list)
    selected: str | None = None
    selected_history: str | None = None
    bbox: list[float] | None = None
    history: list[dict] = field(default_factory=list)
    draft: dict = field(default_factory=lambda: {"points": [], "segments": []})
    language: str = ""
    view: dict = field(default_factory=lambda: {"lat": 35.68, "lng": 139.76, "zoom": 6})
    filter: dict = field(default_factory=dict)
    export_options: dict = field(default_factory=dict)
    registered_sources: list[str] = field(default_factory=list)
    last_directory: str = ""

    def layer(self, layer_id):
        return next((layer for layer in self.layers if layer.id == layer_id), None)

    def metadata(self):
        return asdict(self)


def initial_state():
    return AppState(layers=[
        Layer("OpenStreetMap", id="osm-tiles", kind="tile", url="https://tile.openstreetmap.org/{z}/{x}/{y}.png", attribution="© OpenStreetMap contributors"),
        Layer("国土地理院地図", id="gsi-tiles", kind="tile", visible=False, url="https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", attribution="国土地理院"),
        Layer("国土地理院写真", id="gsi-photo", kind="tile", visible=False, url="https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", attribution="国土地理院"),
        Layer("過去の出力履歴", id=HISTORY_ID, kind="history", visible=False),
    ])
