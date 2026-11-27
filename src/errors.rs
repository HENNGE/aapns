//! Error types.

use std::borrow::Cow;
use std::fmt;
use std::io;

use crate::models::PushType;

/// Result type used throughout this crate.
pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Errors raised by aapns.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum Error {
    /// This connection cannot send more requests right now; try again later
    /// or use another connection.
    #[error("connection is busy, try again later")]
    Blocked,

    /// The connection (or connection pool) is closed; the condition is
    /// permanent. Contains the reason the connection was closed, for example
    /// `BadCertificateEnvironment` when APNs rejected the client certificate.
    #[error("connection closed: {0}")]
    Closed(String),

    /// The request has timed out, or would time out before it could be retried.
    #[error("{0}")]
    Timeout(String),

    /// The server sent a response that could not be parsed.
    #[error("malformed response: {0}")]
    Format(String),

    /// The server sent an abnormally large response.
    #[error("response too large: {0}")]
    ResponseTooLarge(String),

    /// The HTTP/2 stream used for the request was reset. Contains the HTTP/2
    /// error code.
    #[error("stream reset: {}", h2_error_name(*.0))]
    StreamReset(u32),

    /// APNs rejected the notification.
    #[error(transparent)]
    Response(#[from] ResponseError),

    /// The origin is not of the form `https://<host>[:<port>]`.
    #[error("invalid origin {0:?}, expected https://<host>[:<port>]")]
    InvalidOrigin(String),

    /// A connection pool must have at least one connection.
    #[error("connection pool size must be strictly positive")]
    InvalidPoolSize,

    /// The request could not be constructed.
    #[error("invalid request: {0}")]
    InvalidRequest(String),

    /// The encoded notification is larger than APNs accepts.
    #[error(
        "notification payload is {size} bytes, exceeding the maximum payload size of {limit} bytes for {push_type} notifications"
    )]
    PayloadTooLarge {
        /// Push type of the notification, which determines the limit.
        push_type: PushType,
        /// Size of the encoded payload.
        size: usize,
        /// Maximum allowed size.
        limit: usize,
    },

    /// Client certificate or trust store could not be loaded.
    #[error("TLS configuration error: {0}")]
    Tls(String),

    /// Network error while establishing a connection.
    #[error(transparent)]
    Io(#[from] io::Error),

    /// Pushing to a simulator via `xcrun simctl` failed.
    #[error("simulator push failed: {0}")]
    Simulator(String),
}

/// A notification rejected by APNs.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("APNs rejected the notification (HTTP {status}): {reason}")]
pub struct ResponseError {
    /// HTTP status of the response.
    pub status: u16,
    /// Reason APNs gave for the rejection.
    pub reason: Reason,
    /// `apns-id` of the rejected notification.
    pub apns_id: Option<String>,
}

macro_rules! reasons {
    ($($name:ident),* $(,)?) => {
        /// Reason given by APNs for rejecting a notification.
        ///
        /// See Apple's documentation on handling notification responses for
        /// the meaning of each value.
        #[derive(Debug, Clone, PartialEq, Eq, Hash)]
        #[non_exhaustive]
        pub enum Reason {
            $(
                #[allow(missing_docs)]
                $name,
            )*
            /// A reason not known to this crate, or `None` if the response did
            /// not include one.
            Unknown(Option<String>),
        }

        impl Reason {
            /// Parses the `reason` field of an APNs error response.
            pub fn parse(reason: Option<&str>) -> Reason {
                match reason {
                    $(Some(stringify!($name)) => Reason::$name,)*
                    other => Reason::Unknown(other.map(str::to_owned)),
                }
            }

            /// The reason as sent by APNs.
            pub fn as_str(&self) -> &str {
                match self {
                    $(Reason::$name => stringify!($name),)*
                    Reason::Unknown(Some(reason)) => reason,
                    Reason::Unknown(None) => "unknown",
                }
            }
        }
    };
}

reasons!(
    BadCollapseId,
    BadDeviceToken,
    BadExpirationDate,
    BadMessageId,
    BadPriority,
    BadTopic,
    DeviceTokenNotForTopic,
    DuplicateHeaders,
    IdleTimeout,
    MissingDeviceToken,
    MissingTopic,
    PayloadEmpty,
    BadCertificate,
    BadCertificateEnvironment,
    ExpiredProviderToken,
    Forbidden,
    InvalidProviderToken,
    MissingProviderToken,
    BadPath,
    MethodNotAllowed,
    Unregistered,
    PayloadTooLarge,
    TooManyProviderTokenUpdates,
    TooManyRequests,
    InternalServerError,
    ServiceUnavailable,
    Shutdown,
);

impl fmt::Display for Reason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Name of an HTTP/2 error code (RFC 9113, section 7).
pub(crate) fn h2_error_name(code: u32) -> Cow<'static, str> {
    let name = match code {
        0x0 => "NO_ERROR",
        0x1 => "PROTOCOL_ERROR",
        0x2 => "INTERNAL_ERROR",
        0x3 => "FLOW_CONTROL_ERROR",
        0x4 => "SETTINGS_TIMEOUT",
        0x5 => "STREAM_CLOSED",
        0x6 => "FRAME_SIZE_ERROR",
        0x7 => "REFUSED_STREAM",
        0x8 => "CANCEL",
        0x9 => "COMPRESSION_ERROR",
        0xa => "CONNECT_ERROR",
        0xb => "ENHANCE_YOUR_CALM",
        0xc => "INADEQUATE_SECURITY",
        0xd => "HTTP_1_1_REQUIRED",
        other => return Cow::Owned(format!("error code {other:#x}")),
    };
    Cow::Borrowed(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reason_round_trip() {
        for name in ["BadDeviceToken", "BadCertificateEnvironment", "Shutdown"] {
            let reason = Reason::parse(Some(name));
            assert!(!matches!(reason, Reason::Unknown(_)));
            assert_eq!(reason.as_str(), name);
        }
    }

    #[test]
    fn unknown_reason() {
        assert_eq!(
            Reason::parse(Some("SomethingNew")),
            Reason::Unknown(Some("SomethingNew".into()))
        );
        assert_eq!(
            Reason::parse(Some("SomethingNew")).to_string(),
            "SomethingNew"
        );
        assert_eq!(Reason::parse(None), Reason::Unknown(None));
    }

    #[test]
    fn response_error_display() {
        let error = Error::from(ResponseError {
            status: 400,
            reason: Reason::BadDeviceToken,
            apns_id: None,
        });
        assert_eq!(
            error.to_string(),
            "APNs rejected the notification (HTTP 400): BadDeviceToken"
        );
    }

    #[test]
    fn h2_error_names() {
        assert_eq!(h2_error_name(0), "NO_ERROR");
        assert_eq!(h2_error_name(7), "REFUSED_STREAM");
        assert_eq!(h2_error_name(0x42), "error code 0x42");
    }
}
