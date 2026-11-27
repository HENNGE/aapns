# Changelog

## 26.11.0

* Rewritten in Rust, published to crates.io as `aapns`. The API keeps the
  same three layers (`api`, `pool`, `connection`); see the documentation.
* Connections enable TCP keepalive (probes after 60s idle, every 10s, giving
  up after 3) and, on Linux, `TCP_USER_TIMEOUT` (30s), so that connections
  silently dropped by a middlebox are detected and replaced (#45).
* Network errors such as `ETIMEDOUT` or `EHOSTUNREACH` close the connection
  quietly, with the error as the connection's outcome.
* TLS errors after the handshake, such as a client certificate rejected by a
  TLS 1.3 server, are logged as `TLS error: ...` and recorded as the
  connection's outcome.
* Time sensitive notifications now set `interruption-level` in the `aps`
  payload. 23.7 sent it as an HTTP header, which APNs ignores.
* A badge of `0` and an expiration of `0` are now sent rather than dropped.
* Device tokens are validated before use in the request path.
* `GOAWAY` frames from the server are acted upon immediately, including the
  connection pool shutting down on `BadCertificateEnvironment`.
* Requests that time out or are cancelled reset their HTTP/2 stream.
* The connection pool's retry back-off is capped at 10 seconds.
* The command line client exits with a non-zero status on failure, and
  accepts `--port` and `--ca-file`.

## 23.7

* Add support for time sensitive notifications.

## 20.8.1

* Fixed notification size check for different push types.

## 20.8

* Added `voip`, `complication`, `fileprovider` and `mdm` push types.

## 20.7

* Fixed development api host
* Fixed cli

## 20.6

* Incompatible change to how clients are created. Please refer to the documentation.
* Incompatible change to the command line interface to allow targeting simulators.
* Added support to send notifications to simulators.

## 20.4

* Significantly more robust
* Better latency, as connections are created ahead of time
* Better concurrency using HTTP/2 streams
* Implemented connection pool
* Rewritten to use `h2` directly
* Removed httpx and structlog dependencies

## 20.2

* Updated httpx.

## 19.11.1

* Fixed click dependency.

## 19.11

* Removed `aapns.connect`
* Removed all import aliases from `aapns`
* Removed custom HTTP/2 client (replaced with [httpx](https://github.com/encode/httpx))
* Added `aapns.api.create_client` to instantiate a connection to APNS.
* Added full, [mypy](http://www.mypy-lang.org) verified, type hints
* Added [black](https://github.com/psf/black) formatting
* Changed build system from setuptools to poetry
