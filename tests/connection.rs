mod common;

use std::time::{Duration, Instant};

use aapns::connection::{Connection, ConnectionState};
use aapns::errors::Error;
use common::*;

async fn connect(server: &MockServer) -> Connection {
    Connection::connect(&server.origin(), Some(tls_config()))
        .await
        .unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sequential() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    for _ in 0..4 {
        let started = Instant::now();
        let response = connection.post(&request42()).await.unwrap();
        assert_eq!(response.code, 200);
        assert_eq!(response.apns_id(), Some(APNS_ID));
        assert_quarter_second(started);
    }
    connection.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn parallel() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    let started = Instant::now();
    let responses = join_all((0..4).map(|_| {
        let connection = connection.clone();
        async move { connection.post(&request42()).await }
    }))
    .await;
    for response in responses {
        let response = response.unwrap();
        assert_eq!(response.code, 200);
        assert_eq!(response.apns_id(), Some(APNS_ID));
    }
    assert_quarter_second(started);
    connection.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bad_token() {
    let server = MockServer::start(Flavor::BadToken).await;
    let connection = connect(&server).await;
    let response = connection.post(&request42()).await.unwrap();
    assert_eq!(response.code, 400);
    assert_eq!(response.apns_id(), Some(APNS_ID));
    assert_eq!(response.reason(), Some("BadDeviceToken"));
    connection.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closing_connection() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    let task = tokio::spawn({
        let connection = connection.clone();
        async move { connection.post(&request42()).await }
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    connection.close().await;
    assert!(matches!(task.await.unwrap(), Err(Error::Closed(_))));
    assert_eq!(connection.state(), ConnectionState::Closed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closed_connection() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    connection.close().await;
    match connection.post(&request42()).await {
        Err(Error::Closed(outcome)) => assert_eq!(outcome, "Closed"),
        other => panic!("unexpected {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn termination() {
    let server = MockServer::start(Flavor::TerminatesConnection).await;
    let connection = connect(&server).await;
    // terminating server may break the first request, and definitely breaks the second
    let result = async {
        connection.post(&request42()).await?;
        connection.post(&request42()).await
    }
    .await;
    match result {
        Err(Error::Closed(outcome)) => assert!(
            [
                "connection error received",
                "Server closed the connection",
                "connection reset",
                "broken pipe"
            ]
            .iter()
            .any(|expected| outcome.contains(expected)),
            "{outcome}"
        ),
        other => panic!("unexpected {other:?}"),
    }
    assert!(connection.is_closing());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn connection_state() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    assert_eq!(connection.state(), ConnectionState::Active);
    assert_eq!(connection.host(), "localhost");
    assert_eq!(connection.port(), server.port());
    assert_eq!(
        (
            connection.pending(),
            connection.buffered(),
            connection.inflight()
        ),
        (0, 0, 0)
    );
    assert!(!connection.is_blocked());
    assert_eq!(connection.outcome(), None);
    connection.close().await;
    assert_eq!(connection.state(), ConnectionState::Closed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn request_on_the_wire() {
    let server = MockServer::start(Flavor::Ok).await;
    let connection = connect(&server).await;
    let request = aapns::connection::Request::new(
        "/3/device/42",
        &[
            ("Apns-Push-Type", "alert"),
            ("apns-topic", "com.example.app"),
        ],
        &serde_json::json!({"aps": {"alert": {"body": "ü"}}}),
    )
    .unwrap();
    connection.post(&request).await.unwrap();
    let requests = server.requests();
    assert_eq!(requests.len(), 1);
    let received = &requests[0];
    assert_eq!(received.method, "POST");
    assert_eq!(received.path, "/3/device/42");
    assert_eq!(received.authority, format!("localhost:{}", server.port()));
    assert_eq!(received.header("apns-push-type"), Some("alert"));
    assert_eq!(received.header("apns-topic"), Some("com.example.app"));
    assert_eq!(
        received.body,
        r#"{"aps":{"alert":{"body":"ü"}}}"#.as_bytes()
    );
    connection.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn alpn_is_always_h2() {
    let server = MockServer::start(Flavor::Ok).await;
    let mut config = (*tls_config()).clone();
    config.alpn_protocols.clear();
    let connection = Connection::connect(&server.origin(), Some(config.into()))
        .await
        .unwrap();
    assert_eq!(connection.post(&request42()).await.unwrap().code, 200);
    connection.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn http2_not_negotiated() {
    let server = MockServer::start(Flavor::NoAlpn).await;
    match Connection::connect(&server.origin(), Some(tls_config())).await {
        Err(Error::Closed(outcome)) => assert_eq!(outcome, "Failed to negotiate HTTP/2"),
        other => panic!("unexpected {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn untrusted_server() {
    let server = MockServer::start(Flavor::Ok).await;
    // The platform trust store does not include the test CA.
    let tls = aapns::tls::create_tls_config(Some(&certs_dir().join("client.pem")), None).unwrap();
    match Connection::connect(&server.origin(), Some(tls)).await {
        Err(Error::Io(error)) => assert!(error.to_string().contains("certificate"), "{error}"),
        other => panic!("unexpected {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bad_certificate_environment() {
    let server = MockServer::start(Flavor::BadCertificateEnvironment).await;
    let connection = connect(&server).await;
    match connection.post(&request42()).await {
        Err(Error::Closed(outcome)) => {
            assert!(outcome.contains("BadCertificateEnvironment"), "{outcome}")
        }
        other => panic!("unexpected {other:?}"),
    }
    assert!(connection.is_closing());
    connection.close().await;
}

#[tokio::test]
async fn client_certificate_rejected() {
    let (logs, _guard) = Logs::capture();
    let server = MockServer::start(Flavor::RejectsClientCertificate).await;
    let connection = connect(&server).await;
    match connection.post(&request42()).await {
        Err(Error::Closed(outcome)) => assert!(outcome.contains("UnknownCA"), "{outcome}"),
        other => panic!("unexpected {other:?}"),
    }
    let outcome = connection.outcome().unwrap().to_owned();
    let logs = logs.text();
    assert!(
        logs.lines().any(
            |line| line.contains(" ERROR ") && line.ends_with(&format!("TLS error: {outcome}"))
        ),
        "{logs}"
    );
    connection.close().await;
}
