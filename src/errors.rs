use std::fmt;
use std::io;

use crate::models::PushType;

pub type Result<T, E = Error> = std::result::Result<T, E>;

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum Error {
    /// This connection can't send more data at this point, can try later.
    #[error("connection is busy, try again later")]
    Blocked,
    /// This connection is now closed, try another.
    #[error("connection closed: {0}")]
    Closed(String),
    /// The request deadline has passed.
    #[error("{0}")]
    Timeout(String),
    /// Response was weird.
    #[error("malformed response: {0}")]
    Format(String),
    /// Server response was larger than allowed.
    #[error("response too large: {0}")]
    ResponseTooLarge(String),
    #[error("stream reset: {:?}", h2::Reason::from(*.0))]
    StreamReset(u32),
    #[error(transparent)]
    Response(#[from] ResponseError),
    #[error("invalid origin {0:?}, expected https://<host>[:<port>]")]
    InvalidOrigin(String),
    #[error("connection pool size must be strictly positive")]
    InvalidPoolSize,
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error(
        "notification payload is {size} bytes, exceeding the maximum payload size of {limit} bytes for {push_type} notifications"
    )]
    PayloadTooLarge {
        push_type: PushType,
        size: usize,
        limit: usize,
    },
    #[error("TLS configuration error: {0}")]
    Tls(String),
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error("simulator push failed: {0}")]
    Simulator(String),
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("APNs rejected the notification (HTTP {status}): {reason}")]
pub struct ResponseError {
    pub status: u16,
    pub reason: Reason,
    pub apns_id: Option<String>,
}

macro_rules! reasons {
    ($($name:ident),* $(,)?) => {
        #[derive(Debug, Clone, PartialEq, Eq, Hash)]
        #[non_exhaustive]
        pub enum Reason {
            $($name,)*
            Unknown(Option<String>),
        }

        impl Reason {
            pub fn parse(reason: Option<&str>) -> Reason {
                match reason {
                    $(Some(stringify!($name)) => Reason::$name,)*
                    other => Reason::Unknown(other.map(str::to_owned)),
                }
            }

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
}
