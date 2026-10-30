//! Linux storage durability and platform policy.
use anyhow::Result;
use pyo3::prelude::*;
use std::{ffi::OsString, path::Path, time::Duration};

use super::startup::{GraphicsMode, READY_ENV, supervise};

pub fn initialize(py: Python<'_>) -> Result<()> {
    // Qt can prefer QT_IM_MODULES over QT_IM_MODULE. Keep both consistent with
    // the explicit single-module choice, or with the desktop's input server.
    super::linux_input::initialize(py)
}

pub fn supervise_startup(py: Python<'_>) -> Result<(bool, Option<i32>)> {
    if std::env::var_os(READY_ENV).is_some() {
        return Ok((true, None));
    }
    let sys = py.import("sys")?;
    let executable: String = sys.getattr("executable")?.extract()?;
    let packaged = sys
        .getattr("frozen")
        .is_ok_and(|value| value.is_truthy().unwrap_or(false));
    let arguments: Vec<String> = sys
        .getattr(if packaged { "argv" } else { "orig_argv" })?
        .extract()?;
    let arguments: Vec<OsString> = arguments.into_iter().skip(1).map(OsString::from).collect();
    let flags = std::env::var_os("QTWEBENGINE_CHROMIUM_FLAGS").unwrap_or_default();
    let with_flag = |flag: &str| {
        let mut value = flags.clone();
        if !value.is_empty() {
            value.push(" ");
        }
        value.push(flag);
        value
    };
    let modes = [
        GraphicsMode {
            name: "default graphics",
            environment: vec![],
        },
        GraphicsMode {
            name: "native EGL",
            environment: vec![
                ("QT_XCB_GL_INTEGRATION", "xcb_egl".into()),
                ("QTWEBENGINE_CHROMIUM_FLAGS", with_flag("--use-gl=egl")),
            ],
        },
        GraphicsMode {
            name: "GPU acceleration disabled",
            environment: vec![("QTWEBENGINE_CHROMIUM_FLAGS", with_flag("--disable-gpu"))],
        },
    ];
    py.detach(move || {
        supervise(
            Path::new(&executable),
            &arguments,
            &modes,
            Duration::from_secs(30),
        )
        .map(|code| (false, Some(code)))
    })
}

pub fn startup_ready() -> Result<()> {
    if let Some(path) = std::env::var_os(READY_ENV) {
        std::fs::write(path, b"ready")?;
    }
    Ok(())
}

pub(super) fn prepare_data_directory(_directory: &Path) -> Result<()> {
    // The shared .userdata name already makes the directory hidden on Linux.
    Ok(())
}

pub(super) fn replace_file(
    temporary: tempfile::NamedTempFile,
    target: &Path,
    parent: &Path,
) -> Result<()> {
    temporary.persist(target).map_err(|e| e.error)?;
    std::fs::File::open(parent)?.sync_all()?;
    Ok(())
}

pub fn bundle_library(_name: &str) -> bool {
    true
}
