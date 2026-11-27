//! Asynchronous Apple Push Notification Service client.
//!
//! See the documentation at <https://aapns.readthedocs.io>.

pub mod api;
pub mod config;
pub mod connection;
pub mod errors;
pub mod models;
pub mod pool;
pub mod tls;

pub use http;
pub use rustls;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard, PoisonError};

pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

/// Increments the counter while alive
pub(crate) struct Counted<'a>(&'a AtomicUsize);

impl<'a> Counted<'a> {
    pub fn new(counter: &'a AtomicUsize) -> Self {
        counter.fetch_add(1, Ordering::SeqCst);
        Counted(counter)
    }

    /// Unless the counter is at `limit` already
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
