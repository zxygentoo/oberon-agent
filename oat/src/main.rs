//! oat — oberon-agent-tool — stateless CLI driver for AgentTool.Mod on a
//! live Project Oberon or Extended Oberon system. See `skill/oberon-agent/`
//! for the agent-side rules.
//!
//! This file is only the process contract: run the CLI, prefix any error,
//! map it to the documented exit code. Everything else lives in cli.rs.

#![forbid(unsafe_code)]

mod cli;
mod error;
mod protocol;
mod retry;
mod text;
mod tools;
mod transport;

fn main() {
    let code = match cli::run() {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("oat: error: {e}");
            e.exit_code()
        }
    };
    std::process::exit(code);
}
