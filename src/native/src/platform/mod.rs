//! Shared platform interface and application storage policy.
use anyhow::{Context, Result};
use std::{
    io::Write,
    path::{Path, PathBuf},
};

#[cfg(target_os = "linux")]
#[path = "linux.rs"]
mod os;
#[cfg(target_os = "windows")]
#[path = "windows.rs"]
mod os;

#[cfg(any(target_os = "linux", test))]
mod linux_input;
pub(crate) mod startup;
pub use os::{bundle_library, initialize, startup_ready, supervise_startup};

fn application_root(package_file: &Path, executable: &Path, packaged: bool) -> Result<PathBuf> {
    if packaged {
        return executable
            .parent()
            .map(Path::to_path_buf)
            .context("Cannot determine the application root");
    }
    package_file
        .ancestors()
        .find(|path| path.join("pyproject.toml").is_file())
        .map(Path::to_path_buf)
        .context("Cannot determine the source checkout root")
}

fn create_data_directory(root: &Path) -> Result<PathBuf> {
    let directory = root.join(".userdata");
    std::fs::create_dir_all(&directory)
        .with_context(|| format!("Cannot create {}", directory.display()))?;
    os::prepare_data_directory(&directory)?;
    Ok(directory)
}

pub fn data_directory(package_file: &Path, packaged: bool) -> Result<String> {
    let root = application_root(package_file, &std::env::current_exe()?, packaged)?;
    Ok(create_data_directory(&root)?.to_string_lossy().into_owned())
}

pub fn atomic_write(path: &str, data: &[u8]) -> Result<()> {
    let target = Path::new(path);
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(data)?;
    temporary.as_file().sync_all()?;
    os::replace_file(temporary, target, parent)
}

pub fn open_external(target: &str) -> Result<()> {
    open::that_detached(target).context("Cannot open external browser")
}
