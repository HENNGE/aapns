//! Functional tests of the high level API against the mock server.

mod common;

use std::time::Instant;

use aapns::api::{Client, SendOptions, Target};
use aapns::config::Priority;
use aapns::errors::{Error, Reason};
use common::*;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sequential() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server).create_client().await.unwrap();
    for _ in 0..4 {
        let started = Instant::now();
        let apns_id = client
            .send_notification("42", &notification(), &SendOptions::default())
            .await
            .unwrap();
        assert_eq!(apns_id.as_deref(), Some(APNS_ID));
        assert_quarter_second(started);
    }
    client.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn parallel() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server).create_client().await.unwrap();
    let started = Instant::now();
    let apns_ids = join_all((0..4).map(|_| {
        let client = client.clone();
        async move {
            client
                .send_notification("42", &notification(), &SendOptions::default())
                .await
        }
    }))
    .await;
    for apns_id in apns_ids {
        assert_eq!(apns_id.unwrap().as_deref(), Some(APNS_ID));
    }
    assert_quarter_second(started);
    client.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bad_token() {
    let server = MockServer::start(Flavor::BadToken).await;
    let client = target(&server).create_client().await.unwrap();
    match client
        .send_notification("42", &notification(), &SendOptions::default())
        .await
    {
        Err(Error::Response(error)) => {
            assert_eq!(error.reason, Reason::BadDeviceToken);
            assert_eq!(error.apns_id.as_deref(), Some(APNS_ID));
            assert_eq!(error.status, 400);
        }
        other => panic!("unexpected {other:?}"),
    }
    client.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closing_client() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server).create_client().await.unwrap();
    let task = tokio::spawn({
        let client = client.clone();
        async move {
            client
                .send_notification("42", &notification(), &SendOptions::default())
                .await
        }
    });
    tokio::task::yield_now().await;
    client.close().await;
    assert!(matches!(task.await.unwrap(), Err(Error::Closed(_))));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closed_client() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server).create_client().await.unwrap();
    client.close().await;
    let result = client
        .send_notification("42", &notification(), &SendOptions::default())
        .await;
    assert!(matches!(result, Err(Error::Closed(_))), "{result:?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn termination() {
    let server = MockServer::start(Flavor::TerminatesConnection).await;
    let client = target(&server).create_client().await.unwrap();
    // The terminating server may break the first request, and definitely
    // breaks the second, which times out after 10s.
    let result = async {
        client
            .send_notification("42", &notification(), &SendOptions::default())
            .await?;
        client
            .send_notification("42", &notification(), &SendOptions::default())
            .await
    }
    .await;
    assert!(matches!(result, Err(Error::Timeout(_))), "{result:?}");
    client.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn request_on_the_wire() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server).create_client().await.unwrap();
    let options = SendOptions {
        apns_id: Some("8f8a1c0e-0000-4000-8000-000000000001".into()),
        expiration: Some(0),
        priority: Priority::Immediately,
        topic: Some("com.example.app".into()),
        collapse_id: Some("collapse".into()),
        time_sensitive: true,
    };
    client
        .send_notification("a1b2c3", &notification(), &options)
        .await
        .unwrap();
    client
        .send_notification("a1b2c3", &notification(), &SendOptions::default())
        .await
        .unwrap();

    let requests = server.requests();
    let full = &requests[0];
    assert_eq!(full.path, "/3/device/a1b2c3");
    assert_eq!(full.header("apns-priority"), Some("10"));
    assert_eq!(full.header("apns-push-type"), Some("alert"));
    assert_eq!(full.header("apns-id"), options.apns_id.as_deref());
    assert_eq!(full.header("apns-expiration"), Some("0"));
    assert_eq!(full.header("apns-topic"), Some("com.example.app"));
    assert_eq!(full.header("apns-collapse-id"), Some("collapse"));
    // An `aps` key, not a header.
    assert_eq!(full.header("interruption-level"), None);
    assert_eq!(
        full.json(),
        serde_json::json!({"aps": {
            "alert": {"title": "title", "body": "body"},
            "interruption-level": "time-sensitive",
        }})
    );

    let minimal = &requests[1];
    assert_eq!(minimal.header("apns-priority"), Some("5"));
    for name in [
        "apns-id",
        "apns-expiration",
        "apns-topic",
        "apns-collapse-id",
    ] {
        assert_eq!(minimal.header(name), None, "{name}");
    }
    assert_eq!(
        minimal.json(),
        serde_json::json!({"aps": {"alert": {"title": "title", "body": "body"}}})
    );
    client.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn missing_client_certificate() {
    let server = MockServer::start(Flavor::Ok).await;
    let mut target = target(&server);
    target.client_cert_path = certs_dir().join("missing.pem");
    let result = target.create_client().await;
    assert!(matches!(result, Err(Error::Tls(_))), "{:?}", result.err());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pool_size() {
    let server = MockServer::start(Flavor::Ok).await;
    let client = target(&server)
        .with_pool_size(3)
        .create_client()
        .await
        .unwrap();
    assert_eq!(client.transport().active(), 3);
    client.close().await;
    let result = target(&server).with_pool_size(0).create_client().await;
    assert!(matches!(result, Err(Error::InvalidPoolSize)));
}
