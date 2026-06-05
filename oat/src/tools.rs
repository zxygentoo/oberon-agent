//! High-level operations on the Oberon device.
//!
//! Each function turns one wire round-trip (or two for edit) into a typed
//! `Result`. Generic over `Request` so tests can plug in an in-memory fake.

use crate::error::{Error, Result};
use crate::protocol::{self, Request, Status};
use crate::text::{from_oberon, to_oberon};

/// Output of `compile_module`. The compiler log is always returned; `failed`
/// tells main.rs to exit 1 after printing.
pub struct CompileResult {
    pub output: String,
    pub failed: bool,
}

/// Output of `run_command`. The Log delta is always returned; `outcome()`
/// maps the device status to the command result once the log is printed.
pub struct CallResult {
    pub log: String,
    status: Status,
}

impl CallResult {
    pub fn outcome(&self) -> Result<()> {
        match self.status {
            Status::Ok => Ok(()),
            Status::Trapped => Err(Error::Trapped),
            s => Err(Error::BadStatus { status: s.byte() }),
        }
    }
}

pub fn read_file<R: Request>(req: &R, path: &str) -> Result<String> {
    let r = req.send(&protocol::build_get(path)?)?;
    match r.status {
        Status::Ok => Ok(from_oberon(&r.payload)),
        Status::NotFound => Err(Error::NotFound {
            path: path.to_string(),
        }),
        s => Err(Error::BadStatus { status: s.byte() }),
    }
}

pub fn write_file<R: Request>(req: &R, path: &str, content: &str) -> Result<()> {
    let r = req.send(&protocol::build_put(path, &to_oberon(content))?)?;
    if r.ok() {
        Ok(())
    } else {
        Err(Error::BadStatus {
            status: r.status.byte(),
        })
    }
}

/// Replace a unique occurrence of `old` by `new` in a device file.
///
/// Normally one EDIT round-trip: the device matches OLD inside the file via
/// its Texts piece list and splices NEW in atomically. OLD fragments larger
/// than the device's fixed match buffer take the host-side fallback instead.
pub fn edit_file<R: Request>(req: &R, path: &str, old: &str, new: &str) -> Result<()> {
    let old_dev = to_oberon(old);
    if old_dev.is_empty() || old_dev.len() > protocol::EDIT_OLD_LIMIT {
        return edit_file_via_rw(req, path, old, new);
    }
    let r = req.send(&protocol::build_edit(path, &old_dev, &to_oberon(new))?)?;
    match r.status {
        Status::Ok => Ok(()),
        Status::NotFound => Err(Error::NotFound {
            path: path.to_string(),
        }),
        Status::NoMatch => Err(Error::EditNotFound),
        Status::NotUnique => Err(Error::EditNotUnique {
            count: le_count(&r.payload),
        }),
        s => Err(Error::BadStatus { status: s.byte() }),
    }
}

/// Fallback for fragments EDIT cannot carry: full read-modify-write through
/// GET and PUT. Fragments are normalized through the same LF/CR conversion
/// the wire path applies, so both paths match in the same space.
fn edit_file_via_rw<R: Request>(req: &R, path: &str, old: &str, new: &str) -> Result<()> {
    let old = from_oberon(&to_oberon(old));
    let content = read_file(req, path)?;
    let count = content.matches(&old).count();
    if count == 0 {
        return Err(Error::EditNotFound);
    }
    if count > 1 {
        return Err(Error::EditNotUnique { count });
    }
    write_file(req, path, &content.replacen(&old, new, 1))
}

/// Occurrence count from a `Status::NotUnique` payload (u32 LE); 0 if absent.
fn le_count(payload: &[u8]) -> usize {
    payload
        .get(..4)
        .and_then(|b| b.try_into().ok())
        .map_or(0, |b| u32::from_le_bytes(b) as usize)
}

pub fn delete_file<R: Request>(req: &R, path: &str) -> Result<()> {
    let log = call_log(req, "System.DeleteFiles", path)?;
    // System.Mod writes "<name> deleting" on success, "<name> deleting failed"
    // on res != 0. Match the full phrase so a filename containing "failed"
    // doesn't trip the check.
    if log.contains("deleting failed") {
        return Err(Error::NotFound {
            path: path.to_string(),
        });
    }
    Ok(())
}

pub fn list_files<R: Request>(req: &R, prefix: &str) -> Result<String> {
    call_log(req, "AgentTool.ListFiles", prefix)
}

pub fn list_modules<R: Request>(req: &R) -> Result<String> {
    call_log(req, "AgentTool.ListModules", "")
}

/// Read `System.Version` via `AgentTool.Version` (returns the trimmed log line).
/// Empty when the image lacks the System.Version patch.
pub fn version<R: Request>(req: &R) -> Result<String> {
    Ok(call_log(req, "AgentTool.Version", "")?.trim().to_string())
}

pub fn load_module<R: Request>(req: &R, name: &str) -> Result<()> {
    let log = call_log(req, "AgentTool.Load", name)?;
    if log.trim_start().starts_with("loaded") {
        return Ok(());
    }
    let res = parse_res(&log);
    Err(Error::LoadFailed { res, log })
}

pub fn unload_module<R: Request>(req: &R, name: &str) -> Result<String> {
    // We always pass `/f`. On EO that triggers safe-unload (hide-and-rename when
    // live refs persist, full removal otherwise). On PO, `/f` tokenizes as junk
    // that the System.Free scanner discards — so the module is unloaded the
    // unsafe way (dangling refs are possible; the skill warns about this).
    // The "unloading failed" phrase is EO-only; on PO an in-use refusal goes
    // undetected here, which is why the skill insists on operator permission
    // before any unload on PO.
    let args = format!("{name} /f");
    let log = call_log(req, "System.Free", &args)?;
    if log.contains("unloading failed") {
        return Err(Error::UnloadInUse { log });
    }
    Ok(log)
}

pub fn compile_module<R: Request>(req: &R, name: &str, new_symbol: bool) -> Result<CompileResult> {
    let par = if new_symbol {
        format!("{name}/s")
    } else {
        name.to_string()
    };
    let r = req.send(&protocol::build_call("ORP.Compile", &to_oberon(&par))?)?;
    if !r.ok() {
        return Err(Error::BadStatus {
            status: r.status.byte(),
        });
    }
    let output = from_oberon(&r.payload);
    let failed = output.contains("compilation FAILED");
    Ok(CompileResult { output, failed })
}

pub fn run_command<R: Request>(req: &R, cmd: &str, args: &str) -> Result<CallResult> {
    let r = req.send(&protocol::build_call(cmd, &to_oberon(args))?)?;
    Ok(CallResult {
        log: from_oberon(&r.payload),
        status: r.status,
    })
}

// --- internals ---

fn call_log<R: Request>(req: &R, cmd: &str, args: &str) -> Result<String> {
    let r = req.send(&protocol::build_call(cmd, &to_oberon(args))?)?;
    if !r.ok() {
        return Err(Error::BadStatus {
            status: r.status.byte(),
        });
    }
    Ok(from_oberon(&r.payload))
}

fn parse_res(log: &str) -> Option<i32> {
    log.split_whitespace()
        .find_map(|tok| tok.strip_prefix("res=").and_then(|s| s.parse().ok()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::Response;
    use std::cell::RefCell;
    use std::collections::{HashMap, HashSet};

    /// Closure shape for ad-hoc CALL responses in a test setup: returns
    /// `Some((status, log))` to override the default dispatch, or `None` to
    /// fall through to the built-in behavior.
    type CallHandler = Box<dyn Fn(&str, &[u8]) -> Option<(Status, Vec<u8>)>>;

    /// In-memory fake of the Oberon side.
    struct FakeDevice {
        files: RefCell<HashMap<String, Vec<u8>>>,
        modules: RefCell<HashSet<String>>,
        call: Option<CallHandler>,
    }

    impl FakeDevice {
        fn new() -> Self {
            Self {
                files: RefCell::new(HashMap::new()),
                modules: RefCell::new(
                    ["System", "Oberon", "AgentTool"]
                        .iter()
                        .map(ToString::to_string)
                        .collect(),
                ),
                call: None,
            }
        }

        fn with_file(self, name: &str, body: &[u8]) -> Self {
            self.files.borrow_mut().insert(name.to_string(), body.to_vec());
            self
        }

        fn with_call(mut self, f: impl Fn(&str, &[u8]) -> Option<(Status, Vec<u8>)> + 'static) -> Self {
            self.call = Some(Box::new(f));
            self
        }

        fn dispatch_call(&self, cmd: &str, par: &[u8]) -> (Status, Vec<u8>) {
            if let Some(f) = &self.call {
                if let Some(out) = f(cmd, par) {
                    return out;
                }
            }
            let arg = String::from_utf8_lossy(par).replace('\r', "\n");
            let arg = arg.trim();
            match cmd {
                "System.DeleteFiles" => {
                    if self.files.borrow_mut().remove(arg).is_some() {
                        (Status::Ok, format!("System.DeleteFiles\n{arg} deleting\n").into_bytes())
                    } else {
                        (
                            Status::Ok,
                            format!("System.DeleteFiles\n{arg} deleting failed\n").into_bytes(),
                        )
                    }
                }
                "System.Free" => {
                    // EO syntax: one or more module names then optional /f.
                    let parts: Vec<&str> = arg.split_whitespace().filter(|p| *p != "/f").collect();
                    if let Some(first) = parts.first() {
                        self.modules.borrow_mut().remove(*first);
                        let action = if arg.contains("/f") {
                            "removing from module list"
                        } else {
                            "unloading"
                        };
                        (
                            Status::Ok,
                            format!("System.Free\n{first} {action}\n").into_bytes(),
                        )
                    } else {
                        (Status::Ok, b"System.Free\n".to_vec())
                    }
                }
                "AgentTool.Load" => {
                    self.modules.borrow_mut().insert(arg.to_string());
                    (Status::Ok, format!("loaded {arg}\n").into_bytes())
                }
                "AgentTool.Version" => (
                    Status::Ok,
                    b"Extended Oberon System  AP 1.1.26\n".to_vec(),
                ),
                "AgentTool.ListFiles" => {
                    let mut lines = Vec::new();
                    let mut names: Vec<_> =
                        self.files.borrow().keys().cloned().collect();
                    names.sort();
                    for n in names {
                        let size = self.files.borrow()[&n].len();
                        lines.push(format!("{n}\t{size}\t01.01.24 00:00:00"));
                    }
                    let mut s = lines.join("\n");
                    s.push('\n');
                    (Status::Ok, s.into_bytes())
                }
                "AgentTool.ListModules" => {
                    let mut mods: Vec<_> = self.modules.borrow().iter().cloned().collect();
                    mods.sort();
                    let mut s = mods
                        .into_iter()
                        .map(|m| format!("{m}\t0\t 00001000"))
                        .collect::<Vec<_>>()
                        .join("\n");
                    s.push('\n');
                    (Status::Ok, s.into_bytes())
                }
                _ => (Status::Ok, Vec::new()),
            }
        }

        /// Mirrors `AgentTool`'s `DoEdit`: non-overlapping count, splice at
        /// the first match, occurrence count in the not-unique payload.
        fn dispatch_edit(&self, name: &str, old: &[u8], new: &[u8]) -> Response {
            let empty = |status| Response {
                status,
                payload: Vec::new(),
            };
            if old.is_empty() || old.len() > protocol::EDIT_OLD_LIMIT {
                return empty(Status::Error);
            }
            let Some(content) = self.files.borrow().get(name).cloned() else {
                return empty(Status::NotFound);
            };
            let (mut count, mut first, mut i) = (0u32, None, 0);
            while i + old.len() <= content.len() {
                if &content[i..i + old.len()] == old {
                    count += 1;
                    first.get_or_insert(i);
                    i += old.len(); // non-overlapping, like the device
                } else {
                    i += 1;
                }
            }
            match (count, first) {
                (0, _) | (_, None) => empty(Status::NoMatch),
                (1, Some(at)) => {
                    let mut out = content[..at].to_vec();
                    out.extend_from_slice(new);
                    out.extend_from_slice(&content[at + old.len()..]);
                    self.files.borrow_mut().insert(name.to_string(), out);
                    empty(Status::Ok)
                }
                (n, _) => Response {
                    status: Status::NotUnique,
                    payload: n.to_le_bytes().to_vec(),
                },
            }
        }
    }

    impl Request for FakeDevice {
        fn send(&self, frame: &[u8]) -> Result<Response> {
            assert_eq!(frame[0], protocol::SYNC_REQ, "bad request sync");
            let op = frame[1];
            let nlen = frame[2] as usize;
            let name = std::str::from_utf8(&frame[3..3 + nlen]).unwrap().to_string();
            let mut i = 3 + nlen;
            match op {
                protocol::OP_GET => match self.files.borrow().get(&name) {
                    Some(data) => Ok(Response {
                        status: Status::Ok,
                        payload: data.clone(),
                    }),
                    None => Ok(Response {
                        status: Status::NotFound,
                        payload: Vec::new(),
                    }),
                },
                protocol::OP_PUT => {
                    let dlen = u32::from_le_bytes([
                        frame[i],
                        frame[i + 1],
                        frame[i + 2],
                        frame[i + 3],
                    ]) as usize;
                    i += 4;
                    self.files
                        .borrow_mut()
                        .insert(name, frame[i..i + dlen].to_vec());
                    Ok(Response {
                        status: Status::Ok,
                        payload: Vec::new(),
                    })
                }
                protocol::OP_CALL => {
                    let plen = u32::from_le_bytes([
                        frame[i],
                        frame[i + 1],
                        frame[i + 2],
                        frame[i + 3],
                    ]) as usize;
                    i += 4;
                    let (status, payload) = self.dispatch_call(&name, &frame[i..i + plen]);
                    Ok(Response { status, payload })
                }
                protocol::OP_EDIT => {
                    let olen = u32::from_le_bytes([
                        frame[i],
                        frame[i + 1],
                        frame[i + 2],
                        frame[i + 3],
                    ]) as usize;
                    i += 4;
                    let old = &frame[i..i + olen];
                    i += olen;
                    let nlen = u32::from_le_bytes([
                        frame[i],
                        frame[i + 1],
                        frame[i + 2],
                        frame[i + 3],
                    ]) as usize;
                    i += 4;
                    Ok(self.dispatch_edit(&name, old, &frame[i..i + nlen]))
                }
                _ => panic!("bad op {op}"),
            }
        }
    }

    #[test]
    fn write_then_read_roundtrip() {
        let w = FakeDevice::new();
        write_file(&w, "M.Mod", "MODULE M;\nEND M.\n").unwrap();
        // Stored on the device with CR line separators.
        assert_eq!(w.files.borrow()["M.Mod"], b"MODULE M;\rEND M.\r");
        assert_eq!(read_file(&w, "M.Mod").unwrap(), "MODULE M;\nEND M.\n");
    }

    #[test]
    fn read_missing_file_is_not_found() {
        let err = read_file(&FakeDevice::new(), "X.Mod").unwrap_err();
        assert!(matches!(err, Error::NotFound { .. }));
    }

    #[test]
    fn edit_unique_match_replaces_once() {
        let w = FakeDevice::new().with_file("M.Mod", b"a := 1;\r");
        edit_file(&w, "M.Mod", "a := 1", "a := 2").unwrap();
        assert_eq!(w.files.borrow()["M.Mod"], b"a := 2;\r");
    }

    #[test]
    fn edit_old_not_found() {
        let w = FakeDevice::new().with_file("M.Mod", b"x\r");
        assert!(matches!(
            edit_file(&w, "M.Mod", "zzz", "q"),
            Err(Error::EditNotFound)
        ));
    }

    #[test]
    fn edit_old_not_unique() {
        // Count travels back in the Status::NotUnique payload.
        let w = FakeDevice::new().with_file("M.Mod", b"a a\r");
        assert!(matches!(
            edit_file(&w, "M.Mod", "a", "b"),
            Err(Error::EditNotUnique { count: 2 })
        ));
    }

    #[test]
    fn edit_missing_file_is_not_found() {
        assert!(matches!(
            edit_file(&FakeDevice::new(), "Gone.Mod", "a", "b"),
            Err(Error::NotFound { .. })
        ));
    }

    #[test]
    fn edit_empty_new_deletes_old() {
        let w = FakeDevice::new().with_file("M.Mod", b"keep drop keep\r");
        edit_file(&w, "M.Mod", " drop", "").unwrap();
        assert_eq!(w.files.borrow()["M.Mod"], b"keep keep\r");
    }

    #[test]
    fn edit_multiline_old_matches_in_cr_space() {
        // OLD spanning a line break: LF in the argument must match the CR
        // stored on the device.
        let w = FakeDevice::new().with_file("M.Mod", b"a;\rb;\rc;\r");
        edit_file(&w, "M.Mod", "a;\nb;", "d;").unwrap();
        assert_eq!(w.files.borrow()["M.Mod"], b"d;\rc;\r");
    }

    #[test]
    fn edit_long_old_falls_back_to_get_put() {
        // OLD beyond EDIT_OLD_LIMIT takes the host-side read-modify-write
        // path; a line break inside OLD still matches (both paths normalize).
        let long = format!("{}\n{}", "x".repeat(600), "y".repeat(600));
        let content = format!("head\n{long}\ntail\n");
        let w = FakeDevice::new();
        write_file(&w, "Big.Txt", &content).unwrap();
        edit_file(&w, "Big.Txt", &long, "z").unwrap();
        assert_eq!(read_file(&w, "Big.Txt").unwrap(), "head\nz\ntail\n");
    }

    #[test]
    fn edit_old_at_limit_takes_wire_path() {
        // Exactly EDIT_OLD_LIMIT device bytes still fits the device buffer.
        let old = "x".repeat(protocol::EDIT_OLD_LIMIT);
        let w = FakeDevice::new();
        write_file(&w, "Lim.Txt", &format!("a{old}b")).unwrap();
        edit_file(&w, "Lim.Txt", &old, "-").unwrap();
        assert_eq!(read_file(&w, "Lim.Txt").unwrap(), "a-b");
    }

    #[test]
    fn delete_present_and_absent() {
        let w = FakeDevice::new().with_file("M.Mod", b"x\r");
        delete_file(&w, "M.Mod").unwrap();
        assert!(!w.files.borrow().contains_key("M.Mod"));
        assert!(matches!(
            delete_file(&w, "Gone.Mod"),
            Err(Error::NotFound { .. })
        ));
    }

    #[test]
    fn list_files_returns_tsv() {
        let w = FakeDevice::new()
            .with_file("A.Mod", b"xx")
            .with_file("B.Mod", b"yyy");
        let out = list_files(&w, "").unwrap();
        assert!(out.contains("A.Mod\t2"));
        assert!(out.contains("B.Mod\t3"));
    }

    #[test]
    fn list_modules_contains_seeded_modules() {
        let out = list_modules(&FakeDevice::new()).unwrap();
        assert!(out.contains("AgentTool\t"));
        assert!(out.lines().filter(|l| !l.trim().is_empty()).count() >= 3);
    }

    #[test]
    fn version_returns_system_version_string() {
        let v = version(&FakeDevice::new()).unwrap();
        assert!(v.contains("Extended Oberon"));
    }

    #[test]
    fn version_handles_empty_payload() {
        let w = FakeDevice::new().with_call(|cmd, _| {
            (cmd == "AgentTool.Version").then(|| (Status::Ok, Vec::new()))
        });
        assert_eq!(version(&w).unwrap(), "");
    }

    #[test]
    fn load_module_marks_success() {
        load_module(&FakeDevice::new(), "Foo").unwrap();
    }

    #[test]
    fn load_module_failure_parses_res() {
        let w = FakeDevice::new().with_call(|cmd, _| {
            if cmd == "AgentTool.Load" {
                Some((Status::Ok, b"AgentTool.Load\n  res=2\n".to_vec()))
            } else {
                None
            }
        });
        match load_module(&w, "Bad") {
            Err(Error::LoadFailed { res: Some(2), .. }) => {}
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn unload_in_use_is_detected() {
        let w = FakeDevice::new().with_call(|cmd, _| {
            if cmd == "System.Free" {
                Some((
                    Status::Ok,
                    b"System.Free\n  X unloading failed, try /f option\n".to_vec(),
                ))
            } else {
                None
            }
        });
        assert!(matches!(
            unload_module(&w, "X"),
            Err(Error::UnloadInUse { .. })
        ));
    }

    #[test]
    fn compile_returns_raw_log_and_failed_flag() {
        let w = FakeDevice::new().with_call(|cmd, _| {
            if cmd == "ORP.Compile" {
                Some((
                    Status::Ok,
                    b"  compiling M\n  pos 5 undef\ncompilation FAILED\n".to_vec(),
                ))
            } else {
                None
            }
        });
        let r = compile_module(&w, "M.Mod", false).unwrap();
        assert!(r.failed);
        assert!(r.output.contains("undef"));
    }

    #[test]
    fn compile_new_symbol_passes_slash_s() {
        use std::rc::Rc;
        let saw_slash_s = Rc::new(std::cell::Cell::new(false));
        let saw = saw_slash_s.clone();
        let w = FakeDevice::new().with_call(move |cmd, par| {
            if cmd == "ORP.Compile" {
                saw.set(String::from_utf8_lossy(par).contains("M.Mod/s"));
                Some((Status::Ok, b"  compiling M new symbol file  10 4 ABCD\n".to_vec()))
            } else {
                None
            }
        });
        let r = compile_module(&w, "M.Mod", true).unwrap();
        assert!(!r.failed);
        assert!(saw_slash_s.get());
    }

    #[test]
    fn run_command_reports_trapped_status() {
        let w = FakeDevice::new().with_call(|_, _| Some((Status::Trapped, b"trap log\n".to_vec())));
        let r = run_command(&w, "Bad.Cmd", "").unwrap();
        assert!(matches!(r.outcome(), Err(Error::Trapped)));
        assert_eq!(r.log, "trap log\n");
    }

    #[test]
    fn run_command_ok_status_maps_to_ok() {
        let r = run_command(&FakeDevice::new(), "Any.Cmd", "").unwrap();
        assert!(r.outcome().is_ok());
    }

    #[test]
    fn compile_non_ok_status_is_bad_status() {
        let w = FakeDevice::new()
            .with_call(|cmd, _| (cmd == "ORP.Compile").then(|| (Status::Error, Vec::new())));
        assert!(matches!(
            compile_module(&w, "M.Mod", false),
            Err(Error::BadStatus { status }) if status == Status::Error.byte()
        ));
    }

    // Guards the full-phrase match in delete_file: a file *named* "failed"
    // must not be misread as a deletion failure.
    #[test]
    fn delete_filename_containing_failed_is_not_misread() {
        let w = FakeDevice::new().with_file("failed.Mod", b"x");
        delete_file(&w, "failed.Mod").unwrap();
        assert!(!w.files.borrow().contains_key("failed.Mod"));
    }
}
