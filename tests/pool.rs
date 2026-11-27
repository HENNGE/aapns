mod common;

use std::time::{Duration, Instant};

use aapns::errors::Error;
use aapns::pool::{Pool, PoolState};
use common::*;

async fn connect(server: &MockServer) -> Pool {
    Pool::connect(&server.origin(), 2, Some(tls_config()))
        .await
        .unwrap()
}

async fn post_concurrently(
    pool: &Pool,
    count: usize,
) -> Vec<aapns::errors::Result<aapns::connection::Response>> {
    join_all((0..count).map(|_| {
        let pool = pool.clone();
        async move { pool.post(&request42()).await }
    }))
    .await
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sequential() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    for _ in 0..4 {
        let started = Instant::now();
        let response = pool.post(&request42()).await.unwrap();
        assert_eq!(response.code, 200);
        assert_eq!(response.apns_id(), Some(APNS_ID));
        assert_quarter_second(started);
    }
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn parallel() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    let started = Instant::now();
    for response in post_concurrently(&pool, 4).await {
        let response = response.unwrap();
        assert_eq!(response.code, 200);
        assert_eq!(response.apns_id(), Some(APNS_ID));
    }
    assert_quarter_second(started);
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bad_token() {
    let server = MockServer::start(Flavor::BadToken).await;
    let pool = connect(&server).await;
    let response = pool.post(&request42()).await.unwrap();
    assert_eq!(response.code, 400);
    assert_eq!(response.apns_id(), Some(APNS_ID));
    assert_eq!(response.reason(), Some("BadDeviceToken"));
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closing_pool() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    let task = tokio::spawn({
        let pool = pool.clone();
        async move { pool.post(&request42()).await }
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    pool.close().await;
    assert!(matches!(task.await.unwrap(), Err(Error::Closed(_))));
    assert_eq!(pool.state(), PoolState::Closed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closed_pool() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    pool.close().await;
    match pool.post(&request42()).await {
        Err(Error::Closed(outcome)) => assert_eq!(outcome, "Closed"),
        other => panic!("unexpected {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn termination() {
    let server = MockServer::start(Flavor::TerminatesConnection).await;
    let pool = connect(&server).await;
    // terminating server may break the first request, and definitely breaks the second
    let second = Duration::from_secs(1);
    let result = async {
        pool.post(&request42().with_timeout(Some(second))).await?;
        pool.post(&request42().with_timeout(Some(second))).await
    }
    .await;
    assert!(matches!(result, Err(Error::Timeout(_))), "{result:?}");
    pool.close().await;
}

/// Expected run time is ~3s for 2000 requests, max here is 10.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn performance() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    let responses = tokio::time::timeout(Duration::from_secs(10), post_concurrently(&pool, 2000))
        .await
        .expect("2000 requests should complete in 10s");
    for response in responses {
        assert_eq!(response.unwrap().code, 200);
    }
    assert_eq!(pool.completed(), 2000);
    assert_eq!(pool.errors(), 0);
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resize() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    pool.post(&request42()).await.unwrap();
    assert_eq!(pool.active(), 2);

    pool.resize(4).unwrap();
    assert_eq!(pool.size(), 4);
    wait_until(Duration::from_secs(1), || pool.active() == 4).await;
    for response in post_concurrently(&pool, 4).await {
        assert_eq!(response.unwrap().code, 200);
    }

    pool.resize(2).unwrap();
    wait_until(Duration::from_secs(1), || pool.active() == 2).await;
    for response in post_concurrently(&pool, 4).await {
        assert_eq!(response.unwrap().code, 200);
    }
    wait_until(Duration::from_secs(2), || pool.dying() == 0).await;
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resize_bad_size() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    assert!(matches!(pool.resize(0), Err(Error::InvalidPoolSize)));
    assert_eq!(pool.size(), 2);
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pool_state() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    assert_eq!(pool.state(), PoolState::Active);
    assert_eq!(
        (pool.buffered(), pool.inflight(), pool.pending()),
        (0, 0, 0)
    );
    assert_eq!((pool.completed(), pool.errors()), (0, 0));

    pool.post(&request42()).await.unwrap();
    assert_eq!(
        (pool.buffered(), pool.inflight(), pool.pending()),
        (0, 0, 0)
    );
    assert_eq!((pool.completed(), pool.errors()), (1, 0));
    assert_eq!(
        pool.to_string(),
        format!(
            "<Pool active https://localhost:{} active:2 dying:0 buffered:0 inflight:0 retrying:0 completed:1 errors:0>",
            server.port()
        )
    );
    pool.close().await;
    assert_eq!(pool.state(), PoolState::Closed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn errors_are_counted() {
    let server = MockServer::start(Flavor::Ok).await;
    let pool = connect(&server).await;
    // A request that expires while in flight.
    let result = pool
        .post(&request42().with_timeout(Some(Duration::from_millis(50))))
        .await;
    assert!(matches!(result, Err(Error::Timeout(_))), "{result:?}");
    // A request cancelled by the caller.
    let _ = tokio::time::timeout(Duration::from_millis(50), pool.post(&request42())).await;
    assert_eq!((pool.completed(), pool.errors()), (0, 2));
    pool.close().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bad_certificate_environment() {
    let server = MockServer::start(Flavor::BadCertificateEnvironment).await;
    let pool = connect(&server).await;
    match pool.post(&request42()).await {
        Err(Error::Closed(outcome)) => assert_eq!(outcome, "BadCertificateEnvironment"),
        other => panic!("unexpected {other:?}"),
    }
    assert_eq!(pool.state(), PoolState::Closing);
    assert_eq!(pool.outcome().as_deref(), Some("BadCertificateEnvironment"));
    pool.close().await;
    assert_eq!(pool.state(), PoolState::Closed);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn broken_connections_are_replaced() {
    let server = MockServer::start(Flavor::RejectsClientCertificate).await;
    let pool = connect(&server).await;
    let result = pool
        .post(&request42().with_timeout(Some(Duration::from_secs(1))))
        .await;
    assert!(matches!(result, Err(Error::Timeout(_))), "{result:?}");
    assert_eq!(pool.state(), PoolState::Active);
    pool.close().await;
}
