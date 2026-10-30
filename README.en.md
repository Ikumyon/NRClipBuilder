# NRClipBuilder

[日本語](README.md)

A railway GIS editor and NIMBY Rails exporter for Windows x64 and Ubuntu 24.04+ Linux x64.

## Running

Ready-to-run packages are available from [GitHub Release Assets](https://github.com/Ikumyon/NRClipBuilder/releases). No Python installation is required. Extract the ZIP or tar.gz, keep the entire directory and launch NRClipBuilder.

Linux packages include a Fcitx 4/5 input plugin built against the bundled Qt. The desktop still needs a running Fcitx service and an input engine such as Mozc. If neither `QT_IM_MODULE` nor `QT_IM_MODULES` is set, Rust selects the input method specified by `XMODIFIERS`.

Elevation is requested when the pointer stops over the map. A separate map field displays the coordinates and error details for network errors, HTTP failures, five-second timeouts, missing coverage, and invalid responses, including while selecting a BBox.

On Linux, the application starts with the default graphics configuration. Only if graphics startup crashes or fails to become ready within 30 seconds does it retry with native EGL (`QT_XCB_GL_INTEGRATION=xcb_egl` and `--use-gl=egl`). If that also fails, the final attempt disables GPU acceleration, equivalent to `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" ./NRClipBuilder`. Rust controls the retries, and the selected mode is not saved. Normal exits, failures after startup, and unrelated startup errors do not trigger retries. GBM allocation warnings can still appear while the window and map render successfully through EGL.

The instructions below explain how to run, modify and build the application from source.

Import N05 ZIP, Shapefile and GeoJSON; filter attributes and railway types; select a BBox; draw and edit lines, bridges and tunnels; manage layers, opacity, history and languages; export GeoJSON, Turnout Overpass JSON, NIMBY Rails NRC1 model 226 or PMTiles.

Use Ctrl+Z to undo an edit. While drawing, right-click removes the last vertex; double-click or **Finish Line** commits the draft. Select a line layer or output history and a BBox before exporting. Leaflet is bundled; tiles, OSM and elevation require a network connection.

## Running from source

Download and extract the source using GitHub's **Code → Download ZIP**, or clone the repository. Run the remaining commands from the project root.

```text
git clone https://github.com/Ikumyon/NRClipBuilder.git
cd NRClipBuilder
```

Create a regular Python 3.14 virtual environment with `python -m venv --system-site-packages .venv` and activate it. Install Rust, Node.js 24 and a C/C++ toolchain (Visual Studio Build Tools on Windows, GCC on Linux), then run:

```text
python -m pip install -r packaging/requirements-build.txt
npm --prefix src/web ci
npm --prefix src/web run build
python -m maturin develop --release --locked
python -m nrclip_builder
```

Python dependencies generally allow updates within the same minor version (for example, `PySide6~=6.12.0` allows the 6.12 series). Rust and npm use caret ranges: the same major for versions 1 and above, or the same minor for 0.x dependencies. `Cargo.lock` and `package-lock.json` record resolved versions; update them with `cargo update` and `npm --prefix src/web update`.

File arguments are supported: `python -m nrclip_builder example.geojson`.

## Making changes and building

Restart the application after changing Python code. After changing the map UI, run `npm --prefix src/web run build`. After changing Rust code, run `python -m maturin develop --release --locked` before restarting.

Use the following commands to check your changes and build a standalone application. Build Windows packages on Windows and Linux packages on Linux.

```text
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --all --check
npm --prefix src/web test
npm --prefix src/web run format:check
python -m PyInstaller --noconfirm --clean packaging/nrclip_builder.spec
```

The standalone application is generated in `dist/NRClipBuilder/`. Keep this entire directory, including the files alongside the executable.

UI tests require QtWebEngine. Build the CI-only validation application with `python -m PyInstaller --noconfirm --clean --distpath build/smoke-dist --workpath build/smoke-work packaging/smoke.spec`, then run `build/smoke-dist/NRClipBuilder/NRClipBuilder.exe` on Windows or `xvfb-run -a build/smoke-dist/NRClipBuilder/NRClipBuilder` on headless Linux. Test code, test data and test command-line options are excluded from the distribution application. CI inspects its embedded modules before archiving it.

Source builds use version `0.0.0-dev`. Check it with `python -m nrclip_builder --version`.

## Export

Open **File → Export…** to export all checked data layers into one file, using the current filters and BBox. Background maps and the history layer are excluded. Inputs are captured when the dialog opens. Choose the NRC1, PMTiles, GeoJSON or Turnout JSON tab, enter a destination, and export. Settings are retained across tabs and saved with the last selected format in the session. NRC1 scale and curve settings apply only to NRC1. PMTiles defaults to zoom 0–14 and accepts zooms 0–22.

**Export again…** in the History tab uses the selected entry's saved data and BBox, independently of current filters and checked layers. History is added only after the output file is committed successfully.

### PMTiles in NIMBY Rails

PMTiles provides a background railway map. To export playable tracks, choose NRC1 instead. In NIMBY Rails 1.19 or later, open **Map options → Sources → New source**, add the exported local `.pmtiles` map source, and use a style that displays OpenMapTiles railways. The source layer is `transportation`, with `class=rail` and the railway type in `subclass`. Bridges have `brunnel=bridge`, tunnels have `brunnel=tunnel`, and ground lines omit `brunnel`. Colors and line styles depend on the game style; this application does not generate a style mod.

The output is PMTiles v3 with gzip-compressed MVT, generated without external commands. Maximum zoom preserves geometry apart from quantization; lower zooms are simplified. Missing structure information is treated as ground and is never inferred. Attributes follow the [OpenMapTiles transportation schema](https://github.com/openmaptiles/openmaptiles/blob/master/layers/transportation/transportation.yaml) (schema: CC-BY). Source data licensing and attribution remain applicable.

## Code structure and persistence

`packaging/` contains distribution settings, the application launcher, build dependencies, and version preparation/verification scripts.

Python owns Qt presentation, application state/services, GIS import with pyshp/pyproj, and OSM networking. The required PyO3 Rust extension owns datasets, filters, clipping, topology, curves, Overpass/NRC1 export and all OS-specific operations. Long tasks run in workers; Rust computation detaches from Python. Immutable snapshots and shared unchanged features avoid repeated whole-dataset transfers.

The React/TypeScript map lives in `src/web/src/`, with CSS for presentation, a Leaflet controller and typed Qt WebChannel commands. Its static bundle is generated in `src/python/nrclip_builder/resources/map/` and included in wheels and desktop packages. Edit the sources and rebuild; do not edit generated assets. Node.js is only needed for development and CI.

The map uses Qt WebChannel and incremental updates. The attribute table fetches pages of properties. Regex uses Rust `regex` syntax; unsupported lookarounds and backreferences produce errors. Shapefiles with PRJ are transformed to WGS84; files without PRJ are assumed WGS84. CPG determines DBF encoding; without CPG, UTF-8 and CP932 are tried.

Settings, datasets, output history and unfinished drawing share one atomic `.userdata/session-v1.json` transaction under the application root: the checkout root during development, or the executable's folder in desktop packages, independent of the working directory. Browser previews also live in `.userdata/`. Rust resolves the location, sets the Windows hidden attribute and replaces files atomically. Failed saves preserve the previous file. Output history is added after a successful export. Previous implementations, settings and recovery formats have no compatibility path; existing user files are neither migrated nor deleted. Invalid new sessions are reported without being overwritten.

## License

MIT. See LICENSE and THIRD_PARTY_NOTICES.md. Portions of the Rust NRC1 and topology implementation are adapted from Alex Sørlie's [Turnout](https://github.com/SuperManifolds/Turnout). Leaflet's license is bundled.
