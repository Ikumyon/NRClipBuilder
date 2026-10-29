import json
from importlib.resources import files

from PySide6.QtCore import QObject, Signal

from .. import _native


def resource(name):
    return files("nrclip_builder").joinpath("resources", name)


class Localisation(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.catalogues = {}
        for path in resource("localisation").iterdir():
            if path.name.endswith(".json"):
                self.catalogues[path.name[:-5]] = json.loads(path.read_text(encoding="utf-8"))
        self.language = "en-us"
        self.choose(_native.system_locale())

    @property
    def strings(self):
        return {**self.catalogues["en-us"], **self.catalogues.get(self.language, {})}

    def text(self, key, default=None):
        return self.strings.get(key, default or key)

    def choose(self, language):
        if language not in self.catalogues:
            language = next((code for code in self.catalogues if code.split("-")[0] == language.split("-")[0]), "en-us")
        self.language = language
        self.changed.emit()
