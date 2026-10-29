# NRClipBuilder

A railway GIS editor and NIMBY Rails exporter for Windows x64 and Ubuntu 24.04+ Linux x64.

## Running

Download `NRClipBuilder-windows-x64.zip` or `NRClipBuilder-linux-x64.tar.gz` from GitHub Release Assets, extract it and launch NRClipBuilder. No Python installation is required. Keep the entire extracted directory: distribution uses PyInstaller's onedir format.

Import N05 ZIP, Shapefile and GeoJSON; filter attributes and railway types; select a BBox; draw and edit lines, bridges and tunnels; manage layers, opacity, history and languages; export GeoJSON, Turnout Overpass JSON or NIMBY Rails NRC1 model 226.

Use Ctrl+Z to undo an edit. While drawing, right-click removes the last vertex; double-click or **Finish Line** commits the draft. Select a line layer or output history and a BBox before exporting. Leaflet is bundled; tiles, OSM and elevation require a network connection.

## Development

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

```text
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
cargo fmt --all --check
npm --prefix src/web test
npm --prefix src/web run format:check
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean packaging/nrclip_builder.spec
```

Packaged UI tests require QtWebEngine. Use `dist/NRClipBuilder/NRClipBuilder.exe --smoke-test` on Windows or `xvfb-run -a dist/NRClipBuilder/NRClipBuilder --smoke-test` on headless Linux. CI builds on each destination OS and checks packaged startup on Windows and Linux X11/Wayland.

## Versions and releases

The application's version comes from `[package].version` in `src/native/Cargo.toml` and is embedded in the Rust extension at compile time. Development builds use `0.0.0-dev` (`0.0.0.dev0` in Python package metadata). The version is available through `python -m nrclip_builder --version`, the window title, Qt application metadata and Windows EXE properties, without Git or network access after distribution.

1. Create and push a tag such as `v1.2.3` on the intended commit.
2. Select that tag on GitHub's Releases page and manually publish a Release. For prereleases use `v1.2.3-alpha.1`, `v1.2.3-beta.1` or `v1.2.3-rc.1`, and select the prerelease option on GitHub.
3. The `release: published` event starts Actions, which checks out the tag, sets its version without the leading `v` in the build checkout, and checks/builds both operating systems. No version changes are committed and dependencies are not updated.
4. After both builds succeed, Actions attaches `NRClipBuilder-windows-x64.zip` and `NRClipBuilder-linux-x64.tar.gz` to that Release's Assets and verifies both files and their sizes. Archive names do not include the version.

Pushes and pull requests only check, build and smoke-test development packages; they do not upload artifacts. Pushing a tag alone does not publish release packages. Assets are absent while the release build runs. Failed runs can be retried; same-name assets are replaced while the release description and publication status are preserved.

## Architecture and persistence

`tests/` verifies versions and release assets. `packaging/` contains distribution settings, the application launcher, build dependencies, and version preparation/verification scripts.

Python owns Qt presentation, application state/services, GIS import with pyshp/pyproj, and OSM networking. The required PyO3 Rust extension owns datasets, filters, clipping, topology, curves, Overpass/NRC1 export and all OS-specific operations. Long tasks run in workers; Rust computation detaches from Python. Immutable snapshots and shared unchanged features avoid repeated whole-dataset transfers.

The React/TypeScript map lives in `src/web/src/`, with CSS for presentation, a Leaflet controller and typed Qt WebChannel commands. Its static bundle is generated in `src/python/nrclip_builder/resources/map/` and included in wheels and desktop packages. Edit the sources and rebuild; do not edit generated assets. Node.js is only needed for development and CI.

The map uses Qt WebChannel and incremental updates. The attribute table fetches pages of properties. Regex uses Rust `regex` syntax; unsupported lookarounds and backreferences produce errors. Shapefiles with PRJ are transformed to WGS84; files without PRJ are assumed WGS84. CPG determines DBF encoding; without CPG, UTF-8 and CP932 are tried.

Settings, datasets, output history and unfinished drawing share one atomic `.userdata/session-v1.json` transaction under the application root: the checkout root during development, or the executable's folder in desktop packages, independent of the working directory. Browser previews also live in `.userdata/`. Rust resolves the location, sets the Windows hidden attribute and replaces files atomically. Failed saves preserve the previous file. Output history is added after a successful export. Previous implementations, settings and recovery formats have no compatibility path; existing user files are neither migrated nor deleted. Invalid new sessions are reported without being overwritten.

## License

MIT. See LICENSE and THIRD_PARTY_NOTICES.md. Portions of the Rust NRC1 and topology implementation are adapted from Alex Sørlie's [Turnout](https://github.com/SuperManifolds/Turnout). Leaflet's license is bundled.
