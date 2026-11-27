//! Runs the mock APNs server used by the functional tests, for trying out the
//! command line client and the other examples locally:
//!
//! ```sh
//! cargo run --features cli --example mock_server -- --port 2197
//! cargo run --features cli -- server 42 "Hello" --server local --alt-port \
//!     --client-cert-path tests/certs/client.pem
//! ```

#[path = "../tests/common/server.rs"]
mod server;

use clap::Parser;
use server::{Flavor, MockServer};

#[derive(Parser)]
struct Args {
    /// Port to listen on
    #[arg(long, default_value_t = 2197)]
    port: u16,
    /// ok, bad-token, terminates-connection, bad-certificate-environment,
    /// no-alpn or rejects-client-certificate
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
