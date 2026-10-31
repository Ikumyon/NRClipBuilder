//! Windows application identity, hidden attributes and file replacement.
use anyhow::{Context, Result};
use pyo3::Python;
use std::{
    os::windows::{ffi::OsStrExt, fs::MetadataExt},
    path::Path,
};
use windows_sys::Win32::Storage::FileSystem::{FILE_ATTRIBUTE_HIDDEN, SetFileAttributesW};

pub fn initialize(_py: Python<'_>) -> Result<()> {
    let id: Vec<u16> = "NRClipBuilder.Desktop\0".encode_utf16().collect();
    // The UTF-16 buffer is NUL terminated and lives until the call returns.
    let status = unsafe {
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(id.as_ptr())
    };
    anyhow::ensure!(
        status >= 0,
        "Cannot set Windows application identity: {status}"
    );
    Ok(())
}

pub fn supervise_startup(_py: Python<'_>) -> Result<(bool, Option<i32>)> {
    Ok((false, None))
}

pub fn startup_ready() -> Result<()> {
    Ok(())
}

pub(super) fn prepare_data_directory(directory: &Path) -> Result<()> {
    let attributes = std::fs::metadata(directory)?.file_attributes();
    let path: Vec<u16> = directory.as_os_str().encode_wide().chain(Some(0)).collect();
    if unsafe { SetFileAttributesW(path.as_ptr(), attributes | FILE_ATTRIBUTE_HIDDEN) } == 0 {
        return Err(std::io::Error::last_os_error()).context("Cannot hide the user data directory");
    }
    Ok(())
}

pub(super) fn replace_file(
    temporary: tempfile::NamedTempFile,
    target: &Path,
    _parent: &Path,
) -> Result<()> {
    temporary.persist(target).map_err(|e| e.error)?;
    Ok(())
}

/// The pinned Qt wheels use OS ICU. An unrelated ICU from PATH breaks Qt.
pub fn bundle_library(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    !(matches!(name.as_str(), "icu.dll" | "icuuc.dll" | "icuin.dll")
        || (name.starts_with("icudt") && name.ends_with(".dll")))
}
