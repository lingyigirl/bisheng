"""Outbound URL validation for third-party managed agents (F078).

This is deliberately **not** a tightening of the web-link importer's predicate
(``knowledge/domain/services/web_link_import_service.py``): that path has its
own intranet-import policy and tests, and changing it would be a regression.
F078 gets a parameterized helper with a stricter policy:

- only ``http`` / ``https``; no embedded userinfo; no query/fragment;
- resolve **all** A/AAAA answers and reject the URL if any answer is not global
  (private, loopback, link-local, multicast, unspecified, reserved, CGNAT,
  IPv4-mapped private);
- optional exact-host / host:port allowlist; an empty allowlist means "nothing
  is allowed" so an operator must opt in;
- DNS rebinding between this check and httpx's own connect-time resolution is a
  documented residual risk (design 决策 7). A bank deployment should add an
  egress firewall/allowlist; IP pinning requires a custom transport and is
  Phase 2.

The resolver is injectable so tests never touch the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

Resolver = Callable[[str, int], Iterable[str]]


class OutboundUrlRejected(ValueError):
    """Raised when a URL fails scheme/userinfo/resolution/allowlist checks."""


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _normalize_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(raw)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _ip_is_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # ``is_global`` already excludes RFC1918, loopback, link-local, multicast,
    # unspecified, reserved, CGNAT (100.64/10) and documentation ranges; keep
    # the explicit predicates as a readable safety net across Python versions.
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_reserved
    )


def _parse_host_port(entry: str) -> tuple[str, int | None]:
    """Parse an allowlist entry as ``host`` or ``host:port`` (IPv6 in brackets)."""
    parsed = urlsplit(f"//{entry}")
    if not parsed.hostname:
        raise OutboundUrlRejected(f"invalid allowlist entry: {entry!r}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise OutboundUrlRejected(f"invalid allowlist port: {entry!r}") from exc
    return parsed.hostname.lower(), port


def _ensure_allowed(host: str, port: int, allowlist: Iterable[str]) -> None:
    entries = list(allowlist)
    if not entries:
        raise OutboundUrlRejected("outbound allowlist is empty")
    host_l = host.lower()
    for entry in entries:
        allowed_host, allowed_port = _parse_host_port(entry)
        if allowed_host == host_l and (allowed_port is None or allowed_port == port):
            return
    raise OutboundUrlRejected(f"host {host_l}:{port} is not in the outbound allowlist")


def canonicalize_endpoint(url: str) -> str:
    """Canonical form used for uniqueness hashing (not for the actual request)."""
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise OutboundUrlRejected("only http/https endpoints are supported")
    if parsed.username or parsed.password:
        raise OutboundUrlRejected("endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise OutboundUrlRejected("endpoint must not carry query string or fragment")
    host = (parsed.hostname or "").lower()
    if not host:
        raise OutboundUrlRejected("endpoint host is empty")
    port = parsed.port
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    netloc = host
    if port is not None and port != default_port:
        netloc = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def endpoint_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_endpoint(url).encode("utf-8")).hexdigest()


def native_endpoint_hash(native_app_type: int, native_app_id: str) -> str:
    """Non-null sentinel for native projections (avoids DM8 NULL-unique semantics)."""
    payload = f"native:{native_app_type}:{native_app_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_outbound_url(
    url: str,
    *,
    allowlist: Iterable[str],
    allow_private_networks: bool = False,
    resolver: Resolver | None = None,
) -> str:
    """Validate one endpoint and return its canonical form.

    Raises ``OutboundUrlRejected`` with a short reason; callers map that to
    12111 without echoing the URL (it may contain internal topology).
    """
    canonical = canonicalize_endpoint(url)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    _ensure_allowed(host, port, allowlist)

    if allow_private_networks:
        return canonical

    resolve = resolver or _default_resolver
    try:
        addresses = list(resolve(host, port))
    except OSError as exc:
        raise OutboundUrlRejected("endpoint host could not be resolved") from exc
    if not addresses:
        raise OutboundUrlRejected("endpoint host resolved to no addresses")
    for raw in addresses:
        try:
            ip = _normalize_ip(raw)
        except ValueError as exc:
            raise OutboundUrlRejected("endpoint resolved to an invalid address") from exc
        if not _ip_is_allowed(ip):
            raise OutboundUrlRejected("endpoint resolves to a non-public address")
    return canonical


async def avalidate_outbound_url(
    url: str,
    *,
    allowlist: Iterable[str],
    allow_private_networks: bool = False,
    resolver: Resolver | None = None,
) -> str:
    """Async wrapper: DNS resolution runs in a worker thread."""
    return await asyncio.to_thread(
        validate_outbound_url,
        url,
        allowlist=allowlist,
        allow_private_networks=allow_private_networks,
        resolver=resolver,
    )
