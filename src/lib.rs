//! Asynchronous Apple Push Notification service (APNs) client.
//!
//! * Requires TLS 1.2 or better
//! * Requires a [Tokio](https://tokio.rs) runtime
//!
//! # Quickstart
//!
//! ```no_run
//! use aapns::api::{Client, SendOptions, Server, Target};
//! use aapns::config::Priority;
//! use aapns::models::{Alert, Localized, Notification};
//!
//! # async fn example() -> aapns::errors::Result<()> {
//! let client = Server::production("/path/to/push/cert.pem").create_client().await?;
//! let notification = Notification {
//!     badge: Some(42),
//!     ..Notification::new(Alert::new(Localized::with_args("Hello World!", ["foo", "bar"])))
//! };
//! let options = SendOptions { priority: Priority::Immediately, ..SendOptions::default() };
//! let apns_id = client.send_notification("my-device-token", &notification, &options).await?;
//! println!("Sent push notification with ID {apns_id:?}");
//! client.close().await;
//! # Ok(())
//! # }
//! ```
//!
//! # Layers
//!
//! * [`api`]: send [`Notification`](models::Notification)s to APNs or a
//!   simulator.
//! * [`pool`]: post requests over a fixed-size pool of connections, with
//!   automatic reconnection and retries.
//! * [`connection`]: post requests over a single HTTP/2 connection.
//!
//! # Idempotency
//!
//! How to ensure a notification is displayed only once in the presence of
//! network errors?
//!
//! If the underlying TCP connection breaks or times out, some notifications
//! may still be in flight. It is then impossible to tell whether such a
//! notification has not yet reached the Apple server, or it has but the
//! server's response has not reached the client. The same holds when the
//! server shuts down an HTTP/2 connection, for example for maintenance. The
//! pool retries such requests on another connection.
//!
//! APNs provides the `apns-collapse-id` header for this. In the simple case,
//! set it to a random value, so that if aapns retransmits "one time too
//! many", the user still sees a single notification:
//!
//! ```no_run
//! # use aapns::api::{Client, SendOptions};
//! # async fn example(client: impl Client, notification: aapns::models::Notification) -> aapns::errors::Result<()> {
//! let options = SendOptions {
//!     collapse_id: Some(format!("{:x}", std::process::id())), // any unique value
//!     ..SendOptions::default()
//! };
//! client.send_notification("my-device-token", &notification, &options).await?;
//! # Ok(())
//! # }
//! ```
//!
//! If you rely on collapsing notifications otherwise, beware that retransmitted
//! notifications may arrive out of order and the user may ultimately see stale
//! data.
//!
//! # Dead connections
//!
//! Connections use TCP keepalive (probes after 60s idle, every 10s, giving up
//! after 3) and, on Linux, `TCP_USER_TIMEOUT` (30s), so that a connection
//! silently dropped by a middlebox such as a NAT gateway is detected and
//! replaced in about a minute rather than after the OS retransmission limit.
//!
//! TLS errors after the handshake are logged at error level as
//! `TLS error: ...` and recorded as the connection's
//! [outcome](connection::Connection::outcome). With TLS 1.3, APNs checks the
//! client certificate after the client considers the handshake complete, so
//! a rejected certificate shows up this way rather than as a connection error.

#![warn(missing_docs)]

pub mod api;
pub mod config;
pub mod connection;
pub mod errors;
pub mod models;
mod monitor;
pub mod pool;
pub mod tls;

pub use http;
pub use rustls;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard, PoisonError};

/// Locks a mutex, ignoring poisoning: the protected data is always consistent.
pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Increments a counter for as long as it is alive.
pub(crate) struct Counted<'a>(&'a AtomicUsize);

impl<'a> Counted<'a> {
    pub fn new(counter: &'a AtomicUsize) -> Self {
        counter.fetch_add(1, Ordering::SeqCst);
        Counted(counter)
    }

    /// Increments the counter unless it is at `limit` already.
    pub fn acquire(counter: &'a AtomicUsize, limit: usize) -> Option<Self> {
        if counter.fetch_add(1, Ordering::SeqCst) < limit {
            Some(Counted(counter))
        } else {
            counter.fetch_sub(1, Ordering::SeqCst);
            None
        }
    }
}

impl Drop for Counted<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}
