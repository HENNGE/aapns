//! Example of using aapns::connection::Connection (low-level API)

use std::path::PathBuf;
use std::time::Duration;

use aapns::connection::{Connection, Request};
use aapns::tls::create_tls_config;
use clap::{ArgGroup, Parser};
use serde_json::json;
use tracing::info;

#[derive(Parser)]
#[command(group(ArgGroup::new("target").required(true).args(["local", "sandbox", "prod"])))]
struct Args {
    #[arg(long)]
    verbose: bool,
    #[arg(long)]
    local: bool,
    #[arg(long)]
    sandbox: bool,
    #[arg(long)]
    prod: bool,
    #[arg(long)]
    alt_port: bool,
    #[arg(long)]
    localized: bool,
    #[arg(long)]
    client_cert_path: PathBuf,
    device_token: String,
    messages: Vec<String>,
}

#[tokio::main]
async fn main() -> aapns::errors::Result<()> {
    let args = Args::parse();
    tracing_subscriber::fmt()
        .with_max_level(if args.verbose {
            tracing::Level::DEBUG
        } else {
            tracing::Level::INFO
        })
        .init();

    let (host, ca_file) = if args.local {
        ("localhost", Some(PathBuf::from("tests/certs/ca.pem")))
    } else if args.sandbox {
        (aapns::config::SANDBOX_HOST, None)
    } else {
        (aapns::config::PRODUCTION_HOST, None)
    };
    let tls = create_tls_config(Some(&args.client_cert_path), ca_file.as_deref())?;
    let port = if args.alt_port { 2197 } else { 443 };
    let key = if args.localized { "loc-key" } else { "body" };

    let connection = Connection::connect(&format!("https://{host}:{port}"), Some(tls)).await?;
    let mut tasks = Vec::new();
    for text in &args.messages {
        let request = Request::new(
            &format!("/3/device/{}", args.device_token),
            &[("apns-priority", "5"), ("apns-push-type", "alert")],
            &json!({"aps": {"alert": {key: text}}}),
        )?
        .with_timeout(Some(Duration::from_secs(10)));
        info!("Sleeping a bit");
        tokio::time::sleep(Duration::from_secs(1)).await;
        let connection = connection.clone();
        tasks.push(tokio::spawn(async move {
            info!("{request:?}");
            match connection.post(&request).await {
                Ok(response) => info!("{response:?}"),
                Err(error) => info!("Failed with {error}"),
            }
        }));
    }
    for task in tasks {
        let _ = task.await;
    }
    connection.close().await;
    Ok(())
}
