use std::fmt;
use std::future::{Future, poll_fn};
use std::io;
use std::net::Ipv6Addr;
use std::pin::pin;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::task::{Context, Poll, Waker};
use std::time::{Duration, Instant, SystemTime};

use bytes::Bytes;
use h2::SendStream;
use h2::client::SendRequest;
use http::header::{HeaderMap, HeaderName, HeaderValue};
use http::uri::{Authority, PathAndQuery, Uri};
use rustls::ClientConfig;
use rustls::pki_types::ServerName;
use serde::Serialize;
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::TcpStream;
use tokio::sync::{Notify, watch};
use tokio::task::JoinHandle;
use tokio_rustls::TlsConnector;

use crate::errors::{Error, Result};
use crate::{Counted, lock, tls};

// OK response is empty
// Error response is short json, ~30 bytes in size
pub const MAX_RESPONSE_SIZE: usize = 1 << 16;
// Inbound connection flow control window
// It's quite arbitrary, guided by:
// * concurrent requests limit, server limit being 1000 today
// * expected response size, see above
pub const CONNECTION_WINDOW_SIZE: u32 = 1000 * MAX_RESPONSE_SIZE as u32;
// Connection establishment safety time limits
pub const CONNECTION_TIMEOUT: Duration = Duration::from_secs(5);
pub const TLS_TIMEOUT: Duration = Duration::from_secs(5);
// Dead peer detection, in case a middlebox (e.g. NAT) drops the connection silently
// Otherwise OS gives up only after its retransmission limit, ~15min on Linux
// Idle connection: keepalive probes after 60s, every 10s, give up after 3
pub const TCP_KEEPALIVE_IDLE: Duration = Duration::from_secs(60);
pub const TCP_KEEPALIVE_INTERVAL: Duration = Duration::from_secs(10);
pub const TCP_KEEPALIVE_COUNT: u32 = 3;
// Busy connection: give up if sent data is not acknowledged in 30s
pub const TCP_USER_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Origin {
    pub host: String,
    pub port: u16,
}

impl Origin {
    pub fn parse(origin: &str) -> Result<Origin> {
        let invalid = || Error::InvalidOrigin(origin.to_owned());
        let (scheme, rest) = origin.split_once("://").ok_or_else(invalid)?;
        let forbidden = |c: char| {
            matches!(c, '/' | '?' | '#' | ';' | '@' | '\\') || c.is_whitespace() || c.is_control()
        };
        if !scheme.eq_ignore_ascii_case("https") || rest.contains(forbidden) {
            return Err(invalid());
        }
        let (host, port) = match rest.strip_prefix('[') {
            Some(bracketed) => {
                let (host, after) = bracketed.split_once(']').ok_or_else(invalid)?;
                host.parse::<Ipv6Addr>().map_err(|_| invalid())?;
                let port = match after {
                    "" => None,
                    after => Some(after.strip_prefix(':').ok_or_else(invalid)?),
                };
                (host, port)
            }
            None => match rest.split_once(':') {
                Some((host, port)) => (host, Some(port)),
                None => (rest, None),
            },
        };
        let port = match port {
            None => 443,
            Some(port) if !port.is_empty() && port.bytes().all(|b| b.is_ascii_digit()) => {
                port.parse().map_err(|_| invalid())?
            }
            Some(_) => return Err(invalid()),
        };
        if host.is_empty() {
            return Err(invalid());
        }
        Ok(Origin {
            host: host.to_owned(),
            port,
        })
    }

    pub fn authority(&self) -> String {
        if self.host.contains(':') {
            format!("[{}]:{}", self.host, self.port)
        } else {
            format!("{}:{}", self.host, self.port)
        }
    }
}

impl fmt::Display for Origin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "https://{}", self.authority())
    }
}

#[derive(Debug, Clone)]
pub struct Request {
    path: String,
    header: Vec<(HeaderName, HeaderValue)>,
    body: Bytes,
    created: Instant,
    timeout: Option<Duration>,
    deadline: Option<Instant>,
    expiration: Option<Instant>,
}

impl Request {
    pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);

    pub fn new(
        path: &str,
        header: &[(&str, &str)],
        data: &(impl Serialize + ?Sized),
    ) -> Result<Request> {
        if !path.starts_with('/') {
            return Err(Error::InvalidRequest(
                "Absolute URL path is required".into(),
            ));
        }
        PathAndQuery::try_from(path)
            .map_err(|e| Error::InvalidRequest(format!("invalid path {path:?}: {e}")))?;
        let header = header
            .iter()
            .map(|&(name, value)| parse_header(name, value))
            .collect::<Result<Vec<_>>>()?;
        let body = serde_json::to_vec(data)
            .map_err(|e| Error::InvalidRequest(format!("cannot encode request body: {e}")))?;
        Ok(Request {
            path: path.to_owned(),
            header,
            body: body.into(),
            created: Instant::now(),
            timeout: Some(Self::DEFAULT_TIMEOUT),
            deadline: None,
            expiration: None,
        })
    }

    pub fn with_timeout(mut self, timeout: Option<Duration>) -> Self {
        self.timeout = timeout;
        self
    }

    pub fn with_deadline(mut self, deadline: Option<Instant>) -> Self {
        self.deadline = deadline;
        self
    }

    pub fn with_expiration(mut self, expiration: Option<SystemTime>) -> Self {
        self.expiration = expiration.map(|at| {
            let now = Instant::now();
            match at.duration_since(SystemTime::now()) {
                Ok(left) => now
                    .checked_add(left)
                    .unwrap_or(now + Duration::from_secs(1 << 32)),
                Err(_) => now,
            }
        });
        self
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    pub fn body(&self) -> &[u8] {
        &self.body
    }

    pub fn deadline(&self) -> Option<(Instant, &'static str)> {
        [
            (
                "timeout",
                self.timeout.and_then(|t| self.created.checked_add(t)),
            ),
            ("deadline", self.deadline),
            ("expiration", self.expiration),
        ]
        .into_iter()
        .filter_map(|(source, at)| Some((at?, source)))
        .min_by_key(|&(at, _)| at)
    }

    /// Returns `Error::Timeout` if the request has timed out, or the remaining time
    pub fn time_left(&self) -> Result<Option<Duration>> {
        let Some((deadline, source)) = self.deadline() else {
            return Ok(None);
        };
        let now = Instant::now();
        if deadline > now {
            Ok(Some(deadline - now))
        } else {
            Err(timed_out(source))
        }
    }

    fn to_http(&self, authority: &Authority) -> Result<http::Request<()>> {
        let uri = Uri::builder()
            .scheme("https")
            .authority(authority.clone())
            .path_and_query(self.path.as_str())
            .build()
            .map_err(|e| Error::InvalidRequest(e.to_string()))?;
        let mut request = http::Request::post(uri)
            .body(())
            .map_err(|e| Error::InvalidRequest(e.to_string()))?;
        let headers = request.headers_mut();
        for (name, value) in &self.header {
            headers.append(name.clone(), value.clone());
        }
        Ok(request)
    }
}

fn timed_out(source: &str) -> Error {
    Error::Timeout(format!("Request timed out: {source}"))
}

fn parse_header(name: &str, value: &str) -> Result<(HeaderName, HeaderValue)> {
    let header_name = HeaderName::from_bytes(name.as_bytes())
        .map_err(|_| Error::InvalidRequest(format!("invalid header name {name:?}")))?;
    let header_value = HeaderValue::from_str(value)
        .map_err(|_| Error::InvalidRequest(format!("invalid value for header {name:?}")))?;
    // Connection-specific header fields are not allowed in HTTP/2.
    let connection_specific = matches!(
        header_name.as_str(),
        "connection" | "keep-alive" | "proxy-connection" | "transfer-encoding" | "upgrade"
    ) || (header_name == "te" && header_value != "trailers");
    if connection_specific {
        return Err(Error::InvalidRequest(format!(
            "header {header_name:?} is not allowed in HTTP/2"
        )));
    }
    Ok((header_name, header_value))
}

#[derive(Debug, Clone)]
pub struct Response {
    pub code: u16,
    pub header: HeaderMap,
    pub data: Option<Value>,
}

impl Response {
    pub fn new(code: u16, header: HeaderMap, body: &[u8]) -> Result<Response> {
        let data = if body.is_empty() {
            None
        } else {
            Some(serde_json::from_slice(body).map_err(|_| {
                let start = &body[..body.len().min(20)];
                Error::Format(format!("Not JSON: {:?}", String::from_utf8_lossy(start)))
            })?)
        };
        Ok(Response { code, header, data })
    }

    pub fn apns_id(&self) -> Option<&str> {
        self.header.get("apns-id")?.to_str().ok()
    }

    /// Response JSON 'reason' value, used in error responses.
    pub fn reason(&self) -> Option<&str> {
        self.data.as_ref()?.get("reason")?.as_str()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ConnectionState {
    Active,
    Closing,
    Closed,
}

impl ConnectionState {
    pub const fn as_str(self) -> &'static str {
        match self {
            ConnectionState::Active => "active",
            ConnectionState::Closing => "closing",
            ConnectionState::Closed => "closed",
        }
    }
}

impl fmt::Display for ConnectionState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

struct Shared {
    closing: AtomicBool,
    closed: watch::Sender<bool>,
    outcome: OnceLock<String>,
    pending: AtomicUsize,
    buffered: AtomicUsize,
    listener: Option<Arc<Notify>>,
}

impl Shared {
    fn is_closing(&self) -> bool {
        self.closing.load(Ordering::SeqCst)
    }

    fn is_closed(&self) -> bool {
        *self.closed.borrow()
    }

    fn mark_closing(&self, outcome: Option<String>) {
        if let Some(outcome) = outcome {
            let _ = self.outcome.set(outcome);
        }
        if !self.closing.swap(true, Ordering::SeqCst) {
            self.notify();
        }
    }

    fn mark_closed(&self, outcome: Option<String>) {
        if let Some(outcome) = outcome {
            let _ = self.outcome.set(outcome);
        }
        self.closing.store(true, Ordering::SeqCst);
        if !self.closed.send_replace(true) {
            self.notify();
        }
    }

    fn notify(&self) {
        if let Some(listener) = &self.listener {
            listener.notify_one();
        }
    }

    fn closed_error(&self) -> Error {
        Error::Closed(
            self.outcome
                .get()
                .map_or("Closed", String::as_str)
                .to_owned(),
        )
    }
}

struct Inner {
    host: String,
    port: u16,
    authority: Authority,
    sender: SendRequest<Bytes>,
    shared: Arc<Shared>,
    driver: Mutex<Option<JoinHandle<()>>>,
}

impl Drop for Inner {
    fn drop(&mut self) {
        if let Some(driver) = lock(&self.driver).take() {
            driver.abort();
        }
    }
}

/// Encapsulates a single HTTP/2 connection to the APN server.
///
/// Use [`Connection::connect`] to make connections.
///
/// Example use:
///
/// ```no_run
/// # async fn example(request: aapns::connection::Request) -> aapns::errors::Result<()> {
/// let conn = aapns::connection::Connection::connect("https://api.push.apple.com", None).await?;
/// let response = conn.post(&request).await;
/// conn.close().await;
/// # Ok(())
/// # }
/// ```
#[derive(Clone)]
pub struct Connection {
    inner: Arc<Inner>,
}

impl Connection {
    /// Connect to `origin` and return a Connection
    pub async fn connect(origin: &str, tls: Option<Arc<ClientConfig>>) -> Result<Connection> {
        let origin = Origin::parse(origin)?;
        let tls = match tls {
            Some(tls) => tls::with_h2_alpn(tls),
            None => tls::create_tls_config(None, None)?,
        };
        Self::connect_origin(origin, tls, None).await
    }

    pub(crate) async fn connect_origin(
        origin: Origin,
        tls: Arc<ClientConfig>,
        listener: Option<Arc<Notify>>,
    ) -> Result<Connection> {
        let invalid = || Error::InvalidOrigin(origin.to_string());
        let server_name = ServerName::try_from(origin.host.clone()).map_err(|_| invalid())?;
        let authority = Authority::try_from(origin.authority()).map_err(|_| invalid())?;

        let connect = TcpStream::connect((origin.host.as_str(), origin.port));
        let tcp = tokio::time::timeout(CONNECTION_TIMEOUT, connect)
            .await
            .map_err(|_| timeout_error(format!("timed out connecting to {origin}")))??;
        configure_socket(&tcp)?;
        let handshake = TlsConnector::from(tls).connect(server_name, tcp);
        let stream = tokio::time::timeout(TLS_TIMEOUT, handshake)
            .await
            .map_err(|_| timeout_error(format!("TLS handshake with {origin} timed out")))??;
        if stream.get_ref().1.alpn_protocol() != Some(b"h2") {
            return Err(Error::Closed("Failed to negotiate HTTP/2".into()));
        }
        Self::establish(stream, origin, authority, listener).await
    }

    async fn establish<T>(
        stream: T,
        origin: Origin,
        authority: Authority,
        listener: Option<Arc<Notify>>,
    ) -> Result<Connection>
    where
        T: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    {
        let shared = Arc::new(Shared {
            closing: AtomicBool::new(false),
            closed: watch::Sender::new(false),
            outcome: OnceLock::new(),
            pending: AtomicUsize::new(0),
            buffered: AtomicUsize::new(0),
            listener,
        });
        let (sender, connection) = h2::client::Builder::new()
            .enable_push(false)
            .initial_window_size(MAX_RESPONSE_SIZE as u32)
            .initial_connection_window_size(CONNECTION_WINDOW_SIZE)
            .max_header_list_size(u16::MAX.into())
            .handshake::<_, Bytes>(stream)
            .await
            .map_err(|e| Error::Closed(describe(&e)))?;

        let driver_shared = Arc::clone(&shared);
        let driver_sender = sender.clone();
        let driver = tokio::spawn(async move {
            let outcome = match connection.await {
                // h2 keeps the GOAWAY, if any, as the reason new requests fail
                Ok(()) => match probe(&driver_sender) {
                    Err(error) if error.is_go_away() => describe(&error),
                    _ => "Server closed the connection".to_owned(),
                },
                Err(error) => {
                    let outcome = describe(&error);
                    if is_tls_error(&error) {
                        // TLS 1.3 servers reject a bad client certificate after the handshake,
                        // e.g. an expired certificate is reported here, so log it
                        tracing::error!("TLS error: {outcome}");
                    }
                    outcome
                }
            };
            tracing::debug!(%outcome, "connection closed");
            driver_shared.mark_closed(Some(outcome));
        });

        Ok(Connection {
            inner: Arc::new(Inner {
                host: origin.host,
                port: origin.port,
                authority,
                sender,
                shared,
                driver: Mutex::new(Some(driver)),
            }),
        })
    }

    /// Post the `request` on the connection
    pub async fn post(&self, request: &Request) -> Result<Response> {
        request.time_left()?;
        let shared = &*self.inner.shared;
        if shared.is_closing() {
            return Err(shared.closed_error());
        }
        let limit = self.inner.sender.current_max_send_streams();
        let Some(_pending) = Counted::acquire(&shared.pending, limit) else {
            return Err(Error::Blocked);
        };

        let exchange = async {
            match request.deadline() {
                Some((deadline, source)) => {
                    tokio::time::timeout_at(deadline.into(), self.exchange(request))
                        .await
                        .unwrap_or_else(|_| Err(timed_out(source)))
                }
                None => self.exchange(request).await,
            }
        };
        let mut closed = shared.closed.subscribe();
        let closed = async move {
            let _ = closed.wait_for(|closed| *closed).await;
        };
        tokio::select! {
            biased;
            result = exchange => result,
            () = closed => Err(shared.closed_error()),
        }
    }

    async fn exchange(&self, request: &Request) -> Result<Response> {
        let buffered = Counted::new(&self.inner.shared.buffered);
        let mut sender = self.inner.sender.clone();
        let (response, mut stream) = sender
            .send_request(request.to_http(&self.inner.authority)?, false)
            .map_err(|e| self.send_failed(e))?;
        let sent = send_body(&mut stream, request.body.clone()).await;
        drop(buffered);

        let response = response.await.map_err(|e| self.stream_failed(e))?;
        if let Err(error) = sent {
            tracing::debug!(%error, "request body was not sent completely");
        }
        let (parts, mut body) = response.into_parts();
        let mut data = Vec::new();
        while let Some(chunk) = body.data().await {
            let chunk = chunk.map_err(|e| self.stream_failed(e))?;
            let _ = body.flow_control().release_capacity(chunk.len());
            data.extend_from_slice(&chunk);
            if data.len() >= MAX_RESPONSE_SIZE {
                return Err(Error::ResponseTooLarge(format!(
                    "Larger than {MAX_RESPONSE_SIZE}"
                )));
            }
        }
        Response::new(parts.status.as_u16(), parts.headers, &data)
    }

    fn send_failed(&self, error: h2::Error) -> Error {
        if error.is_go_away() || error.is_io() || probe(&self.inner.sender).is_err() {
            let shared = &*self.inner.shared;
            shared.mark_closing(Some(describe(&error)));
            shared.closed_error()
        } else {
            Error::InvalidRequest(error.to_string())
        }
    }

    fn stream_failed(&self, error: h2::Error) -> Error {
        if error.is_reset() {
            return Error::StreamReset(error.reason().map_or(0, u32::from));
        }
        let shared = &*self.inner.shared;
        if error.is_go_away() || error.is_io() {
            shared.mark_closing(Some(describe(&error)));
        }
        if shared.is_closing() {
            shared.closed_error()
        } else {
            Error::Closed(describe(&error))
        }
    }

    /// Terminate the connection and free up the resources
    pub async fn close(&self) {
        let shared = &*self.inner.shared;
        shared.mark_closing(Some("Closed".into()));
        let driver = lock(&self.inner.driver).take();
        if let Some(driver) = driver {
            driver.abort();
            let _ = driver.await;
        }
        shared.mark_closed(None);
    }

    pub(crate) fn begin_closing(&self) {
        self.inner.shared.mark_closing(None);
    }

    pub fn state(&self) -> ConnectionState {
        let shared = &*self.inner.shared;
        if shared.is_closed() {
            ConnectionState::Closed
        } else if shared.is_closing() {
            ConnectionState::Closing
        } else {
            ConnectionState::Active
        }
    }

    pub fn is_closing(&self) -> bool {
        self.inner.shared.is_closing()
    }

    pub fn is_closed(&self) -> bool {
        self.inner.shared.is_closed()
    }

    /// Is this connection unable to process more requests, either for now or permanently?
    pub fn is_blocked(&self) -> bool {
        self.is_closing()
            || self.inner.shared.pending.load(Ordering::SeqCst)
                >= self.inner.sender.current_max_send_streams()
    }

    pub fn outcome(&self) -> Option<&str> {
        self.inner.shared.outcome.get().map(String::as_str)
    }

    /// Count of the requests that were sent out and are awaiting server response.
    pub fn inflight(&self) -> usize {
        self.pending().saturating_sub(self.buffered())
    }

    /// Count of the requests that we are still to send out.
    pub fn buffered(&self) -> usize {
        self.inner.shared.buffered.load(Ordering::SeqCst)
    }

    /// Total count of pending requests.
    pub fn pending(&self) -> usize {
        self.inner.shared.pending.load(Ordering::SeqCst)
    }

    pub fn host(&self) -> &str {
        &self.inner.host
    }

    pub fn port(&self) -> u16 {
        self.inner.port
    }
}

impl fmt::Debug for Connection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Connection")
            .field("host", &self.inner.host)
            .field("port", &self.inner.port)
            .field("state", &self.state())
            .field("pending", &self.pending())
            .field("outcome", &self.outcome())
            .finish()
    }
}

async fn send_body(
    stream: &mut SendStream<Bytes>,
    mut body: Bytes,
) -> std::result::Result<(), String> {
    if body.is_empty() {
        return stream.send_data(body, true).map_err(|e| e.to_string());
    }
    loop {
        stream.reserve_capacity(body.len());
        let capacity = stream.capacity();
        if capacity > 0 {
            let chunk = body.split_to(capacity.min(body.len()));
            let end_of_stream = body.is_empty();
            stream
                .send_data(chunk, end_of_stream)
                .map_err(|e| e.to_string())?;
            if end_of_stream {
                return Ok(());
            }
            continue;
        }
        match poll_fn(|cx| stream.poll_capacity(cx)).await {
            Some(Ok(_)) => {}
            Some(Err(error)) => return Err(error.to_string()),
            None => return Err("stream closed before the request body was sent".into()),
        }
    }
}

fn probe(sender: &SendRequest<Bytes>) -> std::result::Result<(), h2::Error> {
    let mut ready = pin!(sender.clone().ready());
    match ready.as_mut().poll(&mut Context::from_waker(Waker::noop())) {
        Poll::Ready(Err(error)) => Err(error),
        _ => Ok(()),
    }
}

// rustls errors reach h2 as InvalidData I/O errors
fn is_tls_error(error: &h2::Error) -> bool {
    error
        .get_io()
        .is_some_and(|io| io.kind() == io::ErrorKind::InvalidData)
}

fn describe(error: &h2::Error) -> String {
    match error.get_io() {
        Some(io) => io.to_string(),
        None => error.to_string(),
    }
}

fn timeout_error(message: String) -> io::Error {
    io::Error::new(io::ErrorKind::TimedOut, message)
}

fn configure_socket(tcp: &TcpStream) -> io::Result<()> {
    tcp.set_nodelay(true)?;
    let socket = socket2::SockRef::from(tcp);
    // Not all options are available on all platforms
    let keepalive = socket2::TcpKeepalive::new().with_time(TCP_KEEPALIVE_IDLE);
    #[cfg(any(
        target_os = "android",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "fuchsia",
        target_os = "illumos",
        target_os = "ios",
        target_os = "linux",
        target_os = "macos",
        target_os = "netbsd",
        target_os = "tvos",
        target_os = "watchos",
        target_os = "windows",
    ))]
    let keepalive = keepalive.with_interval(TCP_KEEPALIVE_INTERVAL);
    #[cfg(any(
        target_os = "android",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "fuchsia",
        target_os = "illumos",
        target_os = "ios",
        target_os = "linux",
        target_os = "macos",
        target_os = "netbsd",
        target_os = "tvos",
        target_os = "watchos",
    ))]
    let keepalive = keepalive.with_retries(TCP_KEEPALIVE_COUNT);
    socket.set_tcp_keepalive(&keepalive)?;
    #[cfg(any(target_os = "android", target_os = "fuchsia", target_os = "linux"))]
    socket.set_tcp_user_timeout(Some(TCP_USER_TIMEOUT))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn valid_origins() {
        let parse = |s| Origin::parse(s).unwrap();
        assert_eq!(
            parse("https://localhost"),
            Origin {
                host: "localhost".into(),
                port: 443
            }
        );
        assert_eq!(
            parse("https://localhost:1234"),
            Origin {
                host: "localhost".into(),
                port: 1234
            }
        );
        assert_eq!(parse("HTTPS://api.push.apple.com:2197").port, 2197);
        assert_eq!(parse("https://127.0.0.1:8443").host, "127.0.0.1");
        let ipv6 = parse("https://[::1]:8443");
        assert_eq!(
            ipv6,
            Origin {
                host: "::1".into(),
                port: 8443
            }
        );
        assert_eq!(ipv6.authority(), "[::1]:8443");
        assert_eq!(parse("https://[::1]").port, 443);
    }

    #[test]
    fn invalid_origins() {
        for origin in [
            "just.hostname.is.invalid",
            "http://localhost",
            "https://user@localhost:1234",
            "https://user:pass@localhost:1234",
            "https://localhost:1234/foo/bar",
            "https://localhost:1234/",
            "https://localhost:1234?q=ax",
            "https://localhost:1234;p=ax",
            "https://localhost:1234#frag",
            "https://",
            "https://:1234",
            "https://localhost:",
            "https://localhost:+123",
            "https://localhost:65536",
            "https://localhost:1:2",
            "https://[::1",
            "https://[nope]:1234",
            "https://[::1]1234",
            "https://local host",
        ] {
            assert!(
                matches!(Origin::parse(origin), Err(Error::InvalidOrigin(_))),
                "{origin} should be invalid"
            );
        }
    }

    #[test]
    fn request_body_and_header() {
        let request = Request::new(
            "/3/device/42",
            &[("Apns-Priority", "5"), ("apns-push-type", "alert")],
            &json!({"aps": {"alert": {"body": "ü"}}}),
        )
        .unwrap();
        assert_eq!(request.path(), "/3/device/42");
        assert_eq!(
            request.body(),
            r#"{"aps":{"alert":{"body":"ü"}}}"#.as_bytes()
        );
        let http = request
            .to_http(&Authority::from_static("localhost:443"))
            .unwrap();
        assert_eq!(http.method(), http::Method::POST);
        assert_eq!(http.uri(), "https://localhost:443/3/device/42");
        assert_eq!(http.headers()["apns-priority"], "5");
        assert_eq!(http.headers()["apns-push-type"], "alert");
    }

    #[test]
    fn invalid_requests() {
        let data = json!({});
        for (path, header) in [
            ("relative", &[][..]),
            ("/with space", &[]),
            ("/ok", &[("bad name", "x")]),
            ("/ok", &[("x-ok", "bad\nvalue")]),
            ("/ok", &[("connection", "keep-alive")]),
            ("/ok", &[("transfer-encoding", "chunked")]),
            ("/ok", &[("te", "gzip")]),
        ] {
            assert!(
                matches!(
                    Request::new(path, header, &data),
                    Err(Error::InvalidRequest(_))
                ),
                "{path} {header:?} should be invalid"
            );
        }
        Request::new("/ok", &[("te", "trailers")], &data).unwrap();
    }

    #[test]
    fn default_timeout() {
        let request = Request::new("/", &[], &json!({})).unwrap();
        let (deadline, source) = request.deadline().unwrap();
        assert_eq!(source, "timeout");
        assert!(deadline > Instant::now() + Duration::from_secs(9));
        assert!(request.time_left().unwrap().unwrap() <= Request::DEFAULT_TIMEOUT);
    }

    #[test]
    fn earliest_deadline_wins() {
        let now = Instant::now();
        let request = Request::new("/", &[], &json!({}))
            .unwrap()
            .with_deadline(Some(now + Duration::from_secs(5)));
        assert_eq!(request.deadline().unwrap().1, "deadline");
        let request = request.with_expiration(Some(SystemTime::now() + Duration::from_secs(2)));
        assert_eq!(request.deadline().unwrap().1, "expiration");
        let request = request.with_timeout(Some(Duration::from_secs(1)));
        assert_eq!(request.deadline().unwrap().1, "timeout");
    }

    #[test]
    fn no_deadline() {
        let request = Request::new("/", &[], &json!({}))
            .unwrap()
            .with_timeout(None);
        assert_eq!(request.deadline(), None);
        assert_eq!(request.time_left().unwrap(), None);
    }

    #[test]
    fn expired() {
        let request = Request::new("/", &[], &json!({}))
            .unwrap()
            .with_expiration(Some(SystemTime::UNIX_EPOCH));
        match request.time_left() {
            Err(Error::Timeout(message)) => assert_eq!(message, "Request timed out: expiration"),
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn response_parsing() {
        let mut header = HeaderMap::new();
        header.insert("apns-id", HeaderValue::from_static("abc"));
        let response = Response::new(400, header, br#"{"reason": "BadDeviceToken"}"#).unwrap();
        assert_eq!(response.apns_id(), Some("abc"));
        assert_eq!(response.reason(), Some("BadDeviceToken"));

        let response = Response::new(200, HeaderMap::new(), b"").unwrap();
        assert_eq!(response.data, None);
        assert_eq!(response.reason(), None);
        assert_eq!(response.apns_id(), None);

        let response = Response::new(200, HeaderMap::new(), b"[1]").unwrap();
        assert_eq!(response.reason(), None);

        match Response::new(500, HeaderMap::new(), b"<html>Internal Server Error</html>") {
            Err(Error::Format(message)) => {
                assert_eq!(message, r#"Not JSON: "<html>Internal Serve""#)
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[tokio::test]
    async fn connect_rejects_bad_origins() {
        for origin in [
            "just.hostname.is.invalid",
            "http://localhost",
            "https://localhost:1234/foo",
        ] {
            let result = Connection::connect(origin, None).await;
            assert!(matches!(result, Err(Error::InvalidOrigin(_))), "{origin}");
        }
    }

    #[tokio::test]
    async fn dead_peer_detection() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let tcp = TcpStream::connect(listener.local_addr().unwrap())
            .await
            .unwrap();
        configure_socket(&tcp).unwrap();
        assert!(tcp.nodelay().unwrap());
        let socket = socket2::SockRef::from(&tcp);
        assert!(socket.keepalive().unwrap());
        assert_eq!(socket.tcp_keepalive_time().unwrap(), TCP_KEEPALIVE_IDLE);
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        {
            assert_eq!(
                socket.tcp_keepalive_interval().unwrap(),
                TCP_KEEPALIVE_INTERVAL
            );
            assert_eq!(socket.tcp_keepalive_retries().unwrap(), TCP_KEEPALIVE_COUNT);
        }
        #[cfg(target_os = "linux")]
        assert_eq!(socket.tcp_user_timeout().unwrap(), Some(TCP_USER_TIMEOUT));
    }

    struct FailingIo(Option<io::Error>);

    impl AsyncRead for FailingIo {
        fn poll_read(
            mut self: std::pin::Pin<&mut Self>,
            _: &mut Context<'_>,
            _: &mut tokio::io::ReadBuf<'_>,
        ) -> Poll<io::Result<()>> {
            Poll::Ready(Err(self
                .0
                .take()
                .unwrap_or_else(|| io::ErrorKind::BrokenPipe.into())))
        }
    }

    impl AsyncWrite for FailingIo {
        fn poll_write(
            self: std::pin::Pin<&mut Self>,
            _: &mut Context<'_>,
            buf: &[u8],
        ) -> Poll<io::Result<usize>> {
            Poll::Ready(Ok(buf.len()))
        }

        fn poll_flush(self: std::pin::Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }

        fn poll_shutdown(
            self: std::pin::Pin<&mut Self>,
            _: &mut Context<'_>,
        ) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }

    async fn connection_failing_with(error: io::Error) -> Connection {
        let origin = Origin::parse("https://localhost:1234").unwrap();
        let authority = Authority::from_static("localhost:1234");
        let connection = Connection::establish(FailingIo(Some(error)), origin, authority, None)
            .await
            .unwrap();
        tokio::time::timeout(Duration::from_secs(5), async {
            while !connection.is_closed() {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("connection should close");
        connection
    }

    #[derive(Clone, Default)]
    struct Logs(Arc<Mutex<Vec<u8>>>);

    impl io::Write for Logs {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            lock(&self.0).extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl Logs {
        fn capture() -> (Logs, tracing::subscriber::DefaultGuard) {
            let logs = Logs::default();
            let writer = logs.clone();
            let subscriber = tracing_subscriber::fmt()
                .with_writer(move || writer.clone())
                .with_max_level(tracing::Level::DEBUG)
                .with_ansi(false)
                .finish();
            (logs, tracing::subscriber::set_default(subscriber))
        }

        fn text(&self) -> String {
            String::from_utf8_lossy(&lock(&self.0)).into_owned()
        }
    }

    #[tokio::test]
    async fn network_errors_close_the_connection_quietly() {
        for error in [
            io::Error::new(io::ErrorKind::TimedOut, "Operation timed out"),
            io::Error::new(io::ErrorKind::HostUnreachable, "No route to host"),
            io::Error::from(io::ErrorKind::ConnectionReset),
        ] {
            let (logs, _guard) = Logs::capture();
            let expected = error.to_string();
            let connection = connection_failing_with(error).await;
            assert_eq!(connection.state(), ConnectionState::Closed);
            assert_eq!(connection.outcome(), Some(expected.as_str()));
            match connection
                .post(&Request::new("/", &[], &json!({})).unwrap())
                .await
            {
                Err(Error::Closed(outcome)) => assert_eq!(outcome, expected),
                other => panic!("unexpected {other:?}"),
            }
            let logs = logs.text();
            assert!(logs.contains(&expected), "{logs}");
            assert!(!logs.contains("ERROR"), "{logs}");
        }
    }

    #[tokio::test]
    async fn tls_errors_are_logged() {
        // e.g. client certificate rejected by the server after a TLS 1.3 handshake
        let (logs, _guard) = Logs::capture();
        let alert = rustls::Error::AlertReceived(rustls::AlertDescription::CertificateExpired);
        let error = io::Error::new(io::ErrorKind::InvalidData, alert);
        let expected = error.to_string();
        let connection = connection_failing_with(error).await;
        assert_eq!(connection.state(), ConnectionState::Closed);
        assert_eq!(connection.outcome(), Some(expected.as_str()));
        let logs = logs.text();
        let message = format!("aapns::connection: TLS error: {expected}");
        assert!(
            logs.lines()
                .any(|line| line.contains(" ERROR ") && line.ends_with(&message)),
            "{logs}"
        );
    }
}
