//! Mid level API: a fixed-size pool of connections with automatic replacement
//! and retries.
//!
//! A pool can handle up to `1000 * size` (current APNs limit) concurrent
//! requests and queues requests beyond that, retrying with exponential
//! back-off until the request expires. It is thus suitable for bursty traffic
//! and for sending notifications en masse.
//!
//! ```no_run
//! use aapns::connection::Request;
//! use aapns::errors::Error;
//! use aapns::pool::Pool;
//! use aapns::tls::create_tls_config;
//! use serde_json::json;
//! use std::path::Path;
//! use std::time::Duration;
//!
//! # async fn example() -> aapns::errors::Result<()> {
//! let tls = create_tls_config(Some(Path::new("/path/to/push/cert.pem")), None)?;
//! let pool = Pool::connect("https://api.development.push.apple.com", 10, Some(tls)).await?;
//! let request = Request::new(
//!     "/3/device/42...42",
//!     &[("apns-push-type", "alert"), ("apns-topic", "com.app.your")],
//!     &json!({"aps": {"alert": {"body": "Wakey-wakey, ham and bakey!"}}}),
//! )?
//! .with_timeout(Some(Duration::from_secs(10))); // without a timeout, the pool may retry forever
//! match pool.post(&request).await {
//!     Ok(response) => assert_eq!(response.code, 200),
//!     Err(Error::Timeout(_)) => {} // the notification has expired
//!     Err(Error::Closed(_)) => {}  // the pool is done, e.g. the client certificate has expired
//!     Err(_) => {}                 // rare
//! }
//! pool.close().await;
//! # Ok(())
//! # }
//! ```

use std::fmt;
use std::future::Future;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;

use rustls::ClientConfig;
use tokio::sync::Notify;
use tokio::task::{JoinHandle, JoinSet};

use crate::connection::{Connection, Origin, Request, Response};
use crate::errors::{Error, Result};
use crate::{Counted, lock, tls};

/// Default number of connections in a pool.
pub const DEFAULT_POOL_SIZE: usize = 2;
/// The pool checks its connections at least this often.
const MAINTENANCE_INTERVAL: Duration = Duration::from_secs(1);
/// Upper bound on the delay between retries.
const MAX_RETRY_DELAY: Duration = Duration::from_secs(10);
/// A pool is shut down when one of its connections is closed with this reason.
const FATAL_OUTCOME: &str = "BadCertificateEnvironment";

/// Something requests can be posted to: a [`Pool`], a [`Connection`], or a
/// test double.
pub trait Transport: Send + Sync {
    /// Posts the request.
    fn post(&self, request: &Request) -> impl Future<Output = Result<Response>> + Send;
    /// Closes the transport and frees up its resources.
    fn close(&self) -> impl Future<Output = ()> + Send;
}

impl Transport for Connection {
    async fn post(&self, request: &Request) -> Result<Response> {
        Connection::post(self, request).await
    }

    async fn close(&self) {
        Connection::close(self).await
    }
}

impl Transport for Pool {
    async fn post(&self, request: &Request) -> Result<Response> {
        Pool::post(self, request).await
    }

    async fn close(&self) {
        Pool::close(self).await
    }
}

/// State of a [`Pool`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PoolState {
    /// Accepting requests.
    Active,
    /// Shutting down, for example because the client certificate was rejected.
    Closing,
    /// Closed.
    Closed,
}

impl PoolState {
    /// Lower case name of the state.
    pub const fn as_str(self) -> &'static str {
        match self {
            PoolState::Active => "active",
            PoolState::Closing => "closing",
            PoolState::Closed => "closed",
        }
    }
}

impl fmt::Display for PoolState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

struct Members {
    size: usize,
    active: Vec<Connection>,
    dying: Vec<Connection>,
    closing: bool,
    closed: bool,
    outcome: Option<String>,
}

impl Members {
    fn is_closing(&self) -> bool {
        self.closing || self.closed
    }

    /// Shuts the pool down if APNs rejected the client certificate.
    ///
    /// All connections share the same TLS configuration, and thus the same
    /// client certificate. If APNs closes one connection because of it, the
    /// entire pool is done for.
    fn termination_hook(&mut self, connection: &Connection) {
        if self.outcome.is_none() && connection.outcome() == Some(FATAL_OUTCOME) {
            tracing::error!(
                outcome = FATAL_OUTCOME,
                "client certificate rejected, closing the connection pool"
            );
            self.closing = true;
            self.outcome = Some(FATAL_OUTCOME.to_owned());
        }
    }
}

struct PoolInner {
    origin: Origin,
    tls: Arc<ClientConfig>,
    members: Mutex<Members>,
    errors: AtomicU64,
    completed: AtomicU64,
    retrying: AtomicUsize,
    next: AtomicUsize,
    wakeup: Arc<Notify>,
    maintenance: Mutex<Option<JoinHandle<()>>>,
}

impl PoolInner {
    fn closed_error(&self) -> Error {
        let members = lock(&self.members);
        Error::Closed(members.outcome.clone().unwrap_or_else(|| "Closed".into()))
    }

    fn ensure_open(&self) -> Result<()> {
        if lock(&self.members).is_closing() {
            Err(self.closed_error())
        } else {
            Ok(())
        }
    }

    fn connections(&self) -> Vec<Connection> {
        let members = lock(&self.members);
        members
            .active
            .iter()
            .chain(&members.dying)
            .cloned()
            .collect()
    }
}

impl Drop for PoolInner {
    fn drop(&mut self) {
        if let Some(maintenance) = lock(&self.maintenance).take() {
            maintenance.abort();
        }
    }
}

/// A fixed-size connection pool with automatic replacement of connections.
///
/// Cloning a pool is cheap and yields another handle to the same pool.
#[derive(Clone)]
pub struct Pool {
    inner: Arc<PoolInner>,
}

impl Pool {
    /// Connects `size` connections to `origin`, which must be of the form
    /// `https://<host>[:<port>]`.
    ///
    /// Without a TLS configuration, the server is verified against the
    /// platform's trust store and no client certificate is presented.
    /// Must be called within a Tokio runtime.
    pub async fn connect(
        origin: &str,
        size: usize,
        tls: Option<Arc<ClientConfig>>,
    ) -> Result<Pool> {
        if size < 1 {
            return Err(Error::InvalidPoolSize);
        }
        let origin = Origin::parse(origin)?;
        let tls = match tls {
            Some(tls) => tls::with_h2_alpn(tls),
            None => tls::create_tls_config(None, None)?,
        };
        let wakeup = Arc::new(Notify::new());

        let mut connecting = JoinSet::new();
        for _ in 0..size {
            let connect =
                Connection::connect_origin(origin.clone(), tls.clone(), Some(wakeup.clone()));
            connecting.spawn(connect);
        }
        let mut active = Vec::with_capacity(size);
        while let Some(connected) = connecting.join_next().await {
            match connected {
                Ok(connection) => active.push(connection?),
                Err(error) => std::panic::resume_unwind(error.into_panic()),
            }
        }

        let inner = Arc::new(PoolInner {
            origin,
            tls,
            members: Mutex::new(Members {
                size,
                active,
                dying: Vec::new(),
                closing: false,
                closed: false,
                outcome: None,
            }),
            errors: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            retrying: AtomicUsize::new(0),
            next: AtomicUsize::new(0),
            wakeup: wakeup.clone(),
            maintenance: Mutex::new(None),
        });
        let maintenance = tokio::spawn(maintain(Arc::downgrade(&inner), wakeup));
        *lock(&inner.maintenance) = Some(maintenance);
        Ok(Pool { inner })
    }

    /// Posts `request` on a connection in this pool, retrying with exponential
    /// back-off while all connections are busy or being replaced, until the
    /// request expires.
    ///
    /// A request in flight on a connection that breaks is retried on another
    /// connection, so the server may receive it twice. Set the
    /// `apns-collapse-id` header to make sure the user sees the notification
    /// only once.
    pub async fn post(&self, request: &Request) -> Result<Response> {
        let mut outcome = Outcome {
            pool: &self.inner,
            recorded: false,
        };
        let result = self.post_with_retries(request).await;
        outcome.record(result.is_ok());
        result
    }

    async fn post_with_retries(&self, request: &Request) -> Result<Response> {
        let inner = &*self.inner;
        let mut attempt = 0;
        loop {
            inner.ensure_open()?;
            match self.post_once(request).await {
                Err(Error::Blocked) => {}
                result => return result,
            }
            inner.ensure_open()?;

            let delay = retry_delay(attempt);
            attempt += 1;
            if request.time_left()?.is_some_and(|left| left < delay) {
                return Err(Error::Timeout(
                    "Request would time out awaiting retry".into(),
                ));
            }
            let _retrying = Counted::new(&inner.retrying);
            tokio::time::sleep(delay).await;
        }
    }

    async fn post_once(&self, request: &Request) -> Result<Response> {
        let inner = &*self.inner;
        let active = lock(&inner.members).active.clone();
        let start = inner.next.fetch_add(1, Ordering::Relaxed);
        for i in 0..active.len() {
            let connection = &active[(start + i) % active.len()];
            inner.ensure_open()?;
            if connection.is_closing() {
                continue;
            }
            match connection.post(request).await {
                Err(Error::Blocked | Error::Closed(_)) => continue,
                result => return result,
            }
        }
        Err(Error::Blocked)
    }

    /// Closes the pool and all its connections. Requests in flight fail with
    /// [`Error::Closed`].
    pub async fn close(&self) {
        let inner = &*self.inner;
        {
            let mut members = lock(&inner.members);
            members.closing = true;
            members.outcome.get_or_insert_with(|| "Closed".into());
        }
        let maintenance = lock(&inner.maintenance).take();
        if let Some(maintenance) = maintenance {
            maintenance.abort();
            let _ = maintenance.await;
        }
        for connection in inner.connections() {
            connection.close().await;
        }
        lock(&inner.members).closed = true;
    }

    /// Changes the number of connections in the pool. The pool grows or
    /// shrinks in the background; connections removed from the pool finish
    /// their requests in flight first.
    pub fn resize(&self, size: usize) -> Result<()> {
        if size < 1 {
            return Err(Error::InvalidPoolSize);
        }
        lock(&self.inner.members).size = size;
        self.inner.wakeup.notify_one();
        Ok(())
    }

    /// The state of the pool.
    pub fn state(&self) -> PoolState {
        let members = lock(&self.inner.members);
        if members.closed {
            PoolState::Closed
        } else if members.closing {
            PoolState::Closing
        } else {
            PoolState::Active
        }
    }

    /// Why the pool is closing or closed.
    pub fn outcome(&self) -> Option<String> {
        lock(&self.inner.members).outcome.clone()
    }

    /// The target number of connections.
    pub fn size(&self) -> usize {
        lock(&self.inner.members).size
    }

    /// Number of connections taking new requests.
    pub fn active(&self) -> usize {
        lock(&self.inner.members).active.len()
    }

    /// Number of connections being retired.
    pub fn dying(&self) -> usize {
        lock(&self.inner.members).dying.len()
    }

    /// Count of the requests that were sent and await a response.
    pub fn inflight(&self) -> usize {
        self.inner
            .connections()
            .iter()
            .map(Connection::inflight)
            .sum()
    }

    /// Count of the requests that are still to be sent out.
    pub fn buffered(&self) -> usize {
        self.inner
            .connections()
            .iter()
            .map(Connection::buffered)
            .sum()
    }

    /// Total count of pending requests, including those awaiting a retry.
    pub fn pending(&self) -> usize {
        self.inner
            .connections()
            .iter()
            .map(Connection::pending)
            .sum::<usize>()
            + self.retrying()
    }

    /// Count of the requests waiting to be retried.
    pub fn retrying(&self) -> usize {
        self.inner.retrying.load(Ordering::SeqCst)
    }

    /// Count of the requests that got a response.
    pub fn completed(&self) -> u64 {
        self.inner.completed.load(Ordering::SeqCst)
    }

    /// Count of the requests that failed or were cancelled.
    pub fn errors(&self) -> u64 {
        self.inner.errors.load(Ordering::SeqCst)
    }
}

impl fmt::Display for Pool {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let state = self.state();
        write!(
            f,
            "<Pool {state} {} active:{} dying:{}",
            self.inner.origin,
            self.active(),
            self.dying()
        )?;
        if state != PoolState::Closed {
            write!(
                f,
                " buffered:{} inflight:{}",
                self.buffered(),
                self.inflight()
            )?;
        }
        write!(
            f,
            " retrying:{} completed:{} errors:{}>",
            self.retrying(),
            self.completed(),
            self.errors()
        )
    }
}

impl fmt::Debug for Pool {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self, f)
    }
}

/// Counts a request as completed or as an error, including when the request
/// is cancelled.
struct Outcome<'a> {
    pool: &'a PoolInner,
    recorded: bool,
}

impl Outcome<'_> {
    fn record(&mut self, ok: bool) {
        self.recorded = true;
        let counter = if ok {
            &self.pool.completed
        } else {
            &self.pool.errors
        };
        counter.fetch_add(1, Ordering::SeqCst);
    }
}

impl Drop for Outcome<'_> {
    fn drop(&mut self) {
        if !self.recorded {
            self.pool.errors.fetch_add(1, Ordering::SeqCst);
        }
    }
}

/// 1ms, 3ms, 10ms, 32ms, 100ms, ... up to [`MAX_RETRY_DELAY`].
fn retry_delay(attempt: u32) -> Duration {
    let seconds = 10f64.powf(-3.0 + 0.5 * f64::from(attempt));
    Duration::from_secs_f64(seconds.min(MAX_RETRY_DELAY.as_secs_f64()))
}

async fn maintain(pool: Weak<PoolInner>, wakeup: Arc<Notify>) {
    loop {
        {
            let Some(inner) = pool.upgrade() else { return };
            if !maintain_once(&inner).await {
                return;
            }
        }
        // Woken up early when a connection starts closing or the pool is resized.
        let _ = tokio::time::timeout(MAINTENANCE_INTERVAL, wakeup.notified()).await;
    }
}

/// Retires broken connections, closes idle retired connections and replaces
/// connections. Returns `false` once the pool is closing.
async fn maintain_once(inner: &PoolInner) -> bool {
    let idle = {
        let mut members = lock(&inner.members);
        if members.is_closing() {
            return false;
        }
        let (closing, active): (Vec<_>, Vec<_>) =
            members.active.drain(..).partition(Connection::is_closing);
        members.active = active;
        for connection in closing {
            members.termination_hook(&connection);
            members.dying.push(connection);
        }
        while members.active.len() > members.size {
            let Some(connection) = members.active.pop() else {
                break;
            };
            connection.begin_closing();
            members.termination_hook(&connection);
            members.dying.push(connection);
        }
        let mut idle = Vec::new();
        for connection in std::mem::take(&mut members.dying) {
            if connection.is_closed() {
                // Closed by the server or by itself: forget it.
                members.termination_hook(&connection);
            } else if connection.pending() == 0 {
                idle.push(connection);
            } else {
                members.dying.push(connection);
            }
        }
        idle
    };

    for connection in idle {
        connection.close().await;
        let mut members = lock(&inner.members);
        members.termination_hook(&connection);
        if members.is_closing() {
            return false;
        }
    }

    loop {
        {
            let members = lock(&inner.members);
            if members.is_closing() {
                return false;
            }
            if members.active.len() >= members.size {
                return true;
            }
        }
        match Connection::connect_origin(
            inner.origin.clone(),
            inner.tls.clone(),
            Some(inner.wakeup.clone()),
        )
        .await
        {
            Ok(connection) => {
                let mut members = lock(&inner.members);
                members.termination_hook(&connection);
                members.active.push(connection);
            }
            Err(error) => {
                tracing::error!(origin = %inner.origin, %error, "failed to create APNs connection");
                return true;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_delays() {
        let delays: Vec<_> = (0..6)
            .map(|attempt| retry_delay(attempt).as_secs_f64())
            .collect();
        let expected = [0.001, 0.00316, 0.01, 0.0316, 0.1, 0.316];
        for (delay, expected) in delays.iter().zip(expected) {
            assert!((delay - expected).abs() / expected < 0.01, "{delays:?}");
        }
        assert_eq!(retry_delay(8), Duration::from_secs(10));
        assert_eq!(retry_delay(u32::MAX), MAX_RETRY_DELAY);
    }

    #[tokio::test]
    async fn bad_pool_size() {
        assert!(matches!(
            Pool::connect("https://localhost:1234", 0, None).await,
            Err(Error::InvalidPoolSize)
        ));
    }

    #[tokio::test]
    async fn bad_origin() {
        assert!(matches!(
            Pool::connect("just.host.name.is.invalid", 2, None).await,
            Err(Error::InvalidOrigin(_))
        ));
    }
}
