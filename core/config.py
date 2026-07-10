import json
from pathlib import Path
from typing import Any

class AppConfig:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if self.config_path.exists():
            try:
                self.data = json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Error loading config: {e}")
                self.data = {}
        else:
            self.data = {}
        return self.data

    def save(self, data: dict[str, Any]) -> None:
        self.data = data
        try:
            self.config_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Error saving config: {e}")


class HistoryManager:
    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path
        self.data: list[dict[str, Any]] = []

    def load(self) -> list[dict[str, Any]]:
        if self.history_path.exists():
            try:
                self.data = json.loads(self.history_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"Error loading history: {e}")
                self.data = []
        else:
            self.data = []
        return self.data

    def save(self) -> None:
        try:
            self.history_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Error saving history: {e}")

    def add(
        self,
        name: str,
        bbox: tuple[float, float, float, float],
        geojson: dict[str, Any] | None = None,
    ) -> None:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.data = [item for item in self.data if item.get("name") != name and tuple(item.get("bbox", [])) != bbox]
        item = {
            "name": name,
            "bbox": list(bbox),
            "timestamp": now
        }
        if geojson is not None:
            item["geojson"] = geojson
        self.data.insert(0, item)
        self.save()
