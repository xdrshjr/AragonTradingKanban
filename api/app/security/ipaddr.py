"""Client-address helpers: the three different "shapes" of one IP (spec §24.3.7).

* **stored** — what lands in the database / audit / session list. Truncated for
  privacy: IPv4 `/24`, IPv6 `/48`.
* **rate key** — what a limiter bucket is keyed on. IPv4 **exact**, IPv6 `/64`
  (RV-13: a `/24` would put a whole office NAT behind one login budget, while a `/48`
  hands an attacker 2^16 `/64`s to rotate through).
* **bind bucket** — what a captcha is bound to. Same truncation as *stored*, so a
  phone changing cell towers inside its `/24` keeps its challenge.

Anything that does not parse as an IP (the Starlette test client reports the host
`testclient`) is passed through unchanged: a limiter must still be able to key on it.
"""

from __future__ import annotations

import ipaddress


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None


def _network(value: str, v4_prefix: int, v6_prefix: int) -> str:
    parsed = _parse(value)
    if parsed is None:
        return value
    prefix = v4_prefix if parsed.version == 4 else v6_prefix
    network = ipaddress.ip_network(f"{parsed}/{prefix}", strict=False)
    return str(network)


def stored_prefix(ip: str) -> str:
    """IPv4 → `a.b.c.0/24`, IPv6 → `…/48` (≤ 48 chars, fits `VARCHAR(48)`)."""
    return _network(ip, 24, 48)[:48]


def bind_bucket(ip: str) -> str:
    """The captcha-binding bucket: identical to the stored truncation."""
    return stored_prefix(ip)


def rate_key(ip: str) -> str:
    """IPv4 exact address; IPv6 collapsed to its `/64`."""
    parsed = _parse(ip)
    if parsed is None:
        return ip
    if parsed.version == 4:
        return str(parsed)
    return _network(ip, 32, 64)


def is_ip(value: str) -> bool:
    return _parse(value) is not None
