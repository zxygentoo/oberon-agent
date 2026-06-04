//! Error type and exit-code mapping.
//!
//! Each variant carries enough context for a useful one-line message with a
//! hint where appropriate. `Display` writes that body; `main.rs` adds the
//! `oat: error: ` prefix. `exit_code()` follows the contract documented in
//! `--help` and SKILL.md: tool-level errors -> 1, transport / protocol /
//! argument errors -> 2.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum Error {
    NoSerial,
    BadName { name: String, len: usize },

    OpenFifo { path: PathBuf, source: io::Error },
    OpenSerial { path: PathBuf, source: io::Error },
    Io(io::Error),
    Timeout { secs: f64, got: usize, want: usize },
    Eof,

    BadSync { got: u8, expected: u8 },
    BadStatus { status: u8 },

    NotFound { path: String },
    EditNotFound,
    EditNotUnique { count: usize },
    LoadFailed { res: Option<i32>, log: String },
    UnloadInUse { log: String },
    CompileFailed,
    Trapped,
}

impl Error {
    pub fn exit_code(&self) -> i32 {
        match self {
            Self::NotFound { .. }
            | Self::EditNotFound
            | Self::EditNotUnique { .. }
            | Self::LoadFailed { .. }
            | Self::UnloadInUse { .. }
            | Self::CompileFailed
            | Self::Trapped => 1,
            _ => 2,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoSerial => f.write_str(
                "no serial connection specified — pass --serial or --serial-in/--serial-out",
            ),
            Self::BadName { name, len } => write!(
                f,
                "name length {len} out of range 1..255: {name:?}"
            ),
            Self::OpenFifo { path, source } => fmt_open_fifo(f, path, source),
            Self::OpenSerial { path, source } => {
                write!(f, "cannot open serial device {}: {source}", path.display())
            }
            Self::Io(e) => write!(f, "{e}"),
            Self::Timeout { secs, got, want } => write!(
                f,
                "no response from emulator after {secs}s ({got}/{want} bytes received)\n  \
                 hint: is `risc --serial-in <p.in> --serial-out <p.out> <image>.dsk` running?"
            ),
            Self::Eof => f.write_str(
                "serial line closed (EOF) — the emulator dropped the connection",
            ),
            Self::BadSync { got, expected } => write!(
                f,
                "bad response sync byte 0x{got:02X} (expected 0x{expected:02X}) — \
                 device is out of frame; restart the emulator"
            ),
            Self::BadStatus { status } => write!(f, "device returned status={status}"),
            Self::NotFound { path } => write!(f, "file not found: {path}"),
            Self::EditNotFound => f.write_str("OLD string not found in file"),
            Self::EditNotUnique { count } => write!(
                f,
                "OLD string occurs {count} times in file (must be unique)"
            ),
            Self::LoadFailed { res, log } => fmt_load_failed(f, *res, log),
            Self::UnloadInUse { log } => {
                f.write_str("unload refused — other loaded modules still import this one")?;
                write_indented_log(f, log)
            }
            Self::CompileFailed => f.write_str("compilation FAILED (see log above)"),
            Self::Trapped => f.write_str("trapped (see TRAP message in log above)"),
        }
    }
}

fn fmt_open_fifo(f: &mut fmt::Formatter<'_>, path: &Path, source: &io::Error) -> fmt::Result {
    match source.kind() {
        io::ErrorKind::NotFound => write!(
            f,
            "FIFO does not exist: {}\n  hint: create with `mkfifo /tmp/p.in /tmp/p.out`",
            path.display()
        ),
        io::ErrorKind::PermissionDenied => {
            write!(f, "permission denied opening FIFO {}", path.display())
        }
        _ => write!(f, "cannot open FIFO {}: {source}", path.display()),
    }
}

fn fmt_load_failed(f: &mut fmt::Formatter<'_>, res: Option<i32>, log: &str) -> fmt::Result {
    f.write_str("load failed")?;
    if let Some(r) = res {
        write!(f, " (res={r})")?;
        if let Some(hint) = res_hint(r) {
            write!(f, "\n  hint: {hint}")?;
        }
    }
    write_indented_log(f, log)
}

fn write_indented_log(f: &mut fmt::Formatter<'_>, log: &str) -> fmt::Result {
    for line in log.trim().lines() {
        write!(f, "\n  {line}")?;
    }
    Ok(())
}

fn res_hint(res: i32) -> Option<&'static str> {
    match res {
        1 => Some("name invalid or .rsc not found — has the module been compiled?"),
        2 => Some("bad symbol-file key — recompile importers or compile with --new-symbol"),
        3 => Some("import key conflict — recompile importers or unload them first"),
        4 => Some("corrupted object file"),
        5 => Some("command not found in module"),
        7 => Some("no module space — unload unused modules"),
        _ => None,
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::OpenFifo { source, .. } | Self::OpenSerial { source, .. } => Some(source),
            Self::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Self::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exit_codes_partition_tool_vs_transport() {
        // Tool-level errors -> 1 (the contract in --help and SKILL.md).
        assert_eq!(Error::CompileFailed.exit_code(), 1);
        assert_eq!(Error::NotFound { path: "X".into() }.exit_code(), 1);
        assert_eq!(Error::Trapped.exit_code(), 1);
        // Transport / protocol / argument errors -> 2.
        assert_eq!(Error::NoSerial.exit_code(), 2);
        assert_eq!(Error::Eof.exit_code(), 2);
        assert_eq!(
            Error::BadName {
                name: String::new(),
                len: 0
            }
            .exit_code(),
            2
        );
    }

    #[test]
    fn load_failed_message_carries_res_hint_and_indented_log() {
        let e = Error::LoadFailed {
            res: Some(2),
            log: "AgentTool.Load\nres=2\n".into(),
        };
        let msg = e.to_string();
        assert!(msg.contains("res=2"));
        assert!(msg.contains("hint: bad symbol-file key"));
        assert!(msg.contains("\n  AgentTool.Load"));
    }
}
