# Third-party notices

Linux distributions include the Fcitx 4/5 Qt input plugin from fcitx5-qt 5.1.16
(BSD-3-Clause and LGPL-2.1-or-later), built against the bundled Qt version.
The unmodified source archive and license texts are included in
`third_party/fcitx5-qt/`. Source revision:
https://github.com/fcitx/fcitx5-qt/tree/115750044f3d0db16ca5859808c7da830fd1ff52

`src/native/src/{geo,hobby,import,nrc1,wire,wyhash_nrc1}.rs` and `src/native/src/types/`
contain code adapted from Turnout (https://github.com/SuperManifolds/Turnout).
The test-only independent Python NRC1 decoder was retained from this project's
previous implementation. It is never included in the application.

MIT License

Copyright (c) 2025 Alex Sørlie

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

The React/React DOM MIT licenses and Leaflet BSD-2-Clause license are bundled
with the built map at `nrclip_builder/resources/map/LICENSES.txt`.

PMTiles output uses the `pmtiles` and `mvt` Rust crates (MIT OR Apache-2.0).
The transportation attributes follow the OpenMapTiles schema (CC-BY):
https://github.com/openmaptiles/openmaptiles
https://openmaptiles.org/docs/schema/
The PMTiles metadata includes schema attribution. This does not replace the
licensing and attribution requirements of the user's source datasets.
