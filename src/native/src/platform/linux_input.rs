//! Resolve Linux desktop input-method selection before Qt loads its plugins.

use anyhow::Result;
use pyo3::prelude::*;

#[cfg(target_os = "linux")]
pub(super) fn initialize(py: Python<'_>) -> Result<()> {
    configure(&py.import("os")?.getattr("environ")?)
}

fn configure(environment: &Bound<'_, PyAny>) -> Result<()> {
    let configured: Option<String> = environment
        .call_method1("get", ("QT_IM_MODULE",))?
        .extract()?;
    let modifiers: String = environment
        .call_method1("get", ("XMODIFIERS", ""))?
        .extract()?;
    if let Some(module) = preferred_module(configured.as_deref(), &modifiers) {
        environment.set_item("QT_IM_MODULE", module)?;
        environment.set_item("QT_IM_MODULES", module)?;
    }
    Ok(())
}

pub(super) fn preferred_module<'a>(module: Option<&'a str>, modifiers: &str) -> Option<&'a str> {
    if let Some(module) = module.map(str::trim).filter(|value| !value.is_empty()) {
        return Some(module);
    }
    match modifiers
        .split('@')
        .find_map(|part| part.strip_prefix("im="))
    {
        Some("fcitx") => Some("fcitx"),
        Some("ibus") => Some("ibus"),
        _ => None,
    }
}
