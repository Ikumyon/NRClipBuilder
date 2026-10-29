//! Windows application identity, hidden attributes and file replacement.
use anyhow::{Context, Result};
use std::{
    os::windows::{ffi::OsStrExt, fs::MetadataExt},
    path::Path,
};
use windows_sys::Win32::Storage::FileSystem::{FILE_ATTRIBUTE_HIDDEN, SetFileAttributesW};

pub fn initialize() -> Result<()> {
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

#[cfg(test)]
mod tests {
    #[test]
    fn system_icu_is_not_bundled_from_path() {
        assert!(!super::bundle_library("ICUUC.dll"));
        assert!(!super::bundle_library("icudt78.dll"));
        assert!(super::bundle_library("Qt6Core.dll"));
    }

    #[test]
    fn data_directory_has_hidden_attribute() {
        use std::os::windows::fs::MetadataExt;
        let root = tempfile::tempdir().unwrap();
        let directory = super::super::create_data_directory(root.path()).unwrap();
        assert_ne!(
            std::fs::metadata(directory).unwrap().file_attributes() & super::FILE_ATTRIBUTE_HIDDEN,
            0
        );
    }
}
