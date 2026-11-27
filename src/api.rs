use std::future::Future;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::Value;

use crate::config::{self, Priority};
use crate::connection::Request;
use crate::errors::{Error, Reason, ResponseError, Result};
use crate::models::{Notification, encode_json};
use crate::pool::{DEFAULT_POOL_SIZE, Pool, Transport};
use crate::tls::create_tls_config;

pub const SEND_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SendOptions {
    pub apns_id: Option<String>,
    pub expiration: Option<u64>,
    pub priority: Priority,
    pub topic: Option<String>,
    pub collapse_id: Option<String>,
    pub time_sensitive: bool,
}

/// Defines the APIs a Client implementation must provide.
pub trait Client: Send + Sync {
    /// Send the notification to the device identified by the token.
    /// For details about the parameters, refer to Apple's APNS documentation.
    fn send_notification(
        &self,
        token: &str,
        notification: &Notification,
        options: &SendOptions,
    ) -> impl Future<Output = Result<Option<String>>> + Send;

    /// Close the client. This should only be called once and the client
    /// cannot be used anymore afterwards.
    fn close(&self) -> impl Future<Output = ()> + Send;
}

/// Defines the API a Target must implement.
pub trait Target {
    type Client: Client;

    fn create_client(&self) -> impl Future<Output = Result<Self::Client>> + Send;
}

/// Target to talk to APNS Servers. Most use cases are covered by the convenience
/// constructors provided, so instantiating this struct directly is rarely required.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Server {
    pub client_cert_path: PathBuf,
    pub host: String,
    pub port: u16,
    pub ca_file: Option<PathBuf>,
    pub pool_size: usize,
}

impl Server {
    pub fn new(client_cert_path: impl Into<PathBuf>, host: impl Into<String>) -> Self {
        Server {
            client_cert_path: client_cert_path.into(),
            host: host.into(),
            port: config::DEFAULT_PORT,
            ca_file: None,
            pool_size: DEFAULT_POOL_SIZE,
        }
    }

    /// Returns the server configured to connect to production APNS.
    pub fn production(client_cert_path: impl Into<PathBuf>) -> Self {
        Server::new(client_cert_path, config::PRODUCTION_HOST)
    }

    /// Returns the server configured to connect to production APNS using the
    /// alternative port.
    pub fn production_alt_port(client_cert_path: impl Into<PathBuf>) -> Self {
        Server::production(client_cert_path).with_port(config::ALT_PORT)
    }

    /// Returns the server configured to connect to development APNS.
    pub fn development(client_cert_path: impl Into<PathBuf>) -> Self {
        Server::new(client_cert_path, config::SANDBOX_HOST)
    }

    /// Returns the server configured to connect to development APNS using the
    /// alternative port.
    pub fn development_alt_port(client_cert_path: impl Into<PathBuf>) -> Self {
        Server::development(client_cert_path).with_port(config::ALT_PORT)
    }

    pub fn with_port(mut self, port: u16) -> Self {
        self.port = port;
        self
    }

    pub fn with_ca_file(mut self, ca_file: impl Into<PathBuf>) -> Self {
        self.ca_file = Some(ca_file.into());
        self
    }

    pub fn with_pool_size(mut self, pool_size: usize) -> Self {
        self.pool_size = pool_size;
        self
    }

    pub fn origin(&self) -> String {
        if self.host.contains(':') {
            format!("https://[{}]:{}", self.host, self.port)
        } else {
            format!("https://{}:{}", self.host, self.port)
        }
    }
}

impl Target for Server {
    type Client = Apns<Pool>;

    async fn create_client(&self) -> Result<Apns<Pool>> {
        let client_cert_path = self.client_cert_path.clone();
        let ca_file = self.ca_file.clone();
        // Loading certificates blocks
        let tls = tokio::task::spawn_blocking(move || {
            create_tls_config(Some(&client_cert_path), ca_file.as_deref())
        })
        .await
        .map_err(|e| Error::Tls(e.to_string()))??;
        let pool = Pool::connect(&self.origin(), self.pool_size, Some(tls)).await?;
        Ok(Apns::new(pool))
    }
}

#[derive(Debug, Clone)]
pub struct Apns<T = Pool> {
    transport: T,
}

impl<T> Apns<T> {
    pub fn new(transport: T) -> Self {
        Apns { transport }
    }

    pub fn transport(&self) -> &T {
        &self.transport
    }
}

impl<T: Transport> Client for Apns<T> {
    async fn send_notification(
        &self,
        token: &str,
        notification: &Notification,
        options: &SendOptions,
    ) -> Result<Option<String>> {
        let request = notification_request(token, notification, options)?;
        let size = request.body().len();
        let limit = notification.push_type.max_payload_size();
        if size > limit {
            return Err(Error::PayloadTooLarge {
                push_type: notification.push_type,
                size,
                limit,
            });
        }
        let response = self.transport.post(&request).await?;
        if response.code != 200 {
            return Err(ResponseError {
                status: response.code,
                reason: Reason::parse(response.reason()),
                apns_id: response.apns_id().map(str::to_owned),
            }
            .into());
        }
        Ok(response.apns_id().map(str::to_owned))
    }

    async fn close(&self) {
        self.transport.close().await
    }
}

// interruption-level is part of the payload, not a header
fn payload(notification: &Notification, options: &SendOptions) -> Value {
    let mut payload = notification.to_json();
    if options.time_sensitive {
        if let Some(aps) = payload.get_mut("aps").and_then(Value::as_object_mut) {
            aps.insert("interruption-level".into(), "time-sensitive".into());
        }
    }
    payload
}

fn notification_request(
    token: &str,
    notification: &Notification,
    options: &SendOptions,
) -> Result<Request> {
    let valid_token = !token.is_empty()
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    if !valid_token {
        return Err(Error::InvalidRequest(format!(
            "invalid device token {token:?}"
        )));
    }
    let priority = options.priority.to_string();
    let expiration = options.expiration.map(|e| e.to_string());
    let mut header = vec![
        ("apns-priority", priority.as_str()),
        ("apns-push-type", notification.push_type.as_str()),
    ];
    let optional = [
        ("apns-id", options.apns_id.as_deref()),
        ("apns-expiration", expiration.as_deref()),
        ("apns-topic", options.topic.as_deref()),
        ("apns-collapse-id", options.collapse_id.as_deref()),
    ];
    header.extend(
        optional
            .into_iter()
            .filter_map(|(name, value)| Some((name, value?))),
    );
    let path = format!("/3/device/{token}");
    Ok(
        Request::new(&path, &header, &payload(notification, options))?
            .with_timeout(Some(SEND_TIMEOUT)),
    )
}

/// Target to talk to a local simulator. Provide the device ID of your simulator
/// and the app ID of your app as arguments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Simulator {
    pub device_id: String,
    pub app_id: String,
}

impl Simulator {
    pub fn new(device_id: impl Into<String>, app_id: impl Into<String>) -> Self {
        Simulator {
            device_id: device_id.into(),
            app_id: app_id.into(),
        }
    }
}

impl Target for Simulator {
    type Client = Simulator;

    async fn create_client(&self) -> Result<Simulator> {
        Ok(self.clone())
    }
}

impl Client for Simulator {
    async fn send_notification(
        &self,
        _token: &str,
        notification: &Notification,
        options: &SendOptions,
    ) -> Result<Option<String>> {
        let workspace = tempfile::tempdir()?;
        let path = workspace.path().join("notification.apns");
        tokio::fs::write(&path, encode_json(&payload(notification, options))).await?;
        let status = tokio::process::Command::new("xcrun")
            .args(["simctl", "push", &self.device_id, &self.app_id])
            .arg(&path)
            .status()
            .await
            .map_err(|e| Error::Simulator(format!("cannot run xcrun: {e}")))?;
        if !status.success() {
            return Err(Error::Simulator(format!("xcrun failed: {status}")));
        }
        Ok(None)
    }

    async fn close(&self) {}
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use serde_json::json;

    use super::*;
    use crate::connection::Response;
    use crate::models::{Alert, PushType};

    #[derive(Default)]
    struct NullTransport {
        requests: Mutex<Vec<Request>>,
    }

    impl Transport for NullTransport {
        async fn post(&self, request: &Request) -> Result<Response> {
            self.requests.lock().unwrap().push(request.clone());
            Err(Error::Blocked)
        }

        async fn close(&self) {}
    }

    struct FixedTransport(u16, &'static str, &'static [u8]);

    impl Transport for FixedTransport {
        async fn post(&self, _: &Request) -> Result<Response> {
            let mut header = http::HeaderMap::new();
            header.insert("apns-id", self.1.parse().unwrap());
            Response::new(self.0, header, self.2)
        }

        async fn close(&self) {}
    }

    // body becomes {"aps":{"alert":{"body":"<body>"}}} so there's a fixed 29 byte overhead
    #[tokio::test]
    async fn request_too_large() {
        let cases = [
            (PushType::Alert, 4067, true),
            (PushType::Alert, 4068, false),
            (PushType::Alert, 5091, false),
            (PushType::Alert, 5092, false),
            (PushType::Background, 4067, true),
            (PushType::Background, 4068, false),
            (PushType::Background, 5091, false),
            (PushType::Background, 5092, false),
            (PushType::Voip, 4067, true),
            (PushType::Voip, 4068, true),
            (PushType::Voip, 5091, true),
            (PushType::Voip, 5092, false),
            (PushType::Complication, 4067, true),
            (PushType::Complication, 4068, false),
            (PushType::Complication, 5091, false),
            (PushType::Complication, 5092, false),
            (PushType::FileProvider, 4067, true),
            (PushType::FileProvider, 4068, false),
            (PushType::FileProvider, 5091, false),
            (PushType::FileProvider, 5092, false),
            (PushType::Mdm, 4067, true),
            (PushType::Mdm, 4068, false),
            (PushType::Mdm, 5091, false),
            (PushType::Mdm, 5092, false),
        ];
        for (push_type, inner_body_size, allowed) in cases {
            let client = Apns::new(NullTransport::default());
            let notification = Notification {
                push_type,
                ..Notification::new(Alert::new("a".repeat(inner_body_size)))
            };
            let result = client
                .send_notification("token", &notification, &SendOptions::default())
                .await;
            if allowed {
                assert!(
                    matches!(result, Err(Error::Blocked)),
                    "{push_type} {inner_body_size}: {result:?}"
                );
                assert_eq!(client.transport().requests.lock().unwrap().len(), 1);
            } else {
                assert!(
                    matches!(result, Err(Error::PayloadTooLarge { size, .. }) if size == inner_body_size + 29),
                    "{push_type} {inner_body_size}: {result:?}"
                );
                assert!(client.transport().requests.lock().unwrap().is_empty());
            }
        }
    }

    #[test]
    fn minimal_request() {
        let notification = Notification::new(Alert::new("body"));
        let request = notification_request("42", &notification, &SendOptions::default()).unwrap();
        assert_eq!(request.path(), "/3/device/42");
        assert_eq!(request.body(), br#"{"aps":{"alert":{"body":"body"}}}"#);
        assert_eq!(request.deadline().unwrap().1, "timeout");
        assert!(request.time_left().unwrap().unwrap() <= SEND_TIMEOUT);
    }

    #[test]
    fn time_sensitive() {
        let notification = Notification::new(Alert::new("body"));
        let options = SendOptions {
            time_sensitive: true,
            ..SendOptions::default()
        };
        assert_eq!(
            payload(&notification, &options),
            json!({"aps": {"alert": {"body": "body"}, "interruption-level": "time-sensitive"}})
        );
    }

    #[test]
    fn invalid_tokens() {
        let notification = Notification::new(Alert::new("body"));
        for token in ["", "../../admin", "a/b", "a?b=c", "a b", "token#"] {
            assert!(
                matches!(
                    notification_request(token, &notification, &SendOptions::default()),
                    Err(Error::InvalidRequest(_))
                ),
                "{token:?}"
            );
        }
    }

    #[tokio::test]
    async fn rejected_notification() {
        let client = Apns::new(FixedTransport(
            400,
            "abc",
            br#"{"reason":"BadDeviceToken"}"#,
        ));
        let notification = Notification::new(Alert::new("body"));
        match client
            .send_notification("42", &notification, &SendOptions::default())
            .await
        {
            Err(Error::Response(error)) => {
                assert_eq!(error.status, 400);
                assert_eq!(error.reason, Reason::BadDeviceToken);
                assert_eq!(error.apns_id.as_deref(), Some("abc"));
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[tokio::test]
    async fn accepted_notification() {
        let client = Apns::new(FixedTransport(200, "abc", b""));
        let notification = Notification::new(Alert::new("body"));
        let apns_id = client
            .send_notification("42", &notification, &SendOptions::default())
            .await
            .unwrap();
        assert_eq!(apns_id.as_deref(), Some("abc"));
    }

    #[test]
    fn servers() {
        let production = Server::production("cert.pem");
        assert_eq!(production.origin(), "https://api.push.apple.com:443");
        assert_eq!(production.pool_size, 2);
        assert_eq!(
            Server::production_alt_port("cert.pem").origin(),
            "https://api.push.apple.com:2197"
        );
        assert_eq!(
            Server::development("cert.pem").origin(),
            "https://api.development.push.apple.com:443"
        );
        assert_eq!(
            Server::development_alt_port("cert.pem").origin(),
            "https://api.development.push.apple.com:2197"
        );
        assert_eq!(
            Server::new("cert.pem", "::1").with_port(8443).origin(),
            "https://[::1]:8443"
        );
    }
}
