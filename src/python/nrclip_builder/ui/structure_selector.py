"""Icon-only, exclusive structure selection for the drawing toolbar."""
from .segmented_control import SegmentedControl


class StructureSelector(SegmentedControl):
    VALUES = ("ground", "bridge", "tunnel")

    def __init__(self, parent=None):
        super().__init__(self.VALUES, parent, icons={
            "ground": "ground", "bridge": "bridge-4", "tunnel": "train-front-tunnel",
        })
        self.setObjectName("structureSelector")
        for value, button in self.buttons.items():
            button.setObjectName("structure_" + value)

