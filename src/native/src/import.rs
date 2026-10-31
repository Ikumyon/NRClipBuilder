//! Import `OpenRailwayMap` tracks into a Nimby Rails blueprint.
//!
//! Pipeline:
//! 1. Load Overpass JSON → OSM nodes + ways
//! 2. Clip ways to bbox (optional)
//! 3. Merge ways into continuous routes through shared endpoints
//! 4. Simplify routes to control points (direction changes + max spacing)
//! 5. Generate game track nodes with junction topology
//! 6. Serialize to .nrclip

use crate::{dataset::Cancellation, geometry::BBox};
use anyhow::{Context, Result};
use serde::Deserialize;
use std::collections::{HashMap, HashSet};

use crate::geo::{inverse_geodesic, latlon_to_mercator, merc_y_to_lat_rad};
use crate::nrc1::NrclipFile;
use crate::types::{
    Clip, Collection, ModMeta, ModRelFile, Track, TrackKind, TrackKindHorizon, TrackTexture,
};

const MODEL_VERSION: u32 = 226;
const MAX_TRACK_NODES: usize = 50_000;
const ALIGNMENT_THRESHOLD: f64 = 2.5; // ~143° — reject near-reversal continuations
const BRANCH_OFFSET: f64 = 5.0; // meters — nudge branch root away from parent
const EARTH_RADIUS: f64 = 6_378_137.0;

type VanillaTrackData = (Vec<(i32, TrackKind)>, Vec<ModMeta>);
type PipelineResult = (Vec<Track>, Vec<Vec<(usize, f64, f64)>>, RouteData, OsmData);

// Vanilla game track type keys
const TRACK_TYPE_HIGH_SPEED: i32 = 1;
const TRACK_TYPE_TRAM: i32 = 2;
const TRACK_TYPE_MEDIUM: i32 = 3;

// ══════════════════════════════════════════════════════════════════════
// Intermediate data structures
// ══════════════════════════════════════════════════════════════════════

struct OsmData {
    nodes: HashMap<u64, (f64, f64)>,
    ways: Vec<Vec<u64>>,
    node_layer: HashMap<u64, i32>,
    node_track_type: HashMap<u64, i32>,
    node_maxspeed: HashMap<u64, f32>,
    node_railway_type: HashMap<u64, String>,
    way_layers: Vec<i32>,
    way_track_types: Vec<i32>,
}

struct RouteData {
    routes: Vec<Vec<u64>>,
    route_coords: Vec<Vec<(f64, f64)>>,
    junction_nodes: HashSet<u64>,
    junction_owner: HashMap<u64, usize>,
}

struct RouteNodeInfo {
    game_id: i64,
}

// ══════════════════════════════════════════════════════════════════════
// Public API
// ══════════════════════════════════════════════════════════════════════

/// Map OSM way tags to a vanilla game track type.
fn osm_to_track_type(tags: &serde_json::Value) -> i32 {
    let railway = tags.get("railway").and_then(|v| v.as_str()).unwrap_or("");

    if railway == "tram" || railway == "light_rail" {
        return TRACK_TYPE_TRAM;
    }
    if tags.get("highspeed").and_then(|v| v.as_str()) == Some("yes") {
        return TRACK_TYPE_HIGH_SPEED;
    }
    if tags.get("usage").and_then(|v| v.as_str()) == Some("highspeed") {
        return TRACK_TYPE_HIGH_SPEED;
    }
    if parse_maxspeed_tag(tags).is_some_and(|kmh| kmh >= 200.0) {
        return TRACK_TYPE_HIGH_SPEED;
    }

    TRACK_TYPE_MEDIUM
}

/// Extract the best maxspeed value from OSM tags in km/h.
/// Checks `maxspeed`, then falls back to `maxspeed:forward`/`maxspeed:backward`
/// (taking the higher value since Nimby Rails has no directional speed limits).
fn parse_maxspeed_tag(tags: &serde_json::Value) -> Option<f64> {
    // Try plain maxspeed first
    if let Some(s) = tags.get("maxspeed").and_then(|v| v.as_str()) {
        let kmh = parse_maxspeed_kmh(s);
        if kmh > 0.0 {
            return Some(kmh);
        }
    }
    // Fall back to directional tags — use the higher value
    let fwd = tags
        .get("maxspeed:forward")
        .and_then(|v| v.as_str())
        .map_or(0.0, parse_maxspeed_kmh);
    let bwd = tags
        .get("maxspeed:backward")
        .and_then(|v| v.as_str())
        .map_or(0.0, parse_maxspeed_kmh);
    let best = fwd.max(bwd);
    if best > 0.0 { Some(best) } else { None }
}

/// Parse an OSM maxspeed value to km/h. Handles "200", "79 mph", etc.
fn parse_maxspeed_kmh(s: &str) -> f64 {
    let s = s.trim();
    if let Some(mph) = s.strip_suffix("mph") {
        mph.trim().parse::<f64>().unwrap_or(0.0) * 1.60934
    } else if let Some(knots) = s.strip_suffix("knots") {
        knots.trim().parse::<f64>().unwrap_or(0.0) * 1.852
    } else {
        s.parse::<f64>().unwrap_or(0.0)
    }
}

/// Hardcoded vanilla track kind definitions (keys 1–3). These reference the game's
/// built-in textures (`workshop_id=0, path="tracks"`) so they render correctly
/// without needing to read the user's `collections.nrclip`.
#[must_use]
pub fn default_track_kinds() -> Vec<(i32, TrackKind)> {
    let vanilla_tex = || -> ModRelFile {
        ModRelFile {
            workshop_id: 0,
            path: "tracks".to_string(),
            name: String::new(),
        }
    };
    let empty_file = || -> ModRelFile {
        ModRelFile {
            workshop_id: 0,
            path: String::new(),
            name: String::new(),
        }
    };
    let textures = || -> Vec<TrackTexture> {
        (0..6)
            .map(|sc| TrackTexture {
                speed_class: sc,
                files: if sc <= 3 {
                    [vanilla_tex(), vanilla_tex(), vanilla_tex(), vanilla_tex()]
                } else {
                    [empty_file(), vanilla_tex(), empty_file(), empty_file()]
                },
            })
            .collect()
    };

    let make_kind =
        |key: i32, display: &str, internal: &str, max_speeds: [f64; 3]| -> (i32, TrackKind) {
            (
                key,
                TrackKind {
                    display_name: display.to_string(),
                    speed_class_flag: 1,
                    speed_class: key,
                    internal_name: internal.to_string(),
                    secondary_name: format!("{internal}_name"),
                    horizons: [
                        TrackKindHorizon {
                            speed_class: 0,
                            gauge: 97.222_222_222_222_21,
                            height: 5.21,
                            max_speed: max_speeds[0],
                            width_a: 10.0,
                            width_b: 25.0,
                            spacing: 15.0,
                            offset_a: 2.5,
                            offset_b: 2.0,
                            visual_distance: 125_000,
                            flags: [0, 0, 0, 1, 0],
                            textures: textures(),
                        },
                        TrackKindHorizon {
                            speed_class: 0,
                            gauge: 97.222_222_222_222_21,
                            height: 5.21,
                            max_speed: max_speeds[1],
                            width_a: 10.0,
                            width_b: 25.0,
                            spacing: 25.0,
                            offset_a: 2.5,
                            offset_b: 2.0,
                            visual_distance: 125_000,
                            flags: [1, 1, 1, 1, 0],
                            textures: textures(),
                        },
                        TrackKindHorizon {
                            speed_class: 0,
                            gauge: 97.222_222_222_222_21,
                            height: 5.21,
                            max_speed: max_speeds[2],
                            width_a: 10.0,
                            width_b: 25.0,
                            spacing: 15.0,
                            offset_a: 2.5,
                            offset_b: 2.0,
                            visual_distance: 125_000,
                            flags: [0, 0, 0, 0, 0],
                            textures: textures(),
                        },
                    ],
                },
            )
        };

    vec![
        make_kind(1, "waw_track_hs_1", "High speed", [3300.0, 500.0, 4000.0]),
        make_kind(2, "waw_track_tram_1", "Tram", [500.0, 200.0, 700.0]),
        make_kind(3, "waw_track_med_1", "Medium", [1600.0, 500.0, 2200.0]),
    ]
}

/// Public, format-independent conversion options.
#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ExportOptions {
    pub name: String,
    pub bbox: Option<BBox>,
    pub scale_x: f64,
    pub scale_y: f64,
    pub spline_tolerance: f64,
    pub junction_spacing: f64,
    pub max_spacing: f64,
    pub tangent_mode: bool,
    pub curve_r_tolerance: f64,
    pub enable_curve_r_merging: bool,
}
impl Default for ExportOptions {
    fn default() -> Self {
        Self {
            name: "Tracks".into(),
            bbox: None,
            scale_x: 1.,
            scale_y: 1.,
            spline_tolerance: 2.,
            junction_spacing: 30.,
            max_spacing: 2000.,
            tangent_mode: false,
            curve_r_tolerance: 5.,
            enable_curve_r_merging: true,
        }
    }
}
impl ExportOptions {
    pub fn validate(&self) -> Result<()> {
        anyhow::ensure!(
            [
                self.scale_x,
                self.scale_y,
                self.spline_tolerance,
                self.junction_spacing,
                self.max_spacing,
                self.curve_r_tolerance
            ]
            .iter()
            .all(|v| v.is_finite()),
            "Conversion options must be finite"
        );
        anyhow::ensure!(
            self.scale_x > 0.
                && self.scale_y > 0.
                && self.spline_tolerance > 0.
                && self.junction_spacing >= 0.
                && self.max_spacing >= 1.
                && self.curve_r_tolerance >= 0.,
            "Invalid conversion options"
        );
        if let Some(b) = self.bbox {
            crate::geometry::validate_bbox(b)?;
        }
        Ok(())
    }
}
pub fn convert(json: &str, options: &ExportOptions, cancel: &Cancellation) -> Result<Vec<u8>> {
    options.validate()?;
    cancel.check()?;
    let osm = parse_osm_data(json, &[])?;
    anyhow::ensure!(!osm.ways.is_empty(), "No railway lines to export");
    cancel.check()?;
    let routes = merge_ways_into_routes(&osm);
    let projection =
        crate::reduction::Projection::new(routes.route_coords.iter().flatten().copied());
    cancel.check()?;
    let simplified = simplify_routes(&routes, &osm, options, projection, cancel)?;
    cancel.check()?;
    let mut tracks = build_track_nodes(
        &simplified,
        &routes,
        &osm,
        true,
        options.tangent_mode,
        &HashMap::new(),
    );
    attach_branches(&mut tracks, &simplified, &routes);
    close_routes(&mut tracks, &simplified, &routes);
    validate_node_count(tracks.len())?;
    if options.tangent_mode {
        for track in &mut tracks {
            if track.attached_to_id != 0 {
                track.tangential = Some(0);
            }
        }
    }
    cancel.check()?;
    serialize_to_nrclip(
        tracks,
        &options.name,
        default_track_kinds(),
        Vec::new(),
        options,
        projection.center,
    )
}

fn validate_node_count(count: usize) -> Result<()> {
    anyhow::ensure!(
        count > 0 && count <= MAX_TRACK_NODES,
        "Final track node count {count} exceeds the supported range 1..={MAX_TRACK_NODES}"
    );
    Ok(())
}

fn parse_osm_data(json: &str, railway_types: &[String]) -> Result<OsmData> {
    let data: serde_json::Value = serde_json::from_str(json).context("parse JSON")?;
    let elements = data["elements"].as_array().context("no elements")?;

    let mut osm = OsmData {
        nodes: HashMap::new(),
        ways: Vec::new(),
        node_layer: HashMap::new(),
        node_track_type: HashMap::new(),
        node_maxspeed: HashMap::new(),
        node_railway_type: HashMap::new(),
        way_layers: Vec::new(),
        way_track_types: Vec::new(),
    };

    for e in elements {
        match e["type"].as_str() {
            Some("node") => {
                let Some(id) = e["id"].as_u64() else { continue };
                let Some(lat) = e["lat"].as_f64() else {
                    continue;
                };
                let Some(lon) = e["lon"].as_f64() else {
                    continue;
                };
                osm.nodes.insert(id, (lat, lon));
            }
            Some("way") => {
                if !railway_types.is_empty() {
                    let rt = e
                        .get("tags")
                        .and_then(|t| t.get("railway"))
                        .and_then(|r| r.as_str())
                        .unwrap_or("");
                    if !railway_types.iter().any(|t| t == rt) {
                        continue;
                    }
                }
                let Some(node_arr) = e["nodes"].as_array() else {
                    continue;
                };
                let mut nids: Vec<u64> = node_arr
                    .iter()
                    .filter_map(serde_json::Value::as_u64)
                    .collect();
                nids.dedup();
                let tags = e.get("tags").cloned().unwrap_or_default();
                let base_type = tags
                    .get("railway")
                    .and_then(|r| r.as_str())
                    .unwrap_or("rail");
                let service = tags.get("service").and_then(|s| s.as_str());
                let usage = tags.get("usage").and_then(|s| s.as_str());
                // Build a specific type key: "rail:yard", "rail:siding", or just "rail"
                let railway_type = match (service, usage) {
                    (Some(s), _) => format!("{base_type}:{s}"),
                    (None, Some(u)) => format!("{base_type}:{u}"),
                    _ => base_type.to_string(),
                };
                let layer: i32 = tags
                    .get("layer")
                    .and_then(|l| l.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0);
                let tt = osm_to_track_type(&tags);
                let maxspeed_ms = parse_maxspeed_tag(&tags)
                    .map(|kmh| (kmh / 3.6) as f32)
                    .filter(|&v| v > 0.0);

                if nids.len() >= 2 {
                    for &nid in &nids {
                        osm.node_layer
                            .entry(nid)
                            .and_modify(|existing| {
                                if layer.abs() > existing.abs() {
                                    *existing = layer;
                                }
                            })
                            .or_insert(layer);
                        osm.node_track_type.entry(nid).or_insert(tt);
                        osm.node_railway_type
                            .entry(nid)
                            .or_insert_with(|| railway_type.clone());
                        if let Some(ms) = maxspeed_ms {
                            osm.node_maxspeed.entry(nid).or_insert(ms);
                        }
                    }
                    osm.way_layers.push(layer);
                    osm.way_track_types.push(tt);
                    osm.ways.push(nids);
                }
            }
            _ => {}
        }
    }

    let mut seen = HashSet::new();
    osm.ways.retain(|way| {
        if way.iter().any(|id| !osm.nodes.contains_key(id)) {
            return false;
        }
        let reverse: Vec<_> = way.iter().rev().copied().collect();
        seen.insert(if way <= &reverse {
            way.clone()
        } else {
            reverse
        })
    });
    Ok(osm)
}

fn merge_ways_into_routes(osm: &OsmData) -> RouteData {
    // Build node→way index
    let mut node_ways: HashMap<u64, Vec<(usize, usize)>> = HashMap::new();
    for (wi, way) in osm.ways.iter().enumerate() {
        for (pi, &nid) in way.iter().enumerate() {
            node_ways.entry(nid).or_default().push((wi, pi));
        }
    }

    // Identify shared/junction nodes
    let mut shared_nodes: HashSet<u64> = HashSet::new();
    let mut junction_nodes: HashSet<u64> = HashSet::new();
    for (&nid, refs) in &node_ways {
        let n_ways = refs.iter().map(|&(wi, _)| wi).collect::<HashSet<_>>().len();
        if n_ways >= 2 {
            shared_nodes.insert(nid);
        }
        if n_ways >= 3
            || (n_ways == 2
                && refs
                    .iter()
                    .any(|&(wi, pi)| pi > 0 && pi < osm.ways[wi].len() - 1))
        {
            junction_nodes.insert(nid);
        }
    }

    // Merge ways into routes by extending through shared nodes
    let mut way_used = vec![false; osm.ways.len()];
    let mut routes: Vec<Vec<u64>> = Vec::new();

    let mut way_order: Vec<usize> = (0..osm.ways.len()).collect();
    way_order.sort_by(|&a, &b| osm.ways[b].len().cmp(&osm.ways[a].len()));

    for &start_wi in &way_order {
        if way_used[start_wi] {
            continue;
        }
        way_used[start_wi] = true;
        let mut route = osm.ways[start_wi].clone();

        extend_route_forward(
            &mut route,
            &osm.ways,
            &osm.nodes,
            &shared_nodes,
            &node_ways,
            &mut way_used,
        );
        extend_route_backward(
            &mut route,
            &osm.ways,
            &osm.nodes,
            &shared_nodes,
            &node_ways,
            &mut way_used,
        );

        routes.push(route);
    }

    routes.sort_by_key(|r| std::cmp::Reverse(r.len()));

    let route_coords: Vec<Vec<(f64, f64)>> = routes
        .iter()
        .map(|route| {
            route
                .iter()
                .filter_map(|nid| {
                    osm.nodes
                        .get(nid)
                        .map(|&(lat, lon)| latlon_to_mercator(lat, lon))
                })
                .collect()
        })
        .collect();

    // Junction ownership: first (longest) route through each junction owns it
    let mut junction_owner: HashMap<u64, usize> = HashMap::new();
    for (ri, route) in routes.iter().enumerate() {
        for &nid in route {
            if junction_nodes.contains(&nid) {
                junction_owner.entry(nid).or_insert(ri);
            }
        }
    }

    RouteData {
        routes,
        route_coords,
        junction_nodes,
        junction_owner,
    }
}

fn extend_route_forward(
    route: &mut Vec<u64>,
    ways: &[Vec<u64>],
    osm_nodes: &HashMap<u64, (f64, f64)>,
    shared_nodes: &HashSet<u64>,
    node_ways: &HashMap<u64, Vec<(usize, usize)>>,
    way_used: &mut [bool],
) {
    let mut route_set: HashSet<u64> = route.iter().copied().collect();
    loop {
        let last = *route.last().expect("non-empty route");
        if !shared_nodes.contains(&last) {
            break;
        }
        let cur_heading = if route.len() >= 2 {
            let a = &osm_nodes[&route[route.len() - 2]];
            let b = &osm_nodes[&last];
            (b.0 - a.0).atan2(b.1 - a.1)
        } else {
            0.0
        };

        let Some((wi, pi, diff)) =
            find_best_continuation(last, cur_heading, ways, osm_nodes, node_ways, way_used)
        else {
            break;
        };
        if diff > ALIGNMENT_THRESHOLD {
            break;
        }

        let new_nodes: Vec<u64> = if pi == 0 {
            ways[wi][1..].to_vec()
        } else {
            ways[wi][..ways[wi].len() - 1]
                .iter()
                .rev()
                .copied()
                .collect()
        };
        if new_nodes.iter().any(|n| route_set.contains(n)) {
            break;
        }

        way_used[wi] = true;
        for &n in &new_nodes {
            route_set.insert(n);
        }
        route.extend_from_slice(&new_nodes);
    }
}

fn extend_route_backward(
    route: &mut Vec<u64>,
    ways: &[Vec<u64>],
    osm_nodes: &HashMap<u64, (f64, f64)>,
    shared_nodes: &HashSet<u64>,
    node_ways: &HashMap<u64, Vec<(usize, usize)>>,
    way_used: &mut [bool],
) {
    let mut route_set: HashSet<u64> = route.iter().copied().collect();
    loop {
        let first = route[0];
        if !shared_nodes.contains(&first) {
            break;
        }
        let cur_heading = if route.len() >= 2 {
            let a = &osm_nodes[&route[1]];
            let b = &osm_nodes[&first];
            (b.0 - a.0).atan2(b.1 - a.1)
        } else {
            0.0
        };

        let Some((wi, pi, diff)) =
            find_best_continuation(first, cur_heading, ways, osm_nodes, node_ways, way_used)
        else {
            break;
        };
        if diff > ALIGNMENT_THRESHOLD {
            break;
        }

        let new_nodes: Vec<u64> = if pi == ways[wi].len() - 1 {
            ways[wi][..ways[wi].len() - 1].to_vec()
        } else {
            ways[wi][1..].iter().rev().copied().collect()
        };
        if new_nodes.iter().any(|n| route_set.contains(n)) {
            break;
        }

        way_used[wi] = true;
        for &n in &new_nodes {
            route_set.insert(n);
        }
        let mut prefix = new_nodes;
        prefix.push(first);
        prefix.extend_from_slice(&route[1..]);
        *route = prefix;
    }
}

fn find_best_continuation(
    node: u64,
    cur_heading: f64,
    ways: &[Vec<u64>],
    osm_nodes: &HashMap<u64, (f64, f64)>,
    node_ways: &HashMap<u64, Vec<(usize, usize)>>,
    way_used: &[bool],
) -> Option<(usize, usize, f64)> {
    let mut best: Option<(usize, usize, f64)> = None;
    for &(wi, pi) in &node_ways[&node] {
        if way_used[wi] {
            continue;
        }
        let cont_first = if pi == 0 {
            ways[wi].get(1)
        } else {
            ways[wi].len().checked_sub(2).and_then(|i| ways[wi].get(i))
        };
        let Some(&cont_nid) = cont_first else {
            continue;
        };
        let c = &osm_nodes[&cont_nid];
        let b = &osm_nodes[&node];
        let h = (c.0 - b.0).atan2(c.1 - b.1);
        let mut diff = (h - cur_heading).abs();
        if diff > std::f64::consts::PI {
            diff = std::f64::consts::TAU - diff;
        }
        if best.is_none_or(|b| diff < b.2) {
            best = Some((wi, pi, diff));
        }
    }
    best
}

fn simplify_routes(
    rd: &RouteData,
    osm: &OsmData,
    options: &ExportOptions,
    projection: crate::reduction::Projection,
    cancel: &Cancellation,
) -> Result<Vec<Vec<(usize, f64, f64)>>> {
    rd.routes
        .iter()
        .zip(&rd.route_coords)
        .map(|(route, coords)| {
            cancel.check()?;
            let mut protected = vec![false; coords.len()];
            protected[0] = true;
            *protected.last_mut().unwrap() = true;
            for (i, node) in route.iter().enumerate() {
                if rd.junction_nodes.contains(node) {
                    protected[i] = true;
                }
                if i > 0
                    && (osm.node_layer.get(&route[i - 1]) != osm.node_layer.get(node)
                        || osm.node_track_type.get(&route[i - 1]) != osm.node_track_type.get(node)
                        || osm.node_maxspeed.get(&route[i - 1]) != osm.node_maxspeed.get(node))
                {
                    protected[i - 1] = true;
                    protected[i] = true;
                }
            }
            if options.junction_spacing > 0. {
                for reverse in [false, true] {
                    let end = if reverse { route.len() - 1 } else { 0 };
                    if !rd.junction_nodes.contains(&route[end]) {
                        continue;
                    }
                    let origin = projection.ground(coords[end]);
                    let indices: Vec<_> = if reverse {
                        (1..route.len() - 1).rev().collect()
                    } else {
                        (1..route.len() - 1).collect()
                    };
                    for i in indices {
                        let point = projection.ground(coords[i]);
                        if (point.0 - origin.0).hypot(point.1 - origin.1)
                            >= options.junction_spacing
                        {
                            protected[i] = true;
                            break;
                        }
                    }
                }
            }
            crate::reduction::reduce(coords, &protected, projection, options, cancel)
        })
        .collect()
}

fn build_track_nodes(
    simplified: &[Vec<(usize, f64, f64)>],
    rd: &RouteData,
    osm: &OsmData,
    apply_speed_limits: bool,
    tangent_mode: bool,
    type_speed_overrides: &HashMap<String, u32>,
) -> Vec<Track> {
    let mut track_nodes: Vec<Track> = Vec::new();
    let mut node_id_counter: i64 = 100;

    for (ri, simp) in simplified.iter().enumerate() {
        let mut last_layer: i32 = 0;
        let mut last_track_type: i32 = TRACK_TYPE_MEDIUM;
        let mut last_maxspeed: Option<f32> = None;
        let mut last_railway_type = String::new();

        for (si, &(orig_idx, x, y)) in simp.iter().enumerate() {
            let gid = node_id_counter;
            node_id_counter += 100;
            let prev = if si > 0 {
                track_nodes[track_nodes.len() - 1].node_id
            } else {
                0
            };

            let (layer, track_type, max_speed, railway_type) = if orig_idx == usize::MAX {
                (
                    last_layer,
                    last_track_type,
                    last_maxspeed,
                    last_railway_type.clone(),
                )
            } else {
                let osm_nid = rd.routes[ri][orig_idx];
                let l = osm.node_layer.get(&osm_nid).copied().unwrap_or(0);
                let tt = osm
                    .node_track_type
                    .get(&osm_nid)
                    .copied()
                    .unwrap_or(TRACK_TYPE_MEDIUM);
                let ms = osm.node_maxspeed.get(&osm_nid).copied();
                let rt = osm
                    .node_railway_type
                    .get(&osm_nid)
                    .cloned()
                    .unwrap_or_default();
                last_layer = l;
                last_track_type = tt;
                last_maxspeed = ms;
                last_railway_type.clone_from(&rt);
                (l, tt, ms, rt)
            };

            // Speed priority: specific override (rail:yard) > base override (rail) > OSM > none
            let base_type = railway_type.split(':').next().unwrap_or(&railway_type);
            let override_kmh = type_speed_overrides
                .get(&railway_type)
                .or_else(|| type_speed_overrides.get(base_type));
            let speed = if let Some(&kmh) = override_kmh {
                Some((f64::from(kmh) / 3.6) as f32)
            } else if apply_speed_limits {
                max_speed.or(Some(0.0))
            } else {
                Some(0.0)
            };

            track_nodes.push(Track {
                node_id: gid,
                x,
                y,
                layer,
                track_type,
                user_max_speed: speed,
                prev_node: prev,
                tangential: Some(u8::from(tangent_mode)),
                ..Track::default()
            });
            if si > 0 {
                let prev_idx = track_nodes.len() - 2;
                track_nodes[prev_idx].next_node = gid;
            }
        }
    }

    track_nodes
}

fn attach_branches(
    track_nodes: &mut [Track],
    simplified: &[Vec<(usize, f64, f64)>],
    rd: &RouteData,
) {
    // Build route → game node chains and junction game ID map
    let mut route_game_nodes: Vec<Vec<RouteNodeInfo>> = Vec::new();
    let mut junction_game_ids: HashMap<u64, i64> = HashMap::new();
    let mut idx = 0;
    for (ri, simp) in simplified.iter().enumerate() {
        let mut chain = Vec::new();
        for (si, &(orig_idx, _, _)) in simp.iter().enumerate() {
            let gid = track_nodes[idx + si].node_id;
            chain.push(RouteNodeInfo { game_id: gid });
            if orig_idx != usize::MAX {
                let osm_nid = rd.routes[ri][orig_idx];
                if rd.junction_nodes.contains(&osm_nid)
                    && rd.junction_owner.get(&osm_nid) == Some(&ri)
                {
                    junction_game_ids.insert(osm_nid, gid);
                }
            }
        }
        idx += simp.len();
        route_game_nodes.push(chain);
    }

    // Build O(1) node_id → index lookup
    let node_idx: HashMap<i64, usize> = track_nodes
        .iter()
        .enumerate()
        .map(|(i, t)| (t.node_id, i))
        .collect();

    for (ri, simp) in simplified.iter().enumerate() {
        if simp.len() < 2 {
            continue;
        }

        for &is_start in &[true, false] {
            let endpoint_orig_idx = if is_start {
                simp[0].0
            } else {
                simp.last().expect("non-empty simp").0
            };
            // Skip synthetic interpolated nodes — they can't be junctions
            if endpoint_orig_idx == usize::MAX {
                continue;
            }
            let endpoint_osm = rd.routes[ri][endpoint_orig_idx];
            if !rd.junction_nodes.contains(&endpoint_osm) {
                continue;
            }

            let Some(&owner_ri) = rd.junction_owner.get(&endpoint_osm) else {
                continue;
            };
            if owner_ri == ri {
                continue;
            }

            let branch_gid = if is_start {
                route_game_nodes[ri][0].game_id
            } else {
                route_game_nodes[ri]
                    .last()
                    .expect("non-empty chain")
                    .game_id
            };

            let junction_orig = rd.routes[ri][endpoint_orig_idx];

            // Attach to the junction owner's game node directly
            let Some(&parent_game_id) = junction_game_ids.get(&junction_orig) else {
                continue;
            };
            let parent_node_id = parent_game_id;

            // Compute t = 0.5 (center of parent node's shape polyline)
            let t = 0.5;

            // Determine branch direction relative to parent's next node
            let br_idx = node_idx[&branch_gid];
            let pi = node_idx[&parent_node_id];
            let parent_next = track_nodes[pi].next_node;
            let parent_prev = track_nodes[pi].prev_node;
            let neighbor_node = if parent_next != 0 {
                parent_next
            } else if parent_prev != 0 {
                parent_prev
            } else {
                continue;
            };
            let qi = node_idx[&neighbor_node];
            let seg_dx = track_nodes[qi].x - track_nodes[pi].x;
            let seg_dy = track_nodes[qi].y - track_nodes[pi].y;

            let neighbor_gid = if is_start {
                if route_game_nodes[ri].len() > 1 {
                    route_game_nodes[ri][1].game_id
                } else {
                    continue;
                }
            } else {
                let len = route_game_nodes[ri].len();
                if len > 1 {
                    route_game_nodes[ri][len - 2].game_id
                } else {
                    continue;
                }
            };
            let ni = node_idx[&neighbor_gid];
            let br_dx = track_nodes[ni].x - track_nodes[br_idx].x;
            let br_dy = track_nodes[ni].y - track_nodes[br_idx].y;
            let dot = br_dx * seg_dx + br_dy * seg_dy;
            let dir = if dot >= 0.0 { 1 } else { -1 };

            // Nudge branch root away from parent in ground meters.
            // Pipeline works in Mercator where distances are stretched by 1/cos(lat),
            // so scale the offset to achieve the desired ground-meter distance.
            let merc_offset = BRANCH_OFFSET / merc_y_to_lat_rad(track_nodes[br_idx].y).cos();
            let br_len = (br_dx * br_dx + br_dy * br_dy).sqrt().max(1e-10);
            track_nodes[br_idx].x += (br_dx / br_len) * merc_offset;
            track_nodes[br_idx].y += (br_dy / br_len) * merc_offset;

            // Set attachment fields
            track_nodes[br_idx].attached_to_id = parent_node_id;
            track_nodes[br_idx].attached_to_t = t;
            track_nodes[br_idx].attached_to_direction = Some(dir);

            let par_idx = node_idx[&parent_node_id];
            track_nodes[par_idx].attached_by.push(branch_gid);
        }
    }
}

fn close_routes(
    tracks: &mut Vec<Track>,
    simplified: &[Vec<(usize, f64, f64)>],
    routes: &RouteData,
) {
    let mut replacements = HashMap::new();
    let mut offset = 0;
    for (route, simp) in routes.routes.iter().zip(simplified) {
        if route.first() == route.last() && simp.len() > 2 {
            let last = offset + simp.len() - 1;
            replacements.insert(tracks[last].node_id, tracks[offset].node_id);
            tracks[offset].prev_node = tracks[last].prev_node;
            let attached = tracks[last].attached_by.clone();
            tracks[offset].attached_by.extend(attached);
        }
        offset += simp.len();
    }
    tracks.retain(|t| !replacements.contains_key(&t.node_id));
    for track in tracks {
        track.prev_node = *replacements
            .get(&track.prev_node)
            .unwrap_or(&track.prev_node);
        track.next_node = *replacements
            .get(&track.next_node)
            .unwrap_or(&track.next_node);
        track.attached_to_id = *replacements
            .get(&track.attached_to_id)
            .unwrap_or(&track.attached_to_id);
        for id in &mut track.attached_by {
            *id = *replacements.get(id).unwrap_or(id);
        }
        track.attached_by.sort_unstable();
        track.attached_by.dedup();
    }
}

fn serialize_to_nrclip(
    mut track_nodes: Vec<Track>,
    name: &str,
    track_kinds: Vec<(i32, TrackKind)>,
    mod_metas: Vec<ModMeta>,
    options: &ExportOptions,
    center: (f64, f64),
) -> Result<Vec<u8>> {
    let (cx, cy) = center;
    let center_lat = merc_y_to_lat_rad(cy);
    let center_lon = cx / EARTH_RADIUS;
    for t in &mut track_nodes {
        let node_lat = merc_y_to_lat_rad(t.y);
        let node_lon = t.x / EARTH_RADIUS;

        // Compute inverse geodesic: the (dx, dy) ground-meter offset that the
        // game's spherical destination formula (RVA 0xB9300) will reconstruct
        // back to this node's exact lat/lon.
        let (dx, dy) = inverse_geodesic(center_lat, center_lon, node_lat, node_lon);
        t.x = dx * options.scale_x;
        t.y = dy * options.scale_y;
    }

    let name_hash = name.bytes().fold(0x0012_3456_7890_u64, |h, b| {
        h.wrapping_mul(31).wrapping_add(u64::from(b))
    });
    let file = NrclipFile {
        version: MODEL_VERSION,
        collections: vec![Collection {
            id_a: name_hash,
            id_b: name_hash.wrapping_mul(7),
            name: name.to_string(),
            clips: vec![Clip {
                guid: name.to_string(),
                clip_id: name_hash.wrapping_mul(13),
                center_x: cx,
                center_y: cy,
                tracks: track_nodes,
                track_kinds,
                mod_metas,
                ..Clip::default()
            }],
            ..Collection::default()
        }],
    };

    file.to_bytes()
}

// ══════════════════════════════════════════════════════════════════════
// Geometry helpers
// ══════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod conversion_tests {
    use super::*;
    use crate::geometry::{Feature, Geometry};
    use serde_json::json;

    fn feature(id: &str, coordinates: Vec<Vec<f64>>, properties: serde_json::Value) -> Feature {
        Feature {
            kind: "Feature".into(),
            id: id.into(),
            geometry: Geometry::LineString { coordinates },
            properties: properties.as_object().unwrap().clone(),
        }
    }
    fn convert_features(features: &[Feature], options: ExportOptions) -> NrclipFile {
        let cancel = Cancellation::default();
        let json = crate::export::overpass(features, &cancel).unwrap();
        NrclipFile::from_bytes(&convert(&json, &options, &cancel).unwrap()).unwrap()
    }
    #[test]
    fn structure_and_n05_tags_survive_the_complete_output_pipeline() {
        for layer in ["", "invalid"] {
            let file = convert_features(
                &[feature(
                    "a",
                    vec![vec![139., 35.], vec![139.002, 35.]],
                    json!({"bridge":true,"layer":layer,"name":"","N05_002":"東北新幹線"}),
                )],
                ExportOptions::default(),
            );
            let tracks = &file.collections[0].clips[0].tracks;
            assert!(
                tracks
                    .iter()
                    .all(|track| track.layer == 1 && track.track_type == 1)
            );
        }
        let file = convert_features(
            &[feature(
                "numeric",
                vec![vec![139., 35.], vec![139.002, 35.]],
                json!({"layer": 1.0, "tunnel": "yes"}),
            )],
            ExportOptions::default(),
        );
        assert!(
            file.collections[0].clips[0]
                .tracks
                .iter()
                .all(|track| track.layer == 1)
        );
    }
    #[test]
    fn zero_junction_spacing_does_not_force_extra_nodes_and_attachment_is_preserved() {
        let features = [
            feature(
                "main",
                vec![vec![139., 35.], vec![139.001, 35.], vec![139.002, 35.]],
                json!({}),
            ),
            feature(
                "branch",
                vec![
                    vec![139.001, 35.],
                    vec![139.001, 35.001],
                    vec![139.001, 35.002],
                ],
                json!({}),
            ),
        ];
        let file = convert_features(
            &features,
            ExportOptions {
                junction_spacing: 0.,
                tangent_mode: true,
                ..Default::default()
            },
        );
        let tracks = &file.collections[0].clips[0].tracks;
        assert_eq!(tracks.len(), 5);
        let branch = tracks
            .iter()
            .find(|track| track.attached_to_id != 0)
            .unwrap();
        assert_eq!(branch.tangential, Some(0));
        assert!(
            tracks
                .iter()
                .any(|track| track.node_id == branch.attached_to_id
                    && track.attached_by.contains(&branch.node_id))
        );
    }
    #[test]
    fn closed_routes_have_no_dangling_links_and_scale_and_speed_are_kept() {
        let feature = feature(
            "loop",
            vec![
                vec![139., 35.],
                vec![139.001, 35.],
                vec![139.001, 35.001],
                vec![139., 35.001],
                vec![139., 35.],
            ],
            json!({"railway":"tram","maxspeed":"72"}),
        );
        let file = convert_features(std::slice::from_ref(&feature), ExportOptions::default());
        let scaled = convert_features(
            &[feature],
            ExportOptions {
                scale_x: 2.,
                scale_y: 3.,
                ..Default::default()
            },
        );
        let a = &file.collections[0].clips[0].tracks;
        let b = &scaled.collections[0].clips[0].tracks;
        assert_eq!(a.len(), b.len());
        for (before, after) in a.iter().zip(b) {
            assert!(a.iter().any(|node| node.node_id == before.next_node));
            assert!(a.iter().any(|node| node.node_id == before.prev_node));
            assert_eq!(before.track_type, 2);
            assert_eq!(before.user_max_speed, Some(20.));
            assert!(
                (before.x * 2. - after.x).abs() < 1e-6 && (before.y * 3. - after.y).abs() < 1e-6
            );
        }
    }
    #[test]
    fn final_node_limit_includes_the_actual_count() {
        assert!(validate_node_count(50_000).is_ok());
        let message = validate_node_count(50_001).unwrap_err().to_string();
        assert!(message.contains("50001") && message.contains("50000"));
    }
}
