//! Supervise graphics startup in separate processes; Qt may abort instead of returning an error.
use anyhow::{Context, Result};
use std::{
    ffi::OsString,
    path::Path,
    process::{Command, ExitStatus},
    thread,
    time::{Duration, Instant},
};

pub const GRAPHICS_STARTUP_FAILURE: i32 = 86;
pub const READY_ENV: &str = "NRCLIP_STARTUP_READY";

pub struct GraphicsMode {
    pub name: &'static str,
    pub environment: Vec<(&'static str, OsString)>,
}

fn graphics_failure(status: ExitStatus) -> bool {
    if status.code() == Some(GRAPHICS_STARTUP_FAILURE) {
        return true;
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        // Fatal native faults can come from Qt. SIGINT/SIGTERM are cancellation,
        // so they must not cause a replacement window to be opened.
        matches!(status.signal(), Some(4 | 6 | 7 | 11))
    }
    #[cfg(not(unix))]
    false
}

pub fn supervise(
    executable: &Path,
    arguments: &[OsString],
    modes: &[GraphicsMode],
    timeout: Duration,
) -> Result<i32> {
    let directory = tempfile::tempdir().context("Cannot create startup monitor directory")?;
    let ready = directory.path().join("ready");
    for (index, mode) in modes.iter().enumerate() {
        if index > 0 {
            eprintln!("Graphics startup failed; retrying with {}.", mode.name);
        }
        let mut command = Command::new(executable);
        command.args(arguments).env(READY_ENV, &ready);
        for (key, value) in &mode.environment {
            command.env(key, value);
        }
        let mut child = command
            .spawn()
            .context("Cannot start application process")?;
        let started = Instant::now();
        let mut running = false;
        let status = loop {
            // Read this before the exit status, including when the child exits immediately
            // after reporting success. A later crash must never restart a user's session.
            running |= ready.is_file();
            if let Some(status) = child.try_wait()? {
                running |= ready.is_file();
                break Some(status);
            }
            if running {
                break Some(child.wait()?);
            }
            if !running && started.elapsed() >= timeout {
                child.kill()?;
                child.wait()?;
                eprintln!("Graphics startup timed out with {}.", mode.name);
                break None;
            }
            thread::sleep(Duration::from_millis(25));
        };
        if let Some(status) = status {
            let code = status.code().unwrap_or(1);
            if running || status.success() || !graphics_failure(status) || index + 1 == modes.len()
            {
                return Ok(code);
            }
        } else if index + 1 == modes.len() {
            return Ok(GRAPHICS_STARTUP_FAILURE);
        }
    }
    anyhow::bail!("No graphics startup modes configured")
}
