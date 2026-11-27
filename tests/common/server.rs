//! A mock APNs server, used by the functional tests and the `mock_server`
//! example.
#![allow(dead_code)]

use std::io;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use bytes::Bytes;
use h2::RecvStream;
use h2::server::SendResponse;
use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use rustls::server::WebPkiClientVerifier;
use rustls::{RootCertStore, ServerConfig};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;
use tokio::task::{JoinHandle, JoinSet};
use tokio_rustls::TlsAcceptor;
use tokio_rustls::server::TlsStream;

/// `apns-id` of the responses.
pub const APNS_ID: &str = "42424242-4242-4242-4242-424242424242";

/// How the server behaves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flavor {
    /// Accepts every notification after ¼s.
    Ok,
    /// Rejects every notification with `BadDeviceToken` after 1s.
    BadToken,
    /// Accepts the first notification after 1s, then shuts down: sends GOAWAY
    /// on all connections and stops listening.
    TerminatesConnection,
    /// Closes every connection with a GOAWAY frame whose debug data is
    /// `{"reason":"BadCertificateEnvironment"}`, like APNs does when the
    /// client certificate is for the other environment.
    BadCertificateEnvironment,
    /// Does not negotiate ALPN.
    NoAlpn,
    /// Rejects the client certificate as issued by an unknown CA. With TLS
    /// 1.3, the client only notices after the handshake.
    RejectsClientCertificate,
}

impl FromStr for Flavor {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(match s {
            "ok" => Flavor::Ok,
            "bad-token" => Flavor::BadToken,
            "terminates-connection" => Flavor::TerminatesConnection,
            "bad-certificate-environment" => Flavor::BadCertificateEnvironment,
            "no-alpn" => Flavor::NoAlpn,
            "rejects-client-certificate" => Flavor::RejectsClientCertificate,
            other => return Err(format!("unknown flavor {other:?}")),
        })
    }
}

/// The test PKI, see `tests/certs/generate.sh`.
pub fn certs_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/certs")
}

/// A request received by the server.
#[derive(Debug, Clone)]
pub struct RecordedRequest {
    pub method: String,
    pub authority: String,
    pub path: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl RecordedRequest {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, v)| v.as_str())
    }

    pub fn json(&self) -> serde_json::Value {
        serde_json::from_slice(&self.body).expect("request body is JSON")
    }
}

struct State {
    flavor: Flavor,
    requests: Mutex<Vec<RecordedRequest>>,
    shutdown: watch::Sender<bool>,
}

/// A running mock server, stopped when dropped.
pub struct MockServer {
    addr: SocketAddr,
    state: Arc<State>,
    task: JoinHandle<()>,
}

impl MockServer {
    /// Starts a server on a free port of the loopback interface.
    pub async fn start(flavor: Flavor) -> MockServer {
        Self::bind(flavor, "127.0.0.1:0")
            .await
            .expect("failed to start mock server")
    }

    /// Starts a server on the given address.
    pub async fn bind(flavor: Flavor, addr: &str) -> io::Result<MockServer> {
        let listener = TcpListener::bind(addr).await?;
        let addr = listener.local_addr()?;
        let acceptor = TlsAcceptor::from(server_config(flavor));
        let state = Arc::new(State {
            flavor,
            requests: Mutex::default(),
            shutdown: watch::Sender::new(false),
        });
        let task = tokio::spawn(serve(listener, acceptor, state.clone()));
        Ok(MockServer { addr, state, task })
    }

    pub fn port(&self) -> u16 {
        self.addr.port()
    }

    pub fn origin(&self) -> String {
        format!("https://localhost:{}", self.port())
    }

    /// Requests received so far.
    pub fn requests(&self) -> Vec<RecordedRequest> {
        self.state.requests.lock().unwrap().clone()
    }

    /// Runs until the server shuts down.
    pub async fn wait(mut self) {
        let _ = (&mut self.task).await;
    }
}

impl Drop for MockServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn server_config(flavor: Flavor) -> Arc<ServerConfig> {
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let certs = |name: &str| {
        CertificateDer::pem_file_iter(certs_dir().join(name))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap()
    };
    let key = PrivateKeyDer::from_pem_file(certs_dir().join("server-key.pem")).unwrap();
    // Like APNs, require a client certificate. To reject it, trust an
    // unrelated certificate instead of the CA that issued it.
    let client_ca = match flavor {
        Flavor::RejectsClientCertificate => "server.pem",
        _ => "ca.pem",
    };
    let mut roots = RootCertStore::empty();
    for cert in certs(client_ca) {
        roots.add(cert).unwrap();
    }
    let verifier = WebPkiClientVerifier::builder_with_provider(Arc::new(roots), provider.clone())
        .build()
        .unwrap();
    let mut config = ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_client_cert_verifier(verifier)
        .with_single_cert(certs("server.pem"), key)
        .unwrap();
    if flavor != Flavor::NoAlpn {
        config.alpn_protocols = vec![b"h2".to_vec()];
    }
    Arc::new(config)
}

async fn serve(listener: TcpListener, acceptor: TlsAcceptor, state: Arc<State>) {
    let mut connections = JoinSet::new();
    let mut shutdown = state.shutdown.subscribe();
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                if let Ok((tcp, _)) = accepted {
                    connections.spawn(serve_connection(tcp, acceptor.clone(), state.clone()));
                }
            }
            _ = shutdown.wait_for(|shutdown| *shutdown) => break,
            Some(_) = connections.join_next(), if !connections.is_empty() => {}
        }
    }
    // Stop listening, then let connections say goodbye.
    drop(listener);
    while connections.join_next().await.is_some() {}
}

async fn serve_connection(tcp: TcpStream, acceptor: TlsAcceptor, state: Arc<State>) {
    let tls = match acceptor.accept(tcp).into_fallible().await {
        Ok(tls) => tls,
        Err((_, mut tcp)) => {
            // The TLS alert has been sent. Read whatever the client sent after
            // its handshake until it hangs up, as closing a socket with unread
            // data resets the connection, which could beat the alert.
            let mut buf = [0; 4096];
            let _ = tokio::time::timeout(Duration::from_secs(5), async {
                while matches!(tcp.read(&mut buf).await, Ok(n) if n > 0) {}
            })
            .await;
            return;
        }
    };
    if state.flavor == Flavor::BadCertificateEnvironment {
        return refuse_certificate(tls).await;
    }
    let handshake = h2::server::Builder::new()
        // Go's default, the server these tests were first written against
        .max_concurrent_streams(250)
        .handshake::<_, Bytes>(tls);
    let Ok(mut connection) = handshake.await else {
        return;
    };
    let mut handlers = JoinSet::new();
    let mut shutdown = state.shutdown.subscribe();
    let mut shutting_down = false;
    loop {
        tokio::select! {
            request = connection.accept() => match request {
                Some(Ok((request, respond))) => {
                    handlers.spawn(handle(request, respond, state.clone()));
                }
                _ => break,
            },
            _ = shutdown.wait_for(|shutdown| *shutdown), if !shutting_down => {
                shutting_down = true;
                connection.abrupt_shutdown(h2::Reason::NO_ERROR);
            }
        }
    }
}

async fn handle(
    request: http::Request<RecvStream>,
    mut respond: SendResponse<Bytes>,
    state: Arc<State>,
) {
    let (parts, mut body) = request.into_parts();
    let mut data = Vec::new();
    while let Some(chunk) = body.data().await {
        let Ok(chunk) = chunk else { return };
        let _ = body.flow_control().release_capacity(chunk.len());
        data.extend_from_slice(&chunk);
    }
    let recorded = RecordedRequest {
        method: parts.method.to_string(),
        authority: parts
            .uri
            .authority()
            .map(ToString::to_string)
            .unwrap_or_default(),
        path: parts.uri.path().to_owned(),
        headers: parts
            .headers
            .iter()
            .map(|(name, value)| {
                (
                    name.to_string(),
                    value.to_str().unwrap_or_default().to_owned(),
                )
            })
            .collect(),
        body: data,
    };
    tracing::info!(
        method = %recorded.method,
        path = %recorded.path,
        headers = ?recorded.headers,
        body = %String::from_utf8_lossy(&recorded.body),
        "mock server received a request"
    );
    state.requests.lock().unwrap().push(recorded);

    match state.flavor {
        Flavor::Ok => {
            tokio::time::sleep(Duration::from_millis(250)).await;
            reply(&mut respond, 200, Some(APNS_ID), b"{}");
        }
        Flavor::BadToken => {
            tokio::time::sleep(Duration::from_secs(1)).await;
            reply(
                &mut respond,
                400,
                Some(APNS_ID),
                br#"{"reason": "BadDeviceToken"}"#,
            );
        }
        Flavor::TerminatesConnection => {
            tokio::time::sleep(Duration::from_secs(1)).await;
            reply(&mut respond, 200, None, b"{}");
            // Give the response time to be written before shutting down.
            tokio::time::sleep(Duration::from_millis(50)).await;
            state.shutdown.send_replace(true);
        }
        Flavor::BadCertificateEnvironment | Flavor::NoAlpn | Flavor::RejectsClientCertificate => {
            unreachable!("never accepts requests")
        }
    }
}

fn reply(
    respond: &mut SendResponse<Bytes>,
    status: u16,
    apns_id: Option<&str>,
    body: &'static [u8],
) {
    let mut response = http::Response::builder().status(status);
    if let Some(apns_id) = apns_id {
        response = response.header("apns-id", apns_id);
    }
    // The client may have given up on the request already.
    if let Ok(mut stream) = respond.send_response(response.body(()).unwrap(), false) {
        let _ = stream.send_data(Bytes::from_static(body), true);
    }
}

/// Sends a GOAWAY frame with a JSON reason, which the `h2` server API cannot.
async fn refuse_certificate(mut tls: TlsStream<TcpStream>) {
    fn frame(frame_type: u8, payload: &[u8]) -> Vec<u8> {
        let mut frame = (payload.len() as u32).to_be_bytes()[1..].to_vec();
        frame.extend([frame_type, 0, 0, 0, 0, 0]);
        frame.extend_from_slice(payload);
        frame
    }
    let mut goaway = vec![0; 8]; // last stream id 0, NO_ERROR
    goaway.extend_from_slice(br#"{"reason":"BadCertificateEnvironment"}"#);
    let mut frames = frame(0x4, &[]); // SETTINGS
    frames.extend(frame(0x7, &goaway));
    let _ = tls.write_all(&frames).await;
    let _ = tls.flush().await;
    // Wait for the client to hang up, so it gets to read the frames.
    let mut buf = [0; 4096];
    let _ = tokio::time::timeout(Duration::from_secs(5), async {
        while matches!(tls.read(&mut buf).await, Ok(n) if n > 0) {}
    })
    .await;
    let _ = tls.shutdown().await;
}
