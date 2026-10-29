//! Linux storage durability and platform policy.
use anyhow::Result;
use std::path::Path;

pub fn initialize() -> Result<()> {
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
