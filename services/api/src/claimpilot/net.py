"""Outbound TLS configuration.

Corporate proxies (e.g. Zscaler) re-sign traffic to some hosts with their own root CA, which
browsers trust through the OS certificate store but Python's bundled CA list does not. On
Windows and macOS we therefore verify against the OS trust store (``truststore``); verification
stays ON. Elsewhere (Linux containers, hosting) the default CA bundle is used unchanged.
"""

from __future__ import annotations

import ssl
import sys


def ssl_context() -> ssl.SSLContext | bool:
    """A verifying SSL context for ``httpx`` (``True`` means httpx's default CA bundle)."""
    if sys.platform in ("win32", "darwin"):
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return True
