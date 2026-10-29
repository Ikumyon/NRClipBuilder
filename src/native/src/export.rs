use crate::{
    dataset::{Cancellation, text},
    geometry::Feature,
};
use anyhow::Result;
use serde_json::{Map, Value, json};
use std::collections::HashMap;

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
        tags.entry("railway").or_insert(json!("rail"));
        if !tags.contains_key("name") {
            for key in ["N05_002", "N05_011", "路線名"] {
                if let Some(v) = props.get(key) {
                    tags.insert("name".into(), json!(text(v)));
                    break;
                }
            }
        }
        if let Some(v) = props.get("N05_003") {
            tags.entry("operator").or_insert(json!(text(v)));
        }
        if !tags.contains_key("layer") {
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
            || tags.get("name").is_some_and(|v| text(v).contains("新幹線"))
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
