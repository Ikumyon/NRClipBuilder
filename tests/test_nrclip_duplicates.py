import unittest

from core.nrclip import geojson_to_nrclip_bytes
from core.nrclip_serde import decode_collections_nrclip


def make_line(coords: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "properties": {"railway": "rail"},
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def track_count(features: list[dict]) -> int:
    data = geojson_to_nrclip_bytes(features, "duplicate-check", max_spacing=2000.0)
    decoded = decode_collections_nrclip(data)
    return len(decoded["collections"][0]["clips"][0]["tracks"])


class NrclipDuplicateTest(unittest.TestCase):
    def test_duplicate_line_is_exported_once(self) -> None:
        line = make_line([[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]])

        self.assertEqual(track_count([line, line]), track_count([line]))

    def test_reversed_duplicate_line_is_exported_once(self) -> None:
        line = make_line([[139.0, 35.0], [139.001, 35.0], [139.002, 35.0]])
        reversed_line = make_line([[139.002, 35.0], [139.001, 35.0], [139.0, 35.0]])

        self.assertEqual(track_count([line, reversed_line]), track_count([line]))


if __name__ == "__main__":
    unittest.main()
