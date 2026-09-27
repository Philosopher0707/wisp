"""Shared network-destination validation for provider endpoints.

wisp/tools/web.py owns the general SSRF check for `web_fetch` (blocks
private + loopback + link-local + reserved + multicast + unspecified — a
fetched URL should only ever be a genuine public site). Provider endpoints
are a narrower case: `ollama_url` legitimately names `localhost` or a LAN
host, so only the classes that are wrong in EVERY case (cloud metadata,
reserved, unspecified, multicast) are rejected here, unconditionally.
`api_base` (the cloud-provider direction: openai/nvidia/openrouter) reuses
the existing https-or-loopback policy, as a free function so both
`wisp.providers.factory.ProviderFactory` and `wisp.provider_catalog`'s
model-listing path enforce the identical check instead of one of them
skipping it.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse


def assert_ollama_host_safe(url: str) -> None:
    """Unconditionally reject a cloud-metadata/link-local/reserved ollama_url.

    Loopback and ordinary private/LAN addresses are left alone — they are
    the normal case for a local or LAN-hosted Ollama daemon. Only the
    classes with no legitimate use as an Ollama endpoint are blocked, and
    this runs regardless of WISP_PRODUCTION_MODE (that flag layers a
    STRICTER, opt-in allowlist on top; it is not what makes this baseline
    safe).

    A hostname that fails to resolve is not this function's concern — the
    caller's own request will fail with its own clear connection error.
    """
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return
    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            resolved.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not resolved:
        return
    # Block only when EVERY resolved address is unsafe — a dual-stack host
    # with one usable record must not be refused over the other.
    if all(
        ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast
        for ip in resolved
    ):
        raise ValueError(
            f"Ollama URL {url!r} resolves only to a link-local/reserved/"
            f"metadata address ({host}) — refusing to connect."
        )


def validate_api_base(base_url: str, name: str) -> str:
    """Fail-closed endpoint check for key-bearing providers (openai/nvidia/openrouter).

    Empty passes through (provider falls back to its own default base).
    Non-empty must be http(s) with a hostname; plaintext http is
    loopback-only unless WISP_ALLOW_INSECURE_BASE=true — a remote http
    listener otherwise receives the Bearer key in cleartext.
    """
    if not base_url:
        return ""
    parsed = urllib.parse.urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not hostname:
        raise ValueError(f"Invalid api_base for provider {name!r}: {base_url!r}")
    if parsed.scheme == "http" and hostname not in ("localhost", "127.0.0.1", "::1"):
        if os.environ.get("WISP_ALLOW_INSECURE_BASE", "").strip().lower() not in (
            "1", "true", "on", "yes",
        ):
            raise ValueError(
                f"Refusing plaintext http api_base for provider {name!r}: "
                f"{base_url!r} (use https, loopback, or WISP_ALLOW_INSECURE_BASE=true)"
            )
    return base_url
