//! Monitors the transport underneath `h2` for events `h2` does not expose.
//!
//! * `GOAWAY` frames: when APNs is unhappy with a connection as a whole, for
//!   example because the client certificate is for the wrong environment, it
//!   sends a `GOAWAY` frame whose debug data is a JSON object such as
//!   `{"reason": "BadCertificateEnvironment"}`. `h2` does not expose that
//!   debug data, so the bytes read from the server are scanned for `GOAWAY`
//!   frames before `h2` processes them. This also tells us that the server is
//!   shutting the connection down as soon as the frame arrives.
//! * I/O errors: `h2` reduces I/O errors to their kind and message, losing
//!   the OS error code of e.g. `ETIMEDOUT` and the type of TLS errors such as
//!   a client certificate rejected after a TLS 1.3 handshake. The first error
//!   is reported here with its original type.

use std::io;
use std::pin::Pin;
use std::task::{Context, Poll, ready};

use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};

use crate::errors::h2_error_name;

const FRAME_HEADER_LEN: usize = 9;
const GOAWAY: u8 = 0x7;
/// Last stream id and error code precede the debug data.
const GOAWAY_FIXED_LEN: usize = 8;
/// Debug data beyond this length is ignored.
const MAX_DEBUG_DATA: usize = 1024;

/// A `GOAWAY` frame received from the server.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct GoAway {
    pub last_stream_id: u32,
    pub error_code: u32,
    pub debug_data: Vec<u8>,
}

impl GoAway {
    /// Why the server is closing the connection: the `reason` of the JSON
    /// debug data, the debug data itself, or the error code name.
    pub fn outcome(&self) -> String {
        if self.debug_data.is_empty() {
            return h2_error_name(self.error_code).into_owned();
        }
        let json = serde_json::from_slice::<serde_json::Value>(&self.debug_data).ok();
        match json.as_ref().and_then(|json| json.get("reason")?.as_str()) {
            Some(reason) => reason.to_owned(),
            None => {
                let end = self.debug_data.len().min(100);
                String::from_utf8_lossy(&self.debug_data[..end]).into_owned()
            }
        }
    }
}

#[derive(Debug)]
enum State {
    Header {
        buf: [u8; FRAME_HEADER_LEN],
        filled: usize,
    },
    Skip {
        remaining: usize,
    },
    GoAway {
        remaining: usize,
        payload: Vec<u8>,
    },
}

/// Incremental parser of the server-to-client HTTP/2 byte stream.
#[derive(Debug)]
pub(crate) struct FrameObserver {
    state: State,
}

impl FrameObserver {
    pub fn new() -> Self {
        FrameObserver {
            state: State::Header {
                buf: [0; FRAME_HEADER_LEN],
                filled: 0,
            },
        }
    }

    /// Feeds bytes received from the server, calling `on_goaway` for every
    /// complete `GOAWAY` frame.
    pub fn feed(&mut self, mut data: &[u8], on_goaway: &mut impl FnMut(GoAway)) {
        while !data.is_empty() {
            match &mut self.state {
                State::Header { buf, filled } => {
                    let n = (FRAME_HEADER_LEN - *filled).min(data.len());
                    buf[*filled..*filled + n].copy_from_slice(&data[..n]);
                    *filled += n;
                    data = &data[n..];
                    if *filled == FRAME_HEADER_LEN {
                        let len = u32::from_be_bytes([0, buf[0], buf[1], buf[2]]) as usize;
                        let frame_type = buf[3];
                        self.state = if frame_type == GOAWAY {
                            State::GoAway {
                                remaining: len,
                                payload: Vec::with_capacity(
                                    len.min(GOAWAY_FIXED_LEN + MAX_DEBUG_DATA),
                                ),
                            }
                        } else {
                            State::Skip { remaining: len }
                        };
                    }
                }
                State::Skip { remaining } => {
                    let n = (*remaining).min(data.len());
                    *remaining -= n;
                    data = &data[n..];
                }
                State::GoAway { remaining, payload } => {
                    let n = (*remaining).min(data.len());
                    let keep = (GOAWAY_FIXED_LEN + MAX_DEBUG_DATA)
                        .saturating_sub(payload.len())
                        .min(n);
                    payload.extend_from_slice(&data[..keep]);
                    *remaining -= n;
                    data = &data[n..];
                    if *remaining == 0 && payload.len() >= GOAWAY_FIXED_LEN {
                        let id =
                            u32::from_be_bytes([payload[0], payload[1], payload[2], payload[3]]);
                        on_goaway(GoAway {
                            last_stream_id: id & 0x7fff_ffff,
                            error_code: u32::from_be_bytes([
                                payload[4], payload[5], payload[6], payload[7],
                            ]),
                            debug_data: payload[GOAWAY_FIXED_LEN..].to_vec(),
                        });
                    }
                }
            }
            let frame_done = match &self.state {
                State::Skip { remaining } | State::GoAway { remaining, .. } => *remaining == 0,
                State::Header { .. } => false,
            };
            if frame_done {
                self.state = State::Header {
                    buf: [0; FRAME_HEADER_LEN],
                    filled: 0,
                };
            }
        }
    }
}

/// Something that happened on a monitored transport.
#[derive(Debug)]
pub(crate) enum Event<'a> {
    /// The server sent a `GOAWAY` frame.
    GoAway(GoAway),
    /// Reading or writing failed; only the first error is reported.
    Error(&'a io::Error),
}

/// Whether `error` is a TLS error, such as an alert from the server.
pub(crate) fn is_tls_error(error: &io::Error) -> bool {
    error
        .get_ref()
        .is_some_and(|inner| inner.is::<rustls::Error>())
}

/// An I/O stream that reports [`Event`]s.
pub(crate) struct MonitoredIo<T, F> {
    inner: T,
    observer: FrameObserver,
    on_event: F,
    failed: bool,
}

impl<T, F> MonitoredIo<T, F>
where
    F: FnMut(Event<'_>),
{
    pub fn new(inner: T, on_event: F) -> Self {
        MonitoredIo {
            inner,
            observer: FrameObserver::new(),
            on_event,
            failed: false,
        }
    }

    fn check<R>(&mut self, result: Poll<io::Result<R>>) -> Poll<io::Result<R>> {
        if let Poll::Ready(Err(error)) = &result {
            if !self.failed {
                self.failed = true;
                (self.on_event)(Event::Error(error));
            }
        }
        result
    }
}

impl<T, F> AsyncRead for MonitoredIo<T, F>
where
    T: AsyncRead + Unpin,
    F: FnMut(Event<'_>) + Unpin,
{
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        let before = buf.filled().len();
        let result = Pin::new(&mut this.inner).poll_read(cx, buf);
        ready!(this.check(result))?;
        let on_event = &mut this.on_event;
        this.observer.feed(&buf.filled()[before..], &mut |goaway| {
            on_event(Event::GoAway(goaway))
        });
        Poll::Ready(Ok(()))
    }
}

impl<T, F> AsyncWrite for MonitoredIo<T, F>
where
    T: AsyncWrite + Unpin,
    F: FnMut(Event<'_>) + Unpin,
{
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        let result = Pin::new(&mut this.inner).poll_write(cx, buf);
        this.check(result)
    }

    fn poll_write_vectored(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bufs: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        let result = Pin::new(&mut this.inner).poll_write_vectored(cx, bufs);
        this.check(result)
    }

    fn is_write_vectored(&self) -> bool {
        self.inner.is_write_vectored()
    }

    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        let result = Pin::new(&mut this.inner).poll_flush(cx);
        this.check(result)
    }

    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        // The connection is ending anyway, errors closing it are not news.
        Pin::new(&mut self.get_mut().inner).poll_shutdown(cx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(frame_type: u8, stream_id: u32, payload: &[u8]) -> Vec<u8> {
        let len = payload.len() as u32;
        let mut out = len.to_be_bytes()[1..].to_vec();
        out.push(frame_type);
        out.push(0);
        out.extend_from_slice(&stream_id.to_be_bytes());
        out.extend_from_slice(payload);
        out
    }

    fn goaway(last_stream_id: u32, error_code: u32, debug: &[u8]) -> Vec<u8> {
        let mut payload = last_stream_id.to_be_bytes().to_vec();
        payload.extend_from_slice(&error_code.to_be_bytes());
        payload.extend_from_slice(debug);
        frame(GOAWAY, 0, &payload)
    }

    fn stream() -> Vec<u8> {
        let mut bytes = frame(0x4, 0, &[0, 3, 0, 0, 3, 0xe8]); // SETTINGS
        bytes.extend(frame(0x1, 1, b"headers")); // HEADERS
        bytes.extend(frame(0x0, 1, &[b'x'; 300])); // DATA
        bytes.extend(frame(0x4, 0, &[])); // SETTINGS ACK, empty payload
        bytes.extend(goaway(5, 0, br#"{"reason":"BadCertificateEnvironment"}"#));
        bytes.extend(frame(0x6, 0, &[0; 8])); // PING
        bytes.extend(goaway(7, 2, b""));
        bytes
    }

    fn observe(chunks: impl IntoIterator<Item = Vec<u8>>) -> Vec<GoAway> {
        let mut observer = FrameObserver::new();
        let mut seen = Vec::new();
        for chunk in chunks {
            observer.feed(&chunk, &mut |goaway| seen.push(goaway));
        }
        seen
    }

    fn expected() -> Vec<GoAway> {
        vec![
            GoAway {
                last_stream_id: 5,
                error_code: 0,
                debug_data: br#"{"reason":"BadCertificateEnvironment"}"#.to_vec(),
            },
            GoAway {
                last_stream_id: 7,
                error_code: 2,
                debug_data: vec![],
            },
        ]
    }

    #[test]
    fn whole_stream() {
        assert_eq!(observe([stream()]), expected());
    }

    #[test]
    fn byte_by_byte() {
        assert_eq!(observe(stream().into_iter().map(|b| vec![b])), expected());
    }

    #[test]
    fn odd_chunks() {
        let bytes = stream();
        assert_eq!(observe(bytes.chunks(7).map(<[u8]>::to_vec)), expected());
    }

    #[test]
    fn long_debug_data_is_truncated() {
        let debug = vec![b'a'; 5000];
        let mut bytes = goaway(1, 0, &debug);
        bytes.extend(goaway(3, 0, b""));
        let seen = observe([bytes]);
        assert_eq!(seen.len(), 2);
        assert_eq!(seen[0].debug_data.len(), MAX_DEBUG_DATA);
        assert_eq!(seen[1].last_stream_id, 3);
    }

    #[test]
    fn outcomes() {
        let outcome = |code, debug: &[u8]| {
            GoAway {
                last_stream_id: 0,
                error_code: code,
                debug_data: debug.to_vec(),
            }
            .outcome()
        };
        assert_eq!(
            outcome(0, br#"{"reason": "BadCertificateEnvironment"}"#),
            "BadCertificateEnvironment"
        );
        assert_eq!(outcome(0, b""), "NO_ERROR");
        assert_eq!(outcome(0xb, b""), "ENHANCE_YOUR_CALM");
        assert_eq!(outcome(0, b"going away"), "going away");
        assert_eq!(outcome(0, &[b'x'; 200]).len(), 100);
    }

    /// Reads and writes fail with a network error.
    struct FailingIo;

    impl AsyncRead for FailingIo {
        fn poll_read(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            _: &mut ReadBuf<'_>,
        ) -> Poll<io::Result<()>> {
            Poll::Ready(Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "Operation timed out",
            )))
        }
    }

    impl AsyncWrite for FailingIo {
        fn poll_write(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            _: &[u8],
        ) -> Poll<io::Result<usize>> {
            Poll::Ready(Err(io::Error::from(io::ErrorKind::BrokenPipe)))
        }

        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }

        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }

    #[tokio::test]
    async fn reports_first_error_only() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let mut errors = Vec::new();
        let mut io = MonitoredIo::new(FailingIo, |event: Event<'_>| match event {
            Event::Error(error) => errors.push(error.to_string()),
            Event::GoAway(goaway) => panic!("unexpected {goaway:?}"),
        });
        assert!(io.read(&mut [0; 8]).await.is_err());
        assert!(io.write(b"x").await.is_err());
        assert!(io.read(&mut [0; 8]).await.is_err());
        drop(io);
        assert_eq!(errors, ["Operation timed out"]);
    }

    #[test]
    fn tls_errors() {
        let alert = rustls::Error::AlertReceived(rustls::AlertDescription::UnknownCA);
        assert!(is_tls_error(&io::Error::new(
            io::ErrorKind::InvalidData,
            alert
        )));
        assert!(!is_tls_error(&io::Error::new(
            io::ErrorKind::TimedOut,
            "Operation timed out"
        )));
        assert!(!is_tls_error(&io::Error::from(
            io::ErrorKind::ConnectionReset
        )));
    }
}
