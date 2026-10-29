# NRClipBuilder

[日本語](README.md)

A railway GIS editor and NIMBY Rails exporter for Windows x64 and Ubuntu 24.04+ Linux x64.

## Running

Ready-to-run packages are available from [GitHub Release Assets](https://github.com/Ikumyon/NRClipBuilder/releases). No Python installation is required. Extract the ZIP or tar.gz, keep the entire directory and launch NRClipBuilder.

The instructions below explain how to run, modify and build the application from source.

Import N05 ZIP, Shapefile and GeoJSON; filter attributes and railway types; select a BBox; draw and edit lines, bridges and tunnels; manage layers, opacity, history and languages; export GeoJSON, Turnout Overpass JSON or NIMBY Rails NRC1 model 226.

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
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean packaging/nrclip_builder.spec
```

The standalone application is generated in `dist/NRClipBuilder/`. Keep this entire directory, including the files alongside the executable.

Packaged UI tests require QtWebEngine. Use `dist/NRClipBuilder/NRClipBuilder.exe --smoke-test` on Windows or `xvfb-run -a dist/NRClipBuilder/NRClipBuilder --smoke-test` on headless Linux.

Source builds use version `0.0.0-dev`. Check it with `python -m nrclip_builder --version`.

## Code structure and persistence

`tests/` verifies versions and release assets. `packaging/` contains distribution settings, the application launcher, build dependencies, and version preparation/verification scripts.

Python owns Qt presentation, application state/services, GIS import with pyshp/pyproj, and OSM networking. The required PyO3 Rust extension owns datasets, filters, clipping, topology, curves, Overpass/NRC1 export and all OS-specific operations. Long tasks run in workers; Rust computation detaches from Python. Immutable snapshots and shared unchanged features avoid repeated whole-dataset transfers.

The React/TypeScript map lives in `src/web/src/`, with CSS for presentation, a Leaflet controller and typed Qt WebChannel commands. Its static bundle is generated in `src/python/nrclip_builder/resources/map/` and included in wheels and desktop packages. Edit the sources and rebuild; do not edit generated assets. Node.js is only needed for development and CI.

The map uses Qt WebChannel and incremental updates. The attribute table fetches pages of properties. Regex uses Rust `regex` syntax; unsupported lookarounds and backreferences produce errors. Shapefiles with PRJ are transformed to WGS84; files without PRJ are assumed WGS84. CPG determines DBF encoding; without CPG, UTF-8 and CP932 are tried.

Settings, datasets, output history and unfinished drawing share one atomic `.userdata/session-v1.json` transaction under the application root: the checkout root during development, or the executable's folder in desktop packages, independent of the working directory. Browser previews also live in `.userdata/`. Rust resolves the location, sets the Windows hidden attribute and replaces files atomically. Failed saves preserve the previous file. Output history is added after a successful export. Previous implementations, settings and recovery formats have no compatibility path; existing user files are neither migrated nor deleted. Invalid new sessions are reported without being overwritten.

## License

MIT. See LICENSE and THIRD_PARTY_NOTICES.md. Portions of the Rust NRC1 and topology implementation are adapted from Alex Sørlie's [Turnout](https://github.com/SuperManifolds/Turnout). Leaflet's license is bundled.
