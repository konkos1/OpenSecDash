from __future__ import annotations

import ipaddress


def is_local_ip_value(value: str | None) -> bool:
    """Return True for addresses/ranges that should be treated as local UI-only.

    We do not rewrite stored country/IP fields. This helper is used by filters
    and presentation so local/private labels remain reversible. It lives in a
    dependency-free core module because the events model derives its
    ``is_local_ip`` column default from it (models must not import services).
    """
    if not value:
        return False
    try:
        network = ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return False
    return (
        network.is_private
        or network.is_loopback
        or network.is_link_local
        or network.is_multicast
        or network.is_reserved
        or network.is_unspecified
    )
