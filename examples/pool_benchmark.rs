//! Example of using `aapns::pool::Pool` (mid level API), and a performance
//! test for the connection pool.
//!
//! Expects a local server on port 2197, for example:
//!
//! ```sh
//! cargo run --release --features cli --example mock_server -- --port 2197 &
//! cargo run --release --features cli --example pool_benchmark -- 2000
//! ```
//!
//! Be careful if you target the sandbox or production server, Apple won't
//! like the flood. Consider limiting the number of requests to ~100 or so.

use std::time::Duration;

use aapns::connection::Request;
use aapns::errors::Error;
use aapns::pool::Pool;
use aapns::tls::create_tls_config;
use clap::Parser;
use serde_json::json;
use tracing::{info, warn};

#[derive(Parser)]
struct Args {
    /// Number of requests
    #[arg(default_value_t = 2000)]
    count: usize,
}

async fn one_request(pool: Pool, i: usize) {
    let request = Request::new(
        &format!("/3/device/aaa-{i}"),
        &[("foo", "bar")],
        &json!({"baz": 42}),
    )
    .unwrap()
    .with_timeout(Some(Duration::from_secs_f64((i as f64 * 0.1).min(10.0))));
    match pool.post(&request).await {
        Ok(_) | Err(Error::Timeout(_) | Error::Blocked | Error::Closed(_)) => {}
        Err(error) => warn!("{error}"),
    }
}

#[tokio::main]
async fn main() -> aapns::errors::Result<()> {
    tracing_subscriber::fmt().init();
    let args = Args::parse();
    let tls = create_tls_config(
        Some("tests/certs/client.pem".as_ref()),
        Some("tests/certs/ca.pem".as_ref()),
    )?;
    let pool = Pool::connect("https://localhost:2197", 2, Some(tls)).await?;

    let monitor = {
        let pool = pool.clone();
        tokio::spawn(async move {
            loop {
                info!("Pool {pool}");
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        })
    };
    tokio::time::sleep(Duration::from_millis(100)).await;
    let tasks: Vec<_> = (0..args.count)
        .map(|i| tokio::spawn(one_request(pool.clone(), i)))
        .collect();
    for task in tasks {
        let _ = task.await;
    }
    pool.close().await;
    monitor.abort();
    info!("Pool {pool}");
    Ok(())
}
