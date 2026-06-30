//! The command-line surface: clap definitions, the per-subcommand handlers,
//! and the dispatch from parsed arguments into tools calls. The whole surface
//! is private — `run` is the one entry point, called by main.rs.

use std::io::{Read, Write};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use clap::{Args, Parser, Subcommand};

use crate::error::{Error, Result};
use crate::protocol::Request;
use crate::retry::Retry;
use crate::tools;
use crate::transport::{self, Transport};

const ABOUT: &str =
    "drive AgentTool.Mod on a live Project Oberon or Extended Oberon system over a serial link";

const LONG_ABOUT: &str = "\
A stateless CLI: each invocation opens the serial line, runs one command, prints its
result, and exits. The wire protocol is PUT/GET/CALL/EDIT — four opcodes between the
host and AgentTool.Mod on the device.";

const AFTER_HELP: &str = "\
Exit codes:
  0  Success.
  1  Tool-level error (file not found, compile failed, unload refused, trap).
  2  Transport / protocol error (no connection, timeout, bad frame, bad args).

Example:
  mkfifo /tmp/p.in /tmp/p.out                                                    # once
  risc --serial-in /tmp/p.in --serial-out /tmp/p.out DiskImage/ExtendedOberon.dsk &
  oat --serial-in /tmp/p.in --serial-out /tmp/p.out check";

#[derive(Parser)]
#[command(
    name = "oat",
    version,
    about = ABOUT,
    long_about = LONG_ABOUT,
    after_help = AFTER_HELP,
)]
struct Cli {
    /// Serial read timeout per request, in seconds.
    #[arg(long, value_name = "SECS", default_value_t = 15.0)]
    timeout: f64,

    /// Baud rate for a real serial device (--serial). Ignored for FIFO pairs.
    #[arg(long, value_name = "RATE", default_value_t = 19200)]
    baud: u32,

    /// Inter-byte ("character") delay for a real serial device (--serial), in microseconds.
    /// The FPGA's RS232R is a single-byte register with no flow control, read by a
    /// cooperative poll, so a request fired with no gap overruns it: the next byte lands
    /// before the poll grabbed the last, and the frame desyncs. (The device buffers a bulk
    /// PUT *payload* itself, but the small request frames still need throttling.) Default
    /// 1000 is ~2x the ~520us byte-time at 19200 baud -- reliable for automated/back-to-back
    /// use; 0 = full line rate, fine only for hand-spaced commands. Ignored for FIFOs.
    #[arg(long, value_name = "US", default_value_t = 1000)]
    char_delay_us: u64,

    /// Re-send a request this many times if it desyncs (transport timeout or a
    /// misframed reply) on a real serial device (--serial). The FPGA's single-
    /// byte register drops a request frame when an Oberon.Loop stall (GC, other
    /// tasks) outlasts the inter-byte gap; char-delay reduces this but can't
    /// remove it. The device self-recovers per frame, so a re-send on a fresh
    /// sync succeeds (measured: one retry ~82->97%, two ~100% on PO; EO needs
    /// none). Only desyncs are retried, never tool-level errors. Ignored for
    /// FIFOs (lossless).
    #[arg(long, value_name = "N", default_value_t = 2)]
    retries: u32,

    #[command(flatten)]
    serial: SerialOpts,

    #[command(subcommand)]
    command: Cmd,
}

#[derive(Args)]
#[command(next_help_heading = "Serial connection (one form required)")]
struct SerialOpts {
    /// Existing PTY / serial device (raw mode set on open).
    #[arg(
        long,
        value_name = "PATH",
        conflicts_with_all = ["serial_in", "serial_out"],
    )]
    serial: Option<PathBuf>,

    /// FIFO the emulator reads (we write). Must be paired with --serial-out.
    #[arg(long = "serial-in", value_name = "PATH", requires = "serial_out")]
    serial_in: Option<PathBuf>,

    /// FIFO the emulator writes (we read). Must be paired with --serial-in.
    #[arg(long = "serial-out", value_name = "PATH", requires = "serial_in")]
    serial_out: Option<PathBuf>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Check that the wire is up and report the OS variant + version.
    Check,

    /// Read a file from the device; content -> stdout.
    Read {
        /// File name, e.g. 'AgentTool.Mod'.
        path: String,
    },

    /// Create or overwrite a file with content from stdin.
    Write {
        /// File name, e.g. 'Stars.Mod'.
        path: String,
    },

    /// Replace a unique occurrence of OLD with NEW in PATH (`str_replace`).
    Edit {
        /// File on the Oberon device.
        path: String,
        /// Exact text to replace; must occur exactly once.
        old: String,
        /// Replacement text.
        new: String,
    },

    /// Delete a file.
    Delete {
        /// File name.
        path: String,
    },

    /// List files (TSV: name<TAB>size<TAB>date). Optional name prefix.
    #[command(name = "list-files")]
    ListFiles {
        /// Optional name prefix; empty lists all files.
        prefix: Option<String>,
    },

    /// List loaded modules (TSV: name<TAB>refcnt<TAB>`code_addr`).
    #[command(name = "list-modules")]
    ListModules,

    /// Compile a module via ORP.Compile; compiler log -> stdout.
    Compile {
        /// Source file, e.g. 'Stars.Mod'.
        name: String,
        /// Pass /s — rewrite the .smb file (use when the module's exported
        /// interface changed).
        #[arg(short = 's', long = "new-symbol")]
        new_symbol: bool,
    },

    /// Load a compiled module.
    Load {
        /// Module name (no extension), e.g. 'Stars'.
        name: String,
    },

    /// Unload a module. EO: safe-unload via System.Free /f.
    /// PO: System.Free (no hide-and-collect — dangling refs possible).
    Unload {
        /// Module name.
        name: String,
    },

    /// Run any Oberon command 'Mod.Proc'; Log delta -> stdout.
    Call {
        /// Command 'Mod.Proc', e.g. 'Stars.Show'.
        cmd: String,
        /// Parameter text scanned via Oberon.Par. Omit for no args.
        args: Option<String>,
    },
}

/// Parse the command line, open the transport, run the subcommand.
pub fn run() -> Result<()> {
    let cli = Cli::parse();
    let timeout = Duration::from_secs_f64(cli.timeout.max(0.001));
    let char_delay = Duration::from_micros(cli.char_delay_us);
    let t = open_transport(&cli.serial, timeout, cli.baud, char_delay)?;
    // Retry only the lossy real-serial path. The FIFO/emulator transport is
    // lossless and back-pressured, so a timeout there is a genuine hang — pass it
    // straight through rather than waiting out N more timeouts.
    let retries = if cli.serial.serial.is_some() {
        cli.retries
    } else {
        0
    };
    dispatch(&Retry::new(t, retries), cli.command)
}

fn open_transport(
    opts: &SerialOpts,
    timeout: Duration,
    baud: u32,
    char_delay: Duration,
) -> Result<Transport> {
    match (&opts.serial, &opts.serial_in, &opts.serial_out) {
        (Some(p), None, None) => transport::open_path(p, timeout, baud, char_delay),
        (None, Some(i), Some(o)) => transport::open_fifos(i, o, timeout),
        (None, None, None) => Err(Error::NoSerial),
        // clap's `conflicts_with_all` + `requires` exhaust every other shape.
        _ => unreachable!("clap rejects mixed --serial / --serial-in / --serial-out"),
    }
}

fn dispatch<R: Request>(t: &R, cmd: Cmd) -> Result<()> {
    match cmd {
        Cmd::Check => cmd_check(t),
        Cmd::Read { path } => cmd_read(t, &path),
        Cmd::Write { path } => cmd_write(t, &path),
        Cmd::Edit { path, old, new } => cmd_edit(t, &path, &old, &new),
        Cmd::Delete { path } => cmd_delete(t, &path),
        Cmd::ListFiles { prefix } => cmd_list_files(t, prefix.as_deref().unwrap_or("")),
        Cmd::ListModules => cmd_list_modules(t),
        Cmd::Compile { name, new_symbol } => cmd_compile(t, &name, new_symbol),
        Cmd::Load { name } => cmd_load(t, &name),
        Cmd::Unload { name } => cmd_unload(t, &name),
        Cmd::Call { cmd, args } => cmd_call(t, &cmd, args.as_deref().unwrap_or("")),
    }
}

// --- subcommand impls ---

fn cmd_check<R: Request>(t: &R) -> Result<()> {
    let start = Instant::now();
    let version = tools::version(t)?;
    let rtt = start.elapsed();
    if version.is_empty() {
        println!("ok: connected (round-trip {}ms)", rtt.as_millis());
        println!("    warning: device reported no version string — image may lack the");
        println!("    System.Version patch. Variant detection is unavailable; proceed at");
        println!("    your own risk (PO-style unsafe unload may apply).");
    } else {
        println!("ok: {version} (round-trip {}ms)", rtt.as_millis());
    }
    Ok(())
}

fn cmd_read<R: Request>(t: &R, path: &str) -> Result<()> {
    let content = tools::read_file(t, path)?;
    let mut out = std::io::stdout().lock();
    out.write_all(content.as_bytes())?;
    Ok(())
}

fn cmd_write<R: Request>(t: &R, path: &str) -> Result<()> {
    let mut content = String::new();
    std::io::stdin().read_to_string(&mut content)?;
    tools::write_file(t, path, &content)?;
    println!("ok wrote {path} ({} bytes)", content.len());
    Ok(())
}

fn cmd_edit<R: Request>(t: &R, path: &str, old: &str, new: &str) -> Result<()> {
    tools::edit_file(t, path, old, new)?;
    println!("ok edited {path}");
    Ok(())
}

fn cmd_delete<R: Request>(t: &R, path: &str) -> Result<()> {
    tools::delete_file(t, path)?;
    println!("ok deleted {path}");
    Ok(())
}

fn cmd_list_files<R: Request>(t: &R, prefix: &str) -> Result<()> {
    let log = tools::list_files(t, prefix)?;
    print!("{log}");
    Ok(())
}

fn cmd_list_modules<R: Request>(t: &R) -> Result<()> {
    let log = tools::list_modules(t)?;
    print!("{log}");
    Ok(())
}

fn cmd_compile<R: Request>(t: &R, name: &str, new_symbol: bool) -> Result<()> {
    let r = tools::compile_module(t, name, new_symbol)?;
    print_log(&r.output);
    if r.failed {
        return Err(Error::CompileFailed);
    }
    Ok(())
}

fn cmd_load<R: Request>(t: &R, name: &str) -> Result<()> {
    tools::load_module(t, name)?;
    println!("ok loaded {name}");
    Ok(())
}

fn cmd_unload<R: Request>(t: &R, name: &str) -> Result<()> {
    let log = tools::unload_module(t, name)?;
    println!("ok unloaded {name}");
    for line in log.trim().lines() {
        println!("  {line}");
    }
    Ok(())
}

fn cmd_call<R: Request>(t: &R, cmd: &str, args: &str) -> Result<()> {
    let r = tools::run_command(t, cmd, args)?;
    print_log(&r.log);
    r.outcome()
}

/// Print a tool log to stdout, ensuring a trailing newline when non-empty
/// (so the next stderr line doesn't get glued onto the last log line).
fn print_log(log: &str) {
    if log.is_empty() {
        return;
    }
    print!("{log}");
    if !log.ends_with('\n') {
        println!();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn cli_definition_is_consistent() {
        Cli::command().debug_assert();
    }

    /// `open_transport`'s `unreachable!` relies on clap rejecting every mixed
    /// or half-paired serial form — pin that here.
    #[test]
    fn serial_forms_are_exclusive_and_paired() {
        let parse = |args: &[&str]| Cli::try_parse_from(args);
        assert!(parse(&["oat", "--serial", "p", "check"]).is_ok());
        assert!(parse(&["oat", "--serial-in", "a", "--serial-out", "b", "check"]).is_ok());
        assert!(parse(&["oat", "check"]).is_ok()); // NoSerial is reported later, with context
        assert!(parse(&[
            "oat",
            "--serial",
            "p",
            "--serial-in",
            "a",
            "--serial-out",
            "b",
            "check"
        ])
        .is_err());
        assert!(parse(&["oat", "--serial-in", "a", "check"]).is_err());
        assert!(parse(&["oat", "--serial-out", "b", "check"]).is_err());
    }
}
