//! Command line client for sending test notifications.

use std::path::PathBuf;
use std::process::ExitCode;

use aapns::api::{Client, SendOptions, Server, Simulator, Target};
use aapns::config::{self, Priority};
use aapns::models::{Alert, Notification};
use clap::{Args, Parser, Subcommand, ValueEnum};

/// Send push notifications through APNs or to a simulator.
#[derive(Parser)]
#[command(name = "aapns", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Send a notification through APNs.
    Server(ServerArgs),
    /// Send a notification to a simulator with `xcrun simctl push`.
    Simulator(SimulatorArgs),
}

#[derive(Clone, Copy, ValueEnum)]
enum Origin {
    /// Production APNs
    Prod,
    /// Development APNs
    Sandbox,
    /// A local test server, see the mock_server example
    Local,
}

#[derive(Args)]
struct ServerArgs {
    /// Device token
    token: String,
    /// Alert body
    body: String,
    /// Alert title
    #[arg(long)]
    title: Option<String>,
    /// PEM file with the client certificate and its private key
    #[arg(long, env = "CLIENT_CERT_PATH")]
    client_cert_path: PathBuf,
    #[arg(long, value_enum, default_value_t = Origin::Sandbox)]
    server: Origin,
    /// Use the alternative port, 2197
    #[arg(long, conflicts_with = "port")]
    alt_port: bool,
    /// Use this port
    #[arg(long)]
    port: Option<u16>,
    /// Additional CA certificates to trust [default for --server=local: tests/certs/ca.pem]
    #[arg(long)]
    ca_file: Option<PathBuf>,
    #[command(flatten)]
    options: Options,
}

#[derive(Args)]
struct SimulatorArgs {
    /// Simulator device id, or `booted`
    device_id: String,
    /// Bundle id of the app
    app_id: String,
    /// Alert body
    body: String,
    /// Alert title
    #[arg(long)]
    title: Option<String>,
    #[command(flatten)]
    options: Options,
}

#[derive(Args)]
struct Options {
    /// UNIX time after which the notification is no longer valid
    #[arg(long)]
    expiration: Option<u64>,
    /// Send with priority 10 rather than 5
    #[arg(long)]
    immediately: bool,
    /// Topic, usually the bundle id of the app
    #[arg(long)]
    topic: Option<String>,
    #[arg(long)]
    collapse_id: Option<String>,
    #[arg(long)]
    apns_id: Option<String>,
    /// Log debug information
    #[arg(long)]
    verbose: bool,
}

impl Options {
    fn send_options(&self) -> SendOptions {
        SendOptions {
            apns_id: self.apns_id.clone(),
            expiration: self.expiration,
            priority: if self.immediately {
                Priority::Immediately
            } else {
                Priority::Normal
            },
            topic: self.topic.clone(),
            collapse_id: self.collapse_id.clone(),
            time_sensitive: false,
        }
    }
}

fn notification(body: &str, title: Option<&str>) -> Notification {
    let mut alert = Alert::new(body);
    if let Some(title) = title {
        alert = alert.with_title(title);
    }
    Notification::new(alert)
}

async fn send<T: Target>(
    target: T,
    token: &str,
    notification: &Notification,
    options: &SendOptions,
) -> aapns::errors::Result<Option<String>> {
    let client = target.create_client().await?;
    let result = client.send_notification(token, notification, options).await;
    client.close().await;
    result
}

async fn run(command: Command) -> aapns::errors::Result<Option<String>> {
    match command {
        Command::Server(args) => {
            let host = match args.server {
                Origin::Prod => config::PRODUCTION_HOST,
                Origin::Sandbox => config::SANDBOX_HOST,
                Origin::Local => "localhost",
            };
            let port = match (args.port, args.alt_port) {
                (Some(port), _) => port,
                (None, true) => config::ALT_PORT,
                (None, false) => config::DEFAULT_PORT,
            };
            let mut target = Server::new(args.client_cert_path, host).with_port(port);
            target.ca_file = match (args.ca_file, args.server) {
                (Some(ca_file), _) => Some(ca_file),
                (None, Origin::Local) => Some("tests/certs/ca.pem".into()),
                (None, _) => None,
            };
            let notification = notification(&args.body, args.title.as_deref());
            send(
                target,
                &args.token,
                &notification,
                &args.options.send_options(),
            )
            .await
        }
        Command::Simulator(args) => {
            let target = Simulator::new(args.device_id, args.app_id);
            let notification = notification(&args.body, args.title.as_deref());
            send(target, "cli", &notification, &args.options.send_options()).await
        }
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let verbose = match &cli.command {
        Command::Server(args) => args.options.verbose,
        Command::Simulator(args) => args.options.verbose,
    };
    tracing_subscriber::fmt()
        .with_max_level(if verbose {
            tracing::Level::DEBUG
        } else {
            tracing::Level::INFO
        })
        .with_writer(std::io::stderr)
        .init();

    match run(cli.command).await {
        Ok(apns_id) => {
            if let Some(apns_id) = apns_id {
                println!("{apns_id}");
            }
            ExitCode::SUCCESS
        }
        Err(error) => {
            tracing::error!("{error}");
            ExitCode::FAILURE
        }
    }
}
