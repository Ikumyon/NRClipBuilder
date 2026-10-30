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

#[cfg(test)]
mod tests {
    use super::preferred_module;

    #[test]
    fn explicit_fcitx_is_used_even_when_desktop_modifiers_say_ibus() {
        assert_eq!(preferred_module(Some("fcitx"), "@im=ibus"), Some("fcitx"));
    }

    #[test]
    fn fcitx_desktop_is_used_without_a_single_module_override() {
        assert_eq!(preferred_module(None, "@im=fcitx"), Some("fcitx"));
        assert_eq!(preferred_module(Some(""), "@im=fcitx"), Some("fcitx"));
    }

    #[test]
    fn other_qt_selection_is_left_alone_without_a_known_desktop() {
        assert_eq!(preferred_module(None, "@im=none"), None);
        assert_eq!(preferred_module(None, ""), None);
        assert_eq!(
            preferred_module(Some("compose"), "@im=fcitx"),
            Some("compose")
        );
    }

    #[test]
    fn ibus_list_cannot_override_fcitx_selection() {
        use pyo3::{prelude::*, types::PyDict};

        Python::initialize();
        Python::attach(|py| {
            for explicit in [true, false] {
                let environment = PyDict::new(py);
                environment.set_item("QT_IM_MODULES", "ibus").unwrap();
                environment.set_item("XMODIFIERS", "@im=fcitx").unwrap();
                if explicit {
                    environment.set_item("QT_IM_MODULE", "fcitx").unwrap();
                }
                super::configure(environment.as_any()).unwrap();
                for key in ["QT_IM_MODULE", "QT_IM_MODULES"] {
                    assert_eq!(
                        environment
                            .get_item(key)
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        "fcitx"
                    );
                }
            }
        });
    }
}
