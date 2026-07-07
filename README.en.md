# NRClipBuilder

A map preview, filtering, and exporting tool for GIS data (N05 / SHP / GeoJSON), built with PySide6.
It allows you to filter railway line data drawn on the map by keywords or attributes, and export clipboard data for NIMBY Rails (`.nrclip`), or Overpass-style JSON that can be passed to Turnout (Rust version)'s `import_orm`.

## Features

- **Import Various GIS Formats**:
  - National Land Numerical Information (Japanese N05, etc.) ZIP files
  - Shapefile (`.shp`)
  - GeoJSON (`.geojson` / `.json`)
- **Advanced Filtering**:
  - OR/AND condition extraction based on attribute names, keywords, and exclusion keywords.
  - Option to display/export only line data (LineString, etc.).
- **Interactive Map Preview**:
  - Interactive map preview using QWebEngineView and Leaflet.js.
  - Background switching between GSI Map (Standard/Aerial) and OpenStreetMap (OSM).
- **Multiple Export Formats**:
  - GeoJSON
  - Overpass-style JSON for Turnout
  - **NIMBY Rails Clipboard Format (`.nrclip`)** direct export (zstd compression + automatic wyhash_nrc1 checksum calculation)
- **Multi-language Support (i18n)**:
  - Supports Japanese and English display switching.
  - Language can be switched in real-time from the combo box in the upper-left of the application.
  - By editing `ja.json` and `en.json` in the `localisation` folder at the same level as the executable, you can freely customize UI labels without rebuilding the application.

## Installation

On Windows, run the following batch file:

```bat
install_requirements.bat
```

Or manually install dependencies in your virtual environment:

```bat
python -m pip install -r requirements.txt
```

## Running the Application

```bat
run.bat
```

Or run directly with Python:

```bat
python nrclip_builder.py
```

You can also open a data file directly by passing it as an argument:

```bat
python nrclip_builder.py C:\path\to\N05-20_GML.zip
```

---

## Credits & License

This project is a Python (PySide6) re-implementation and enhancement based on the data structure, checksum algorithm, and serialization logic of [Turnout](https://github.com/SuperManifolds/Turnout), a Rust-based tool developed by Alex Sørlie.

- Original Project: [Turnout](https://github.com/SuperManifolds/Turnout) (Copyright (c) 2025 Alex Sørlie, MIT License)
- This software is released under the MIT License. See the `LICENSE` file for details.
