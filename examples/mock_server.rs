//! Runs the mock APNS server used by the functional tests

#[path = "../tests/common/server.rs"]
mod server;

use clap::Parser;
use server::{Flavor, MockServer};

#[derive(Parser)]
struct Args {
    #[arg(long, default_value_t = 2197)]
    port: u16,
    #[arg(long, default_value = "ok")]
    flavor: Flavor,
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt().init();
    let args = Args::parse();
    let server = MockServer::bind(args.flavor, &format!("127.0.0.1:{}", args.port)).await?;
    tracing::info!("Serving {:?} on {}", args.flavor, server.origin());
    server.wait().await;
    Ok(())
}
