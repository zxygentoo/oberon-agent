//! Retry decorator for the `Request` seam.
//!
//! A real UART desyncs a fraction of requests on Project Oberon (~13 % at the
//! default char-delay; ~0 % on Extended Oberon): the device's cooperative poll
//! occasionally misses the request frame's first (`sync`) byte when an
//! `Oberon.Loop` stall — GC, the other installed tasks — outlasts the inter-byte
//! gap, so the frame is dropped and the host times out (or reads a misframed
//! reply). Char-delay reduces this but can't remove it, because a loop stall can
//! exceed any fixed gap. See REAL-SERIAL.md for the measured curve.
//!
//! The device self-recovers per frame — its bounded `Rec` clears `rxOk`, the
//! handler bails without committing or replying, and the next poll resyncs on a
//! fresh `sync` — so simply re-sending the request succeeds. Measured on
//! hardware: one retry takes a back-to-back command stream from ~82 % to ~97 %,
//! two retries to 100 %.
//!
//! Re-sending is safe for every opcode here because the lossy direction is
//! device RX only. A desync drops a byte *before* the device acts, so nothing
//! was committed (`DoPut`/`DoEdit`/`DoCall` all check `rxOk` before
//! `Files.Register` / the splice / running the executor). The host's own RX is
//! OS-buffered and not subject to single-byte overrun, so the one case that
//! would make a re-send non-idempotent — the device acts, then its *reply* is
//! lost, and a retried `EDIT` re-runs into `NoMatch` (or a retried delete into
//! "deleting failed") — does not occur on this link.

use crate::error::{Error, Result};
use crate::protocol::{Request, Response};

/// Wraps a `Request`, re-sending on a transport desync up to `retries` extra
/// attempts (so `retries + 1` total). `retries == 0` is a transparent
/// pass-through — used for the lossless FIFO/emulator path.
pub struct Retry<R> {
    inner: R,
    retries: u32,
}

impl<R> Retry<R> {
    pub fn new(inner: R, retries: u32) -> Self {
        Self { inner, retries }
    }
}

/// Only the transport desync signatures are retried — a timed-out or misframed
/// reply, the marks of a dropped request frame. Tool-level statuses never reach
/// here (they come back as `Ok(Response)`); genuine line failures (`Eof`, `Io`,
/// open errors) are not transient, so they fail fast.
fn retriable(e: &Error) -> bool {
    matches!(e, Error::Timeout { .. } | Error::BadSync { .. })
}

impl<R: Request> Request for Retry<R> {
    fn send(&self, frame: &[u8]) -> Result<Response> {
        let mut attempt = 0;
        loop {
            match self.inner.send(frame) {
                Err(e) if retriable(&e) && attempt < self.retries => attempt += 1,
                other => return other,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::Status;
    use std::cell::Cell;

    /// A fake seam that fails (with a chosen error) its first `fail_then` calls,
    /// then succeeds. Counts total calls so tests can assert the attempt budget.
    struct Flaky {
        fail_then: u32,
        err: fn() -> Error,
        calls: Cell<u32>,
    }

    impl Flaky {
        fn new(fail_then: u32, err: fn() -> Error) -> Self {
            Self {
                fail_then,
                err,
                calls: Cell::new(0),
            }
        }
    }

    impl Request for Flaky {
        fn send(&self, _frame: &[u8]) -> Result<Response> {
            let n = self.calls.get();
            self.calls.set(n + 1);
            if n < self.fail_then {
                Err((self.err)())
            } else {
                Ok(Response {
                    status: Status::Ok,
                    payload: Vec::new(),
                })
            }
        }
    }

    fn timeout() -> Error {
        Error::Timeout {
            secs: 1.0,
            got: 0,
            want: 1,
        }
    }
    fn bad_sync() -> Error {
        Error::BadSync {
            got: 0,
            expected: 0x5A,
        }
    }

    #[test]
    fn succeeds_on_first_try_makes_one_call() {
        let f = Flaky::new(0, timeout);
        let r = Retry::new(&f, 2);
        assert!(r.send(b"x").unwrap().ok());
        assert_eq!(f.calls.get(), 1);
    }

    #[test]
    fn recovers_within_the_retry_budget() {
        // Two timeouts then success; 2 retries (3 attempts) covers it.
        let f = Flaky::new(2, timeout);
        let r = Retry::new(&f, 2);
        assert!(r.send(b"x").unwrap().ok());
        assert_eq!(f.calls.get(), 3);
    }

    #[test]
    fn bad_sync_is_also_retried() {
        let f = Flaky::new(1, bad_sync);
        let r = Retry::new(&f, 2);
        assert!(r.send(b"x").unwrap().ok());
        assert_eq!(f.calls.get(), 2);
    }

    #[test]
    fn gives_up_after_exhausting_retries() {
        // Three failures but only 2 retries (3 attempts) — the 3rd attempt also
        // fails, so the error propagates after exactly 3 calls.
        let f = Flaky::new(3, timeout);
        let r = Retry::new(&f, 2);
        assert!(matches!(r.send(b"x"), Err(Error::Timeout { .. })));
        assert_eq!(f.calls.get(), 3);
    }

    #[test]
    fn zero_retries_is_a_single_attempt() {
        let f = Flaky::new(1, timeout);
        let r = Retry::new(&f, 0);
        assert!(matches!(r.send(b"x"), Err(Error::Timeout { .. })));
        assert_eq!(f.calls.get(), 1);
    }

    #[test]
    fn non_retriable_error_fails_fast() {
        // Eof is a genuine line failure, not a desync — no retry.
        let f = Flaky::new(5, || Error::Eof);
        let r = Retry::new(&f, 3);
        assert!(matches!(r.send(b"x"), Err(Error::Eof)));
        assert_eq!(f.calls.get(), 1);
    }
}
