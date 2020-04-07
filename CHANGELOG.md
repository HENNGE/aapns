# Changelog

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
