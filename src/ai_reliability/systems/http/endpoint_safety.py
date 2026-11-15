"""Endpoint safety policy: guards against accidental SSRF via configured URLs.

By default only public ``https``/``http`` endpoints are allowed. Private,
loopback, link-local (including cloud metadata 169.254.169.254) and reserved
addresses must be explicitly opted into per system (e.g. a local InferenceLab
server), and should never be opted into for an exposed multi-tenant deployment.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from pydantic import Field

from ai_reliability.domain.base import DomainModel
from ai_reliability.errors import UnsafeEndpoint

_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data", "ip6-localhost"}
)

Resolver = Callable[[str], Sequence[str]]


def _system_resolver(host: str) -> Sequence[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


class EndpointPolicy(DomainModel):
    """Which endpoints a system adapter may contact."""

    allowed_schemes: tuple[str, ...] = ("https", "http")
    allow_private_network: bool = False
    allowed_hosts: tuple[str, ...] = Field(default=())
    resolve_dns: bool = False


# Each dot-separated part is decimal, octal or 0x-hex, as accepted by inet_aton.
_NUMERIC_HOST = re.compile(r"(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+)){0,3}")


def _literal_ip(host: str) -> str | None:
    """Canonical IP if ``host`` is any literal IP form the OS resolver would accept.

    Covers dotted quads, IPv6, and legacy inet_aton forms such as ``127.1`` or the
    decimal integer ``2130706433`` that naive ``ip_address`` checks miss.
    """
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if _NUMERIC_HOST.fullmatch(host):
        try:
            return str(ipaddress.IPv4Address(socket.inet_aton(host)))
        except OSError:
            return None
    return None


def _is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None and _is_internal(mapped):
            return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_endpoint(
    url: str, policy: EndpointPolicy, *, resolver: Resolver = _system_resolver
) -> str:
    """Return ``url`` if allowed by ``policy``; raise :class:`UnsafeEndpoint` otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in policy.allowed_schemes:
        raise UnsafeEndpoint(f"scheme {parts.scheme!r} not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeEndpoint("endpoint has no host")
    if parts.username or parts.password:
        raise UnsafeEndpoint("credentials embedded in URL are not allowed")
    if policy.allowed_hosts:
        if host not in policy.allowed_hosts:
            raise UnsafeEndpoint(f"host {host!r} not in allowed_hosts")
        return url
    if policy.allow_private_network:
        return url
    if host in _BLOCKED_HOSTNAMES or host.endswith((".localhost", ".internal", ".local")):
        raise UnsafeEndpoint(f"host {host!r} is internal; set allow_private_network to opt in")
    candidates: list[str] = []
    literal = _literal_ip(host)
    if literal is not None:
        candidates.append(literal)
    elif _NUMERIC_HOST.fullmatch(host):
        # Looks numeric but is not a canonical address: refuse rather than let the
        # resolver interpret it (e.g. "0x7f.1" or "017700000001").
        raise UnsafeEndpoint(f"ambiguous numeric host {host!r}")
    elif policy.resolve_dns:
        try:
            candidates.extend(resolver(host))
        except OSError as exc:
            raise UnsafeEndpoint(f"cannot resolve {host!r}: {exc}") from exc
    for candidate in candidates:
        ip = ipaddress.ip_address(candidate.split("%", 1)[0])
        if _is_internal(ip):
            raise UnsafeEndpoint(
                f"host {host!r} resolves to non-public address; set allow_private_network to opt in"
            )
    return url
