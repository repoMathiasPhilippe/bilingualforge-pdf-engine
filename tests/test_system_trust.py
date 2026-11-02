"""``--system-trust``: the layout model's first download behind a TLS proxy.

The weights of the layout model download on the first ``layout_load``. A
TLS-intercepting proxy presents its own certificate, which Python's bundle
refuses and the system store, where the proxy installs its root, accepts.
On 2026-11-02 the weights had vanished from the cache, and the engine's
download failed on ``CERTIFICATE_VERIFY_FAILED`` while the client, which
trusts the system store, would have succeeded.
"""

from __future__ import annotations

import ssl

import pytest

from pdf_engine.__main__ import trust_the_system_store

truststore = pytest.importorskip("truststore")


def test_the_flag_verifies_tls_against_the_system_store() -> None:
    try:
        assert trust_the_system_store()
        assert ssl.SSLContext is truststore.SSLContext
    finally:
        truststore.extract_from_ssl()
    assert ssl.SSLContext is not truststore.SSLContext
