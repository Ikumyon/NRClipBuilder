use crate::{
    dataset::{Cancellation, SelectionData, text},
    geometry::{BBox, Feature, Geometry},
};
use anyhow::Result;
use serde_json::{Map, Value, json};
use std::collections::HashMap;

pub fn line_features(
    selection: &SelectionData,
    bbox: BBox,
    cancel: &Cancellation,
) -> Result<Vec<Feature>> {
    let mut result = Vec::new();
    for mut feature in selection.features(Some(bbox), cancel)? {
        cancel.check()?;
        let mut lines = Vec::new();
        feature.geometry.lines(&mut lines);
        let coordinates: Vec<_> = lines
            .into_iter()
            .filter(|line| line.windows(2).any(|p| p[0][..2] != p[1][..2]))
            .cloned()
            .collect();
        if !coordinates.is_empty() {
            feature.geometry = if coordinates.len() == 1 {
                Geometry::LineString {
                    coordinates: coordinates.into_iter().next().unwrap(),
                }
            } else {
                Geometry::MultiLineString { coordinates }
            };
            result.push(feature);
        }
    }
    Ok(result)
}

pub fn convert(
    format: &str,
    options: &str,
    features: &[Feature],
    cancel: &Cancellation,
) -> Result<Vec<u8>> {
    cancel.check()?;
    match format {
        "geojson" => Ok(serde_json::to_vec(
            &json!({"type":"FeatureCollection","features":features}),
        )?),
        "overpass" => Ok(overpass(features, cancel)?.into_bytes()),
        "nrclip" => {
            let opts: crate::import::ExportOptions = serde_json::from_str(options)?;
            crate::import::convert(&overpass(features, cancel)?, &opts, cancel)
        }
        "pmtiles" => crate::tiles::convert(features, &serde_json::from_str(options)?, cancel),
        _ => anyhow::bail!("Unknown export format"),
    }
}

pub fn overpass(features: &[Feature], cancel: &Cancellation) -> Result<String> {
    let mut ids: HashMap<(i64, i64), u64> = HashMap::new();
    let mut nodes = Vec::new();
    let mut ways = Vec::new();
    for feature in features {
        cancel.check()?;
        let props = &feature.properties;
        let mut tags: Map<String, Value> = props
            .iter()
            .filter(|(k, _)| !k.starts_with('_'))
            .map(|(k, v)| (k.clone(), Value::String(text(v))))
            .collect();
        if tags
            .get("railway")
            .is_none_or(|v| text(v).trim().is_empty())
        {
            tags.insert("railway".into(), json!("rail"));
        }
        if tags.get("name").is_none_or(|v| text(v).trim().is_empty()) {
            for key in ["N05_002", "N05_011", "路線名"] {
                if let Some(v) = props.get(key).filter(|v| !text(v).trim().is_empty()) {
                    tags.insert("name".into(), json!(text(v)));
                    break;
                }
            }
        }
        if let Some(v) = props.get("N05_003") {
            tags.entry("operator").or_insert(json!(text(v)));
        }
        let layer = tags
            .get("layer")
            .and_then(|v| text(v).trim().parse::<f64>().ok())
            .filter(|value| {
                value.is_finite()
                    && value.fract() == 0.
                    && *value >= f64::from(i32::MIN)
                    && *value <= f64::from(i32::MAX)
            });
        if let Some(layer) = layer {
            tags.insert("layer".into(), json!((layer as i32).to_string()));
        } else {
            let truthy = |key: &str| {
                props
                    .get(key)
                    .is_some_and(|v| matches!(text(v).as_str(), "yes" | "true" | "1"))
            };
            tags.insert(
                "layer".into(),
                json!(if truthy("tunnel") {
                    "-1"
                } else if truthy("bridge") {
                    "1"
                } else {
                    "0"
                }),
            );
        }
        if props.get("N05_001").is_some_and(|v| text(v) == "2")
            || ["name", "N05_002", "N05_011"]
                .iter()
                .any(|key| props.get(*key).is_some_and(|v| text(v).contains("新幹線")))
        {
            tags.insert("highspeed".into(), json!("yes"));
        }
        let mut lines = Vec::new();
        feature.geometry.lines(&mut lines);
        for line in lines {
            let mut way = Vec::new();
            for p in line {
                let key = ((p[0] * 1e7).round() as i64, (p[1] * 1e7).round() as i64);
                let next = ids.len() as u64 + 1;
                let id = *ids.entry(key).or_insert_with(|| {
                    nodes.push(json!({"type":"node","id":next,"lon":p[0],"lat":p[1]}));
                    next
                });
                if way.last() != Some(&id) {
                    way.push(id);
                }
            }
            if way.len() >= 2 {
                ways.push(json!({"type":"way","id":10_000_000+ways.len(),"nodes":way,"tags":tags}));
            }
        }
    }
    nodes.extend(ways);
    Ok(serde_json::to_string(
        &json!({"version":0.6,"generator":"NRClipBuilder","elements":nodes}),
    )?)
}
