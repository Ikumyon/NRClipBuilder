#![allow(dead_code)] // The NRC1 wire model includes every game object type.
mod dataset;
mod export;
mod geo;
pub mod geometry;
mod hobby;
mod import;
mod nrc1;
mod platform;
mod reduction;
mod tiles;
mod types;
mod wire;
mod wyhash_nrc1;

use dataset::{Cancellation, Dataset, Filter, SelectionData};
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::PyBytes,
};
use std::{
    collections::HashMap,
    sync::{Arc, RwLock, atomic::Ordering},
};

fn error(e: anyhow::Error) -> PyErr {
    PyValueError::new_err(format!("{e:#}"))
}

#[pyclass(from_py_object)]
#[derive(Clone, Default)]
struct CancelToken {
    inner: Cancellation,
}
#[pymethods]
impl CancelToken {
    #[new]
    fn new() -> Self {
        Self::default()
    }
    fn cancel(&self) {
        self.inner.0.store(true, Ordering::Relaxed);
    }
    fn check(&self) -> PyResult<()> {
        self.inner.check().map_err(error)
    }
}

#[pyclass]
struct Engine {
    datasets: RwLock<HashMap<String, Arc<Dataset>>>,
}
impl Engine {
    fn get(&self, id: &str) -> PyResult<Arc<Dataset>> {
        self.datasets
            .read()
            .map_err(|_| PyRuntimeError::new_err("Dataset lock poisoned"))?
            .get(id)
            .cloned()
            .ok_or_else(|| PyValueError::new_err("Unknown dataset ID"))
    }
}
#[pymethods]
impl Engine {
    #[new]
    fn new() -> Self {
        Self {
            datasets: RwLock::new(HashMap::new()),
        }
    }
    fn register(
        &self,
        py: Python<'_>,
        id: String,
        features: String,
        token: CancelToken,
    ) -> PyResult<()> {
        let data = py
            .detach(|| Dataset::parse(&features, &token.inner))
            .map_err(error)?;
        let mut datasets = self
            .datasets
            .write()
            .map_err(|_| PyRuntimeError::new_err("Dataset lock poisoned"))?;
        token.inner.check().map_err(error)?;
        datasets.insert(id, Arc::new(data));
        Ok(())
    }
    fn remove(&self, id: &str) -> PyResult<()> {
        self.datasets
            .write()
            .map_err(|_| PyRuntimeError::new_err("Dataset lock poisoned"))?
            .remove(id);
        Ok(())
    }
    fn snapshot(&self, py: Python<'_>, id: &str) -> PyResult<String> {
        let data = self.get(id)?;
        Ok(py.detach(move || data.json()))
    }
    fn fields(&self, id: &str) -> PyResult<Vec<String>> {
        Ok(self.get(id)?.fields.clone())
    }
    fn capture(&self, id: &str) -> PyResult<Selection> {
        let data = self.get(id)?;
        let indices = (0..data.entries.len()).collect();
        Ok(Selection {
            inner: SelectionData { data, indices },
        })
    }
    fn prepare_export(
        &self,
        ids: Vec<String>,
        filters: String,
        bbox: Option<[f64; 4]>,
    ) -> PyResult<ExportBatch> {
        let filter =
            serde_json::from_str(&filters).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let sources = ids
            .into_iter()
            .map(|id| self.get(&id).map(|data| (id, data)))
            .collect::<PyResult<_>>()?;
        Ok(ExportBatch {
            sources,
            filter,
            bbox,
            history: None,
        })
    }
    fn edit_many(
        &self,
        py: Python<'_>,
        id: String,
        features: String,
        token: CancelToken,
    ) -> PyResult<String> {
        let data = self.get(&id)?;
        let (updated, old) = py
            .detach(|| data.edited(&features, &token.inner))
            .map_err(error)?;
        let mut datasets = self
            .datasets
            .write()
            .map_err(|_| PyRuntimeError::new_err("Dataset lock poisoned"))?;
        token.inner.check().map_err(error)?;
        let current = datasets
            .get(&id)
            .ok_or_else(|| PyValueError::new_err("Layer was removed"))?;
        if !Arc::ptr_eq(current, &data) {
            return Err(PyValueError::new_err("Concurrent dataset modification"));
        }
        datasets.insert(id, Arc::new(updated));
        Ok(old)
    }
    fn feature(&self, id: &str, feature_id: &str) -> PyResult<String> {
        let data = self.get(id)?;
        let entry = data
            .entries
            .iter()
            .find(|e| e.feature.id == feature_id)
            .ok_or_else(|| PyValueError::new_err("Unknown feature ID"))?;
        serde_json::to_string(&entry.feature).map_err(|e| PyValueError::new_err(e.to_string()))
    }
    fn select(
        &self,
        py: Python<'_>,
        id: &str,
        options: String,
        token: CancelToken,
    ) -> PyResult<Selection> {
        let data = self.get(id)?;
        let filter: Filter =
            serde_json::from_str(&options).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let indices = py
            .detach(|| data.select(&filter, &token.inner))
            .map_err(error)?;
        Ok(Selection {
            inner: SelectionData { data, indices },
        })
    }
}

#[pyclass]
struct Selection {
    inner: SelectionData,
}
#[pymethods]
impl Selection {
    #[getter]
    fn count(&self) -> usize {
        self.inner.indices.len()
    }
    fn fields(&self) -> Vec<String> {
        self.inner.data.fields.clone()
    }
    fn select(&self, py: Python<'_>, options: String, token: CancelToken) -> PyResult<Selection> {
        let filter: Filter =
            serde_json::from_str(&options).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let data = Arc::clone(&self.inner.data);
        let indices = py
            .detach(|| data.select(&filter, &token.inner))
            .map_err(error)?;
        Ok(Selection {
            inner: SelectionData { data, indices },
        })
    }
    fn page(&self, py: Python<'_>, offset: usize, limit: usize) -> String {
        py.detach(|| {
            serde_json::to_string(
                &self
                    .inner
                    .indices
                    .iter()
                    .skip(offset)
                    .take(limit.min(1024))
                    .map(|&i| {
                        let f = &self.inner.data.entries[i].feature;
                        serde_json::json!({"id":f.id,"properties":f.properties})
                    })
                    .collect::<Vec<_>>(),
            )
            .expect("finite feature JSON")
        })
    }
    fn geojson(&self, py: Python<'_>, token: CancelToken) -> PyResult<String> {
        py.detach(|| self.inner.geojson(None, &token.inner))
            .map_err(error)
    }
    fn clipped_geojson(
        &self,
        py: Python<'_>,
        bbox: [f64; 4],
        token: CancelToken,
    ) -> PyResult<String> {
        py.detach(|| self.inner.geojson(Some(bbox), &token.inner))
            .map_err(error)
    }
}

/// Immutable input captured when an export dialog opens. Filtering happens on the worker.
#[pyclass]
struct ExportBatch {
    sources: Vec<(String, Arc<Dataset>)>,
    filter: Filter,
    bbox: Option<[f64; 4]>,
    history: Option<String>,
}

#[pymethods]
impl ExportBatch {
    #[staticmethod]
    fn from_history(geojson: String, bbox: [f64; 4]) -> Self {
        Self {
            sources: Vec::new(),
            filter: Filter::default(),
            bbox: Some(bbox),
            history: Some(geojson),
        }
    }

    fn export(
        &self,
        py: Python<'_>,
        format: &str,
        options: String,
        token: CancelToken,
    ) -> PyResult<(Py<PyBytes>, String)> {
        let (bytes, geojson) = py
            .detach(|| -> anyhow::Result<_> {
                let bbox = self
                    .bbox
                    .ok_or_else(|| anyhow::anyhow!("Select a BBox before exporting"))?;
                geometry::validate_bbox(bbox)?;
                let mut features = Vec::new();
                if let Some(raw) = &self.history {
                    let value: serde_json::Value = serde_json::from_str(raw)?;
                    let data =
                        Dataset::parse(&serde_json::to_string(&value["features"])?, &token.inner)?;
                    features = export::line_features(
                        &SelectionData {
                            indices: (0..data.entries.len()).collect(),
                            data: Arc::new(data),
                        },
                        bbox,
                        &token.inner,
                    )?;
                } else {
                    for (id, data) in &self.sources {
                        token.inner.check()?;
                        let indices = data.select(&self.filter, &token.inner)?;
                        let selection = SelectionData {
                            data: Arc::clone(data),
                            indices,
                        };
                        for mut feature in export::line_features(&selection, bbox, &token.inner)? {
                            feature
                                .properties
                                .insert("source_id".into(), serde_json::json!(feature.id));
                            feature
                                .properties
                                .insert("source_layer".into(), serde_json::json!(id));
                            feature.id = serde_json::to_string(&(id, &feature.id))?;
                            features.push(feature);
                        }
                    }
                }
                anyhow::ensure!(!features.is_empty(), "No railway lines in the export range");
                let geojson = serde_json::to_string(
                    &serde_json::json!({"type":"FeatureCollection", "features":features}),
                )?;
                let bytes = export::convert(format, &options, &features, &token.inner)?;
                token.inner.check()?;
                Ok((bytes, geojson))
            })
            .map_err(error)?;
        Ok((PyBytes::new(py, &bytes).unbind(), geojson))
    }
}

#[pyfunction]
fn data_directory(py: Python<'_>) -> PyResult<String> {
    let sys = py.import("sys")?;
    let packaged = sys
        .getattr("frozen")
        .is_ok_and(|value| value.is_truthy().unwrap_or(false));
    let package_file: String = py
        .import("nrclip_builder")?
        .getattr("__file__")?
        .extract()?;
    platform::data_directory(std::path::Path::new(&package_file), packaged).map_err(error)
}
#[pyfunction]
fn system_locale() -> String {
    sys_locale::get_locale()
        .unwrap_or_else(|| "en-US".into())
        .replace('_', "-")
        .to_lowercase()
}
#[pyfunction]
fn initialize(py: Python<'_>) -> PyResult<()> {
    platform::initialize(py).map_err(error)
}
#[pyfunction]
fn supervise_startup(py: Python<'_>) -> PyResult<(bool, Option<i32>)> {
    platform::supervise_startup(py).map_err(error)
}
#[pyfunction]
fn startup_ready() -> PyResult<()> {
    platform::startup_ready().map_err(error)
}
#[pyfunction]
fn atomic_write(py: Python<'_>, path: String, data: Vec<u8>) -> PyResult<()> {
    py.detach(move || platform::atomic_write(&path, &data))
        .map_err(error)
}
#[pyfunction]
fn open_external(target: &str) -> PyResult<()> {
    platform::open_external(target).map_err(error)
}
#[pyfunction]
fn validate_bbox(bbox: [f64; 4]) -> PyResult<()> {
    geometry::validate_bbox(bbox).map(|_| ()).map_err(error)
}

#[pyfunction]
fn bundle_library(name: &str) -> bool {
    platform::bundle_library(name)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<Engine>()?;
    m.add_class::<Selection>()?;
    m.add_class::<ExportBatch>()?;
    m.add_class::<CancelToken>()?;
    m.add_function(wrap_pyfunction!(data_directory, m)?)?;
    m.add_function(wrap_pyfunction!(system_locale, m)?)?;
    m.add_function(wrap_pyfunction!(initialize, m)?)?;
    m.add_function(wrap_pyfunction!(supervise_startup, m)?)?;
    m.add_function(wrap_pyfunction!(startup_ready, m)?)?;
    m.add(
        "GRAPHICS_STARTUP_FAILURE",
        platform::startup::GRAPHICS_STARTUP_FAILURE,
    )?;
    m.add_function(wrap_pyfunction!(atomic_write, m)?)?;
    m.add_function(wrap_pyfunction!(open_external, m)?)?;
    m.add_function(wrap_pyfunction!(validate_bbox, m)?)?;
    m.add_function(wrap_pyfunction!(bundle_library, m)?)?;
    Ok(())
}
