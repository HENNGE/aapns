#![allow(dead_code)]

pub mod server;

use std::future::Future;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use aapns::api::Server;
use aapns::connection::Request;
use aapns::models::{Alert, Notification};
use aapns::rustls::ClientConfig;
use serde_json::json;

pub use server::{APNS_ID, Flavor, MockServer, certs_dir};

pub fn tls_config() -> Arc<ClientConfig> {
    static CONFIG: OnceLock<Arc<ClientConfig>> = OnceLock::new();
    CONFIG
        .get_or_init(|| {
            aapns::tls::create_tls_config(
                Some(&certs_dir().join("client.pem")),
                Some(&certs_dir().join("ca.pem")),
            )
            .unwrap()
        })
        .clone()
}

pub fn request42() -> Request {
    Request::new("/3/device/42", &[], &json!({})).unwrap()
}

pub fn notification() -> Notification {
    Notification::new(Alert::new("body").with_title("title"))
}

pub fn target(server: &MockServer) -> Server {
    Server::new(certs_dir().join("client.pem"), "localhost")
        .with_port(server.port())
        .with_ca_file(certs_dir().join("ca.pem"))
}

pub async fn join_all<F>(futures: impl IntoIterator<Item = F>) -> Vec<F::Output>
where
    F: Future + Send + 'static,
    F::Output: Send + 'static,
{
    let tasks: Vec<_> = futures.into_iter().map(tokio::spawn).collect();
    let mut outputs = Vec::with_capacity(tasks.len());
    for task in tasks {
        outputs.push(task.await.expect("task panicked"));
    }
    outputs
}

pub fn assert_quarter_second(started: Instant) {
    let took = started.elapsed();
    assert!(
        took > Duration::from_millis(250) && took < Duration::from_millis(300),
        "Server has ¼s artificial delay, took {took:?}"
    );
}

#[derive(Clone, Default)]
pub struct Logs(Arc<std::sync::Mutex<Vec<u8>>>);

impl std::io::Write for Logs {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl Logs {
    pub fn capture() -> (Logs, tracing::subscriber::DefaultGuard) {
        let logs = Logs::default();
        let writer = logs.clone();
        let subscriber = tracing_subscriber::fmt()
            .with_writer(move || writer.clone())
            .with_max_level(tracing::Level::DEBUG)
            .with_ansi(false)
            .finish();
        (logs, tracing::subscriber::set_default(subscriber))
    }

    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.0.lock().unwrap()).into_owned()
    }
}

pub async fn wait_until(timeout: Duration, mut condition: impl FnMut() -> bool) {
    let deadline = Instant::now() + timeout;
    while !condition() {
        assert!(
            Instant::now() < deadline,
            "condition not met in {timeout:?}"
        );
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}
