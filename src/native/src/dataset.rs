use crate::geometry::{BBox, Feature, overlaps, validate_bbox};
use anyhow::{Context, Result, ensure};
use regex::{Regex, RegexBuilder};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{BTreeSet, HashMap, HashSet},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

#[derive(Clone, Default)]
pub struct Cancellation(pub Arc<AtomicBool>);
impl Cancellation {
    pub fn check(&self) -> Result<()> {
        ensure!(!self.0.load(Ordering::Relaxed), "cancelled");
        Ok(())
    }
}

pub struct Entry {
    pub feature: Feature,
    pub bounds: Option<BBox>,
    pub search: HashMap<String, String>,
    pub all_text: String,
}
pub struct Dataset {
    pub entries: Vec<Arc<Entry>>,
    pub fields: Vec<String>,
}

#[derive(Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Filter {
    pub keywords: Vec<String>,
    pub excludes: Vec<String>,
    pub fields: Vec<String>,
    pub regex: bool,
    pub match_all: bool,
    pub lines_only: bool,
    pub railway_types: Option<Vec<String>>,
    pub bbox: Option<BBox>,
}

enum Pattern {
    Literal(String),
    Regex(Regex),
}
impl Pattern {
    fn compile(s: &str, regex: bool) -> Result<Self> {
        if regex {
            Ok(Self::Regex(
                RegexBuilder::new(s)
                    .case_insensitive(true)
                    .build()
                    .context("Invalid regular expression")?,
            ))
        } else {
            Ok(Self::Literal(s.to_lowercase()))
        }
    }
    fn matches(&self, s: &str) -> bool {
        match self {
            Self::Literal(p) => s.contains(p),
            Self::Regex(p) => p.is_match(s),
        }
    }
}

pub fn text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        _ => v.to_string(),
    }
}

impl Dataset {
    pub fn parse(raw: &str, cancel: &Cancellation) -> Result<Self> {
        let features: Vec<Feature> = serde_json::from_str(raw)?;
        let mut ids = HashSet::new();
        let mut fields = BTreeSet::new();
        let mut entries = Vec::with_capacity(features.len());
        for feature in features {
            cancel.check()?;
            ensure!(
                feature.kind == "Feature"
                    && !feature.id.is_empty()
                    && ids.insert(feature.id.clone()),
                "Features must have unique nonempty string IDs"
            );
            feature.geometry.validate()?;
            let bounds = feature.geometry.bounds();
            let search: HashMap<String, String> = feature
                .properties
                .iter()
                .map(|(k, v)| (k.clone(), text(v).to_lowercase()))
                .collect();
            fields.extend(feature.properties.keys().cloned());
            let all_text = feature
                .properties
                .values()
                .map(|v| text(v).to_lowercase())
                .collect::<Vec<_>>()
                .join("\n");
            entries.push(Arc::new(Entry {
                feature,
                bounds,
                search,
                all_text,
            }));
        }
        Ok(Self {
            entries,
            fields: fields.into_iter().collect(),
        })
    }

    pub fn select(&self, filter: &Filter, cancel: &Cancellation) -> Result<Vec<usize>> {
        if let Some(b) = filter.bbox {
            validate_bbox(b)?;
        }
        let include: Vec<_> = filter
            .keywords
            .iter()
            .map(|s| Pattern::compile(s, filter.regex))
            .collect::<Result<_>>()?;
        let exclude: Vec<_> = filter
            .excludes
            .iter()
            .map(|s| Pattern::compile(s, filter.regex))
            .collect::<Result<_>>()?;
        let mut selected = Vec::new();
        for (i, e) in self.entries.iter().enumerate() {
            cancel.check()?;
            if filter.lines_only && !e.feature.geometry.is_line() {
                continue;
            }
            if let Some(b) = filter.bbox
                && (!e.bounds.is_some_and(|bounds| overlaps(bounds, b))
                    || e.feature.geometry.clip(b).is_none())
            {
                continue;
            }
            if let Some(types) = &filter.railway_types
                && let Some(railway) = e.search.get("railway")
                && !types.contains(railway)
            {
                continue;
            }
            let field_text;
            let haystack = if filter.fields.is_empty() || filter.fields.iter().any(|f| f == "*") {
                &e.all_text
            } else {
                field_text = filter
                    .fields
                    .iter()
                    .filter_map(|k| e.search.get(k))
                    .cloned()
                    .collect::<Vec<_>>()
                    .join("\n");
                &field_text
            };
            if exclude.iter().any(|p| p.matches(haystack)) {
                continue;
            }
            if !include.is_empty()
                && if filter.match_all {
                    !include.iter().all(|p| p.matches(haystack))
                } else {
                    !include.iter().any(|p| p.matches(haystack))
                }
            {
                continue;
            }
            selected.push(i);
        }
        Ok(selected)
    }

    pub fn json(&self) -> String {
        serde_json::to_string(&self.entries.iter().map(|e| &e.feature).collect::<Vec<_>>())
            .expect("finite feature JSON")
    }

    pub fn edited(&self, raw: &str, cancel: &Cancellation) -> Result<(Self, String)> {
        let values: Vec<Value> = serde_json::from_str(raw)?;
        let ids: HashSet<_> = values
            .iter()
            .map(|v| v["id"].as_str().context("Missing feature ID"))
            .collect::<Result<_>>()?;
        ensure!(
            ids.len() == values.len(),
            "Duplicate IDs in edit transaction"
        );
        let old: Vec<_> = values
            .iter()
            .map(|v| {
                self.entries
                    .iter()
                    .find(|e| Some(e.feature.id.as_str()) == v["id"].as_str())
                    .map(|e| serde_json::to_value(&e.feature).unwrap())
                    .unwrap_or_else(|| json!({"id":v["id"],"geometry":null}))
            })
            .collect();
        let mut entries: Vec<_> = self
            .entries
            .iter()
            .filter(|e| !ids.contains(e.feature.id.as_str()))
            .cloned()
            .collect();
        let additions: Vec<_> = values.iter().filter(|v| !v["geometry"].is_null()).collect();
        let added = Dataset::parse(&serde_json::to_string(&additions)?, cancel)?;
        entries.extend(added.entries);
        cancel.check()?;
        let fields = entries
            .iter()
            .flat_map(|e| e.feature.properties.keys().cloned())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        Ok((Self { entries, fields }, serde_json::to_string(&old)?))
    }
}

pub struct SelectionData {
    pub data: Arc<Dataset>,
    pub indices: Vec<usize>,
}
#[derive(Serialize)]
struct FeatureCollection<T> {
    #[serde(rename = "type")]
    kind: &'static str,
    features: T,
}
impl SelectionData {
    pub fn features(&self, bbox: Option<BBox>, cancel: &Cancellation) -> Result<Vec<Feature>> {
        if let Some(b) = bbox {
            validate_bbox(b)?;
        }
        let mut features = Vec::new();
        for &i in &self.indices {
            cancel.check()?;
            let entry = &self.data.entries[i];
            if let Some(b) = bbox {
                if !entry.bounds.is_some_and(|bounds| overlaps(bounds, b)) {
                    continue;
                }
                if let Some(geometry) = entry.feature.geometry.clip(b) {
                    let mut f = entry.feature.clone();
                    f.geometry = geometry;
                    features.push(f);
                }
            } else {
                features.push(entry.feature.clone());
            }
        }
        Ok(features)
    }
    pub fn geojson(&self, bbox: Option<BBox>, cancel: &Cancellation) -> Result<String> {
        cancel.check()?;
        let output = if bbox.is_some() {
            serde_json::to_string(&FeatureCollection {
                kind: "FeatureCollection",
                features: self.features(bbox, cancel)?,
            })?
        } else {
            let features: Vec<_> = self
                .indices
                .iter()
                .map(|&i| &self.data.entries[i].feature)
                .collect();
            serde_json::to_string(&FeatureCollection {
                kind: "FeatureCollection",
                features,
            })?
        };
        cancel.check()?;
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample() -> Dataset {
        Dataset::parse(r#"[{"id":"one","properties":{"name":"Rail 日本語"},"geometry":{"type":"LineString","coordinates":[[-2,0],[2,0]]}}]"#,&Cancellation::default()).unwrap()
    }
    #[test]
    fn regex_errors_propagate() {
        let f = Filter {
            regex: true,
            keywords: vec!["(?=x)".into()],
            ..Default::default()
        };
        assert!(sample().select(&f, &Cancellation::default()).is_err());
    }
    #[test]
    fn crossing_bbox_is_selected() {
        let f = Filter {
            bbox: Some([-1., -1., 1., 1.]),
            keywords: vec!["日本語".into()],
            ..Default::default()
        };
        assert_eq!(
            sample().select(&f, &Cancellation::default()).unwrap(),
            vec![0]
        );
    }
    #[test]
    fn cancellation() {
        let c = Cancellation::default();
        c.0.store(true, Ordering::Relaxed);
        assert!(sample().select(&Filter::default(), &c).is_err());
    }
}
