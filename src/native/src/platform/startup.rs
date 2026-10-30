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

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::prelude::*;

    fn run(scenario: &str, timeout: Duration) -> (i32, Vec<String>) {
        Python::initialize();
        let python: String = Python::attach(|py| {
            // Embedded Python sets sys.executable to this Rust test binary.
            // Use the interpreter from sys.base_prefix, not the test runner.
            let prefix: String = py
                .import("sys")
                .unwrap()
                .getattr("base_prefix")
                .unwrap()
                .extract()
                .unwrap();
            if cfg!(windows) {
                Path::new(&prefix).join("python.exe")
            } else {
                Path::new(&prefix).join("bin/python3")
            }
            .to_string_lossy()
            .into_owned()
        });
        let directory = tempfile::tempdir().unwrap();
        let trace = directory.path().join("attempts");
        let arguments = vec![
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/graphics_startup.py")
                .into_os_string(),
            scenario.into(),
            trace.clone().into_os_string(),
        ];
        let modes: Vec<_> = ["default", "egl", "software"]
            .into_iter()
            .map(|name| GraphicsMode {
                name,
                environment: vec![("NRCLIP_TEST_MODE", name.into())],
            })
            .collect();
        let code = supervise(Path::new(&python), &arguments, &modes, timeout).unwrap();
        let attempts = std::fs::read_to_string(trace)
            .unwrap()
            .lines()
            .map(str::to_owned)
            .collect();
        (code, attempts)
    }

    #[test]
    fn normal_start_does_not_try_other_modes() {
        assert_eq!(
            run("default", Duration::from_secs(5)),
            (0, vec!["default".into()])
        );
        assert_eq!(
            run("clean-exit", Duration::from_secs(5)),
            (0, vec!["default".into()])
        );
    }

    #[test]
    fn egl_is_only_tried_after_default_fails() {
        assert_eq!(
            run("egl", Duration::from_secs(5)),
            (0, vec!["default".into(), "egl".into()])
        );
    }

    #[test]
    fn software_is_last_and_attempts_are_bounded() {
        let attempts = vec!["default".into(), "egl".into(), "software".into()];
        assert_eq!(
            run("software", Duration::from_secs(5)),
            (0, attempts.clone())
        );
        assert_eq!(
            run("all-fail", Duration::from_secs(5)),
            (GRAPHICS_STARTUP_FAILURE, attempts)
        );
    }

    #[test]
    fn failures_after_ready_do_not_restart_the_session() {
        assert_eq!(
            run("late-failure", Duration::from_secs(5)),
            (GRAPHICS_STARTUP_FAILURE, vec!["default".into()])
        );
    }

    #[test]
    fn unrelated_startup_error_does_not_trigger_graphics_retries() {
        assert_eq!(
            run("unrelated-error", Duration::from_secs(5)),
            (1, vec!["default".into()])
        );
    }

    #[test]
    fn hung_startup_is_terminated_before_retrying() {
        assert_eq!(
            run("timeout", Duration::from_secs(1)),
            (0, vec!["default".into(), "egl".into()])
        );
    }

    #[cfg(unix)]
    #[test]
    fn signal_before_ready_triggers_retry() {
        assert_eq!(
            run("signal", Duration::from_secs(5)),
            (0, vec!["default".into(), "egl".into()])
        );
        assert_eq!(
            run("terminated", Duration::from_secs(5)),
            (1, vec!["default".into()])
        );
    }
}
