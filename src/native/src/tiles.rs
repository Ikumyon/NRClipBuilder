//! Sparse, buffered Web Mercator line tiling. No external executable is required.
use crate::{
    dataset::{Cancellation, text},
    geometry::{Feature, Position, clip_line},
};
use anyhow::{Result, ensure};
use geo::{LineString, Simplify};
use mvt::{GeomEncoder, GeomType, Tile};
use pmtiles::{PmTilesWriter, TileCoord, TileId, TileType};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{collections::BTreeMap, io::Cursor};

const EXTENT: f64 = 4096.;
const BUFFER: f64 = 64.;
const MAX_LAT: f64 = 85.051_128_779_806_6;

#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    pub name: String,
    pub min_zoom: u8,
    pub max_zoom: u8,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            name: "Tracks".into(),
            min_zoom: 0,
            max_zoom: 14,
        }
    }
}

struct Piece {
    feature: usize,
    lines: Vec<Vec<Position>>,
}

fn brunnel(feature: &Feature) -> Option<&'static str> {
    let props = &feature.properties;
    let layer = props
        .get("layer")
        .and_then(|v| text(v).parse::<f64>().ok())
        .unwrap_or(0.);
    let yes = |key: &str| props.get(key).is_some_and(|v| v == "yes");
    if yes("tunnel") || layer < 0. {
        Some("tunnel")
    } else if yes("bridge") || layer > 0. {
        Some("bridge")
    } else {
        None
    }
}

fn project(p: &[f64]) -> Position {
    let lat = p[1].clamp(-MAX_LAT, MAX_LAT).to_radians();
    vec![
        (p[0] + 180.) / 360.,
        (1. - lat.tan().asinh() / std::f64::consts::PI) / 2.,
    ]
}

fn encode(
    coord: TileCoord,
    pieces: &[Piece],
    features: &[Feature],
    simplify: bool,
    cancel: &Cancellation,
) -> Result<Option<Vec<u8>>> {
    let mut tile = Tile::new(EXTENT as u32);
    let mut layer = tile.create_layer("transportation");
    let scale = EXTENT * 2_f64.powi(i32::from(coord.z()));
    for piece in pieces {
        cancel.check()?;
        let mut encoder = GeomEncoder::new(GeomType::Linestring);
        let mut valid = false;
        for line in &piece.lines {
            cancel.check()?;
            let local: LineString = line
                .iter()
                .map(|p| {
                    (
                        p[0] * scale - f64::from(coord.x()) * EXTENT,
                        p[1] * scale - f64::from(coord.y()) * EXTENT,
                    )
                })
                .collect();
            let local = if simplify { local.simplify(1.) } else { local };
            let mut points: Vec<_> = local
                .0
                .into_iter()
                .map(|p| (p.x.round() as i32, p.y.round() as i32))
                .collect();
            points.dedup();
            if points.len() < 2 {
                continue;
            }
            for (x, y) in points {
                encoder.add_point(x, y)?;
            }
            encoder.complete_geom()?;
            valid = true;
        }
        if !valid {
            continue;
        }
        let source = &features[piece.feature];
        let mut feature = layer.into_feature(encoder.encode()?);
        feature.set_id(piece.feature as u64 + 1);
        feature.add_tag_string("class", "rail");
        feature.add_tag_string(
            "subclass",
            source
                .properties
                .get("railway")
                .map(text)
                .filter(|s| !s.is_empty())
                .as_deref()
                .unwrap_or("rail"),
        );
        feature.add_tag_string("id", &source.id);
        for key in ["source_id", "source_layer"] {
            if let Some(v) = source.properties.get(key) {
                feature.add_tag_string(key, &text(v));
            }
        }
        for (key, candidates) in [
            ("name", &["name", "N05_002", "N05_011", "路線名"][..]),
            ("operator", &["operator", "N05_003"][..]),
        ] {
            if let Some(v) = candidates
                .iter()
                .filter_map(|k| source.properties.get(*k))
                .find(|v| !text(v).is_empty())
            {
                feature.add_tag_string(key, &text(v));
            }
        }
        if let Some(value) = brunnel(source) {
            feature.add_tag_string("brunnel", value);
        }
        if let Some(value) = source
            .properties
            .get("layer")
            .and_then(|v| text(v).parse::<i64>().ok())
        {
            feature.add_tag_int("layer", value);
        }
        layer = feature.into_layer();
    }
    if layer.num_features() == 0 {
        return Ok(None);
    }
    tile.add_layer(layer)?;
    Ok(Some(tile.to_bytes()?))
}

pub fn convert(features: &[Feature], options: &Options, cancel: &Cancellation) -> Result<Vec<u8>> {
    ensure!(
        options.min_zoom <= options.max_zoom && options.max_zoom <= 22,
        "Zoom must satisfy 0 <= minimum <= maximum <= 22"
    );
    cancel.check()?;
    let mut bounds = [180_f64, MAX_LAT, -180_f64, -MAX_LAT];
    let mut pieces = Vec::new();
    for (index, feature) in features.iter().enumerate() {
        cancel.check()?;
        let mut lines = Vec::new();
        feature.geometry.lines(&mut lines);
        let mut projected = Vec::new();
        for line in lines {
            cancel.check()?;
            for clipped in clip_line(line, [-180., -MAX_LAT, 180., MAX_LAT]) {
                let mut output = Vec::new();
                for (i, p) in clipped.iter().enumerate() {
                    if i % 4096 == 0 {
                        cancel.check()?;
                    }
                    bounds[0] = bounds[0].min(p[0]);
                    bounds[1] = bounds[1].min(p[1]);
                    bounds[2] = bounds[2].max(p[0]);
                    bounds[3] = bounds[3].max(p[1]);
                    output.push(project(p));
                }
                projected.push(output);
            }
        }
        if !projected.is_empty() {
            pieces.push(Piece {
                feature: index,
                lines: projected,
            });
        }
    }
    ensure!(
        !pieces.is_empty(),
        "No lines within Web Mercator latitude limits"
    );
    let metadata: Value = json!({"name": options.name, "format":"pbf", "bounds":bounds,
        "minzoom": options.min_zoom, "maxzoom": options.max_zoom,
        "attribution":"Schema: OpenMapTiles (CC-BY)",
        "vector_layers":[{"id":"transportation", "minzoom":options.min_zoom, "maxzoom":options.max_zoom,
        "fields":{"class":"String","subclass":"String","brunnel":"String","layer":"Number","id":"String","source_id":"String","source_layer":"String","name":"String","operator":"String"}}]});
    let mut output = Cursor::new(Vec::new());
    let mut writer = PmTilesWriter::new(TileType::Mvt)
        .min_zoom(options.min_zoom)
        .max_zoom(options.max_zoom)
        .bounds(bounds[0], bounds[1], bounds[2], bounds[3])
        .center((bounds[0] + bounds[2]) / 2., (bounds[1] + bounds[3]) / 2.)
        .center_zoom(options.max_zoom)
        .metadata(metadata.to_string())
        .create(&mut output)?;
    let root = TileCoord::new(0, 0, 0)?;
    let mut current = BTreeMap::from([(TileId::from(root), (root, pieces))]);
    let mut count = 0;
    for zoom in 0..=options.max_zoom {
        let mut next = BTreeMap::new();
        for (_, (coord, pieces)) in current {
            cancel.check()?;
            if zoom >= options.min_zoom
                && let Some(data) =
                    encode(coord, &pieces, features, zoom < options.max_zoom, cancel)?
            {
                writer.add_tile(coord, &data)?;
                count += 1;
            }
            if zoom == options.max_zoom {
                continue;
            }
            let n = 2_f64.powi(i32::from(zoom + 1));
            let buffer = BUFFER / EXTENT / n;
            for dx in 0..2 {
                for dy in 0..2 {
                    let child = TileCoord::new(zoom + 1, coord.x() * 2 + dx, coord.y() * 2 + dy)?;
                    let bbox = [
                        f64::from(child.x()) / n - buffer,
                        f64::from(child.y()) / n - buffer,
                        f64::from(child.x() + 1) / n + buffer,
                        f64::from(child.y() + 1) / n + buffer,
                    ];
                    let mut clipped = Vec::new();
                    for piece in &pieces {
                        cancel.check()?;
                        let mut lines = Vec::new();
                        for line in &piece.lines {
                            cancel.check()?;
                            lines.extend(clip_line(line, bbox));
                        }
                        if !lines.is_empty() {
                            clipped.push(Piece {
                                feature: piece.feature,
                                lines,
                            });
                        }
                    }
                    if !clipped.is_empty() {
                        next.insert(TileId::from(child), (child, clipped));
                    }
                }
            }
        }
        current = next;
    }
    ensure!(
        count > 0,
        "No lines survive quantization in the selected zoom range"
    );
    cancel.check()?;
    writer.finalize()?;
    cancel.check()?;
    Ok(output.into_inner())
}
