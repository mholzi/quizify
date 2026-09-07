"""Same-origin gate for the unauthenticated Quizify endpoints (#785).

Quizify's WebSocket and its three unauthenticated POST views are registered on
``hass.http.app.router`` with **no** Home Assistant auth middleware, because the
players are phones that scanned a QR code and have no HA login. That is the
intended trade-off — but it left one hole the browser does not close for us:

* Browsers do **not** apply CORS to a WebSocket handshake. Any page open in the
  victim's browser could open ``ws://homeassistant.local:8123/api/quizify/ws``
  from the victim's own address, which sails past every per-IP cap, and during
  the admin-bootstrap window be handed the admin role and the session token.
* ``request.json()`` does not enforce a Content-Type, so a cross-site form/fetch
  could POST to flag-question / pack-submit as a CORS *simple* request — no
  preflight, no consent.

Both holes are shut by the two helpers here:

``is_origin_allowed``
    Compares the browser-supplied ``Origin`` against the URLs Home Assistant
    knows itself by, plus the host the request came in on **when that host is
    itself a name only the local network can hand out**. A request with **no**
    ``Origin`` keeps passing: non-browser clients (the standalone dev server,
    the tests, a script) never send one, and an attacker cannot make a browser
    omit it.

    That last qualifier is #872. The first cut seeded the allow-list from
    ``request.host`` unconditionally, which a DNS-rebinding page walks straight
    through: a site on ``evil.example`` with a TTL-0 record re-pointed at the
    Home Assistant LAN address opens
    ``ws://evil.example:8123/api/quizify/ws?role=admin`` from the victim's
    phone, and the handshake then carries ``Host: evil.example:8123`` and
    ``Origin: http://evil.example:8123`` — equal, so the gate passed. Safari
    and Firefox have no Private Network Access block, so on the phones this
    game is played on nothing else was in the way. The same allow-list is now
    applied to ``Host`` itself, so a rebound name is refused whether or not the
    browser sends an ``Origin``.

``check_unauthenticated_post``
    The same gate plus a mandatory ``Content-Type: application/json``, which
    forces a cross-site caller into a preflight that HA will not answer.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

#: The one body type the three unauthenticated POST views accept. Anything
#: else is refused, so a browser cannot reach them as a CORS simple request.
JSON_CONTENT_TYPE = "application/json"

#: Ports that carry no information in an origin comparison — a browser omits
#: them from both ``Origin`` and ``Host``, so a configured URL that spells one
#: out must still compare equal.
_DEFAULT_PORTS = {"http": "80", "https": "443"}

#: Name suffixes that the public DNS root cannot delegate, so a page served
#: from one of them was resolved by the local network (mDNS, the router's own
#: resolver, a hosts file) rather than by an attacker's authoritative server.
#: ``.local`` is mDNS (RFC 6762), ``.home.arpa`` is the reserved
#: home-network zone (RFC 8375), ``.internal`` was set aside by ICANN in 2024
#: for private use, and ``.lan`` / ``.home`` / ``.intranet`` are the names
#: consumer routers have handed out for years and that ICANN has refused to
#: delegate. The list is deliberately short and literal: every entry is a
#: suffix a home network really uses, and nothing is inferred.
_PRIVATE_SUFFIXES = (
    ".local",
    ".localhost",
    ".home.arpa",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
)


def _bare_host(netloc: str) -> str:
    """Return *netloc* without its port and without IPv6 brackets."""
    if netloc.startswith("["):
        # ``[fe80::1]:8123`` — everything up to the closing bracket.
        return netloc.partition("]")[0][1:]
    if netloc.count(":") > 1:
        # An unbracketed IPv6 literal; there is no port to strip.
        return netloc
    head, sep, _port = netloc.rpartition(":")
    return head if sep else netloc


def is_private_network_host(netloc: str) -> bool:
    """Whether *netloc* is a name the public internet cannot own (#872).

    The three shapes a Quizify phone actually uses on the LAN, and nothing
    else:

    * an **IP literal** — ``192.168.0.69:8123``, ``[fe80::1]:8123``. A browser
      only sends this as an ``Origin`` when the page was loaded from that
      address, and an address is not something DNS can re-point.
    * a **single-label host** — ``localhost:8123``, ``homeassistant:8123``.
      A name with no dot cannot be registered in the public DNS, so a page
      served from one came from this network's own resolver.
    * a **reserved private suffix** — ``homeassistant.local:8123``. See
      :data:`_PRIVATE_SUFFIXES`.

    A name under a real public TLD (``quiz.example.com``, ``ha.fritz.box``) is
    deliberately **not** in here: from the server's side it is
    indistinguishable from the rebinding page, and the way to allow it is to
    name it in ``internal_url`` / ``external_url``.
    """
    host = _bare_host(netloc).rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    if "." not in host:
        # ``localhost`` and every other single-label LAN name.
        return True
    return host.endswith(_PRIVATE_SUFFIXES)


def _normalize(value: str | None) -> str | None:
    """Return the lower-cased ``host[:port]`` of *value*, default port dropped.

    Accepts a full URL (``https://ha.example.com:8123/lovelace``), a bare
    authority (``ha.example.com:8123``) or ``None``.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parts = urlsplit(candidate if "//" in candidate else f"//{candidate}")
    netloc = parts.netloc.lower()
    if not netloc:
        return None
    # Strip credentials should a configured URL carry any.
    netloc = netloc.rpartition("@")[2]
    scheme = parts.scheme.lower()
    default_port = _DEFAULT_PORTS.get(scheme)
    if default_port and netloc.endswith(f":{default_port}"):
        netloc = netloc[: -len(default_port) - 1]
    return netloc or None


def _hass_of(runtime: Any) -> Any | None:
    """Return the Home Assistant instance behind *runtime*, if there is one.

    ``StandaloneRuntime`` (dev server, tests) has no ``hass`` — the gate then
    falls back to ``request.host`` alone, which is exactly right there.
    """
    return getattr(runtime, "hass", None)


def _nabu_casa_host(hass: Any) -> str | None:
    """The Nabu Casa remote UI host, or ``None`` when cloud is not in use.

    Imported lazily and defensively: ``homeassistant.components.cloud`` is not
    present on every install, and ``async_remote_ui_url`` raises when the
    instance is not connected to the cloud. ``cloud`` is declared in
    ``manifest.json`` as an **after_dependency** (not a dependency) for exactly
    that reason — Quizify works fine without it, it only wants the remote UI
    host to be knowable when it does exist. hassfest requires the declaration.
    """
    try:
        from homeassistant.components.cloud import (  # noqa: PLC0415
            async_remote_ui_url,
        )

        return _normalize(async_remote_ui_url(hass))
    except Exception:  # noqa: BLE001 — no cloud, not logged in, older HA
        return None


def configured_origin_hosts(runtime: Any) -> set[str]:
    """The ``host[:port]`` values Home Assistant is configured to answer to.

    ``internal_url``, ``external_url`` and the Nabu Casa remote UI host — the
    three places a legitimate Quizify page can be served from under a name the
    public DNS *does* own. Nothing here is derived from the request, which is
    the whole point of #872.
    """
    hosts: set[str] = set()
    hass = _hass_of(runtime)
    config = getattr(hass, "config", None)
    for attr in ("internal_url", "external_url"):
        configured = _normalize(getattr(config, attr, None))
        if configured:
            hosts.add(configured)

    if hass is not None:
        remote = _nabu_casa_host(hass)
        if remote:
            hosts.add(remote)

    return hosts


def allowed_origin_hosts(request: Any, runtime: Any) -> set[str]:
    """Every ``host[:port]`` a legitimate Quizify page can be served from.

    The configured hosts, plus the host this very request arrived on — but the
    latter **only when it is a private-network name** (#872). That keeps the
    two properties the gate needs at once:

    * the phone that loaded the page from ``http://192.168.0.69:8123`` is
      allowed without anyone having configured a URL, and a *second* service on
      the same box (``http://192.168.0.69:9000``) still is not, because the
      comparison keeps the port;
    * a rebound public name never enters the set at all, so ``Host`` matching
      ``Origin`` proves nothing on its own any more.
    """
    hosts = configured_origin_hosts(runtime)
    own = _normalize(getattr(request, "host", None))
    if own and is_private_network_host(own):
        hosts.add(own)
    return hosts


def is_host_allowed(request: Any, runtime: Any) -> bool:
    """Whether the request's own ``Host`` is one Quizify answers to (#872).

    A ``Host`` that is neither a private-network name nor a configured URL is
    a rebound name: the browser resolved it through the attacker's DNS and it
    only reaches us because it now points at the LAN address. Refused before
    the ``Origin`` is even looked at, so the attack is blocked whether or not
    an ``Origin`` rides along.

    A missing or unparseable ``Host`` is *not* judged — HTTP/1.1 requires the
    header and aiohttp synthesises it from the socket, so its absence means a
    test double or an internal call, never a browser.
    """
    own = _normalize(getattr(request, "host", None))
    if own is None:
        return True
    if is_private_network_host(own):
        return True
    return own in configured_origin_hosts(runtime)


def is_origin_allowed(request: Any, runtime: Any) -> bool:
    """Whether *request* may be served, judged by ``Host`` and ``Origin``.

    ``True`` when the request's own ``Host`` is one we answer to *and* either
    no ``Origin`` was sent (non-browser client) or its host matches one of
    :func:`allowed_origin_hosts`. ``False`` — the cross-site case — for
    anything else, ``Origin: null`` (a sandboxed iframe or a ``file://`` page)
    included.
    """
    if not is_host_allowed(request, runtime):
        return False

    headers = getattr(request, "headers", None) or {}
    origin = headers.get("Origin")
    if origin is None or not origin.strip():
        return True

    candidate = _normalize(origin)
    if candidate is None:
        return False
    return candidate in allowed_origin_hosts(request, runtime)


def reject_cross_origin(request: Any, runtime: Any, what: str) -> bool:
    """Log-and-report helper: ``True`` when *request* must be refused."""
    if is_origin_allowed(request, runtime):
        return False
    _LOGGER.warning(
        "Refusing cross-origin %s from %s (Host=%r, Origin=%r, allowed=%s). "
        "If this is how you legitimately reach Home Assistant, add it to "
        "internal_url / external_url in the Home Assistant network settings.",
        what,
        getattr(request, "remote", None),
        getattr(request, "host", None),
        (getattr(request, "headers", None) or {}).get("Origin"),
        sorted(allowed_origin_hosts(request, runtime)),
    )
    return True


def check_unauthenticated_post(request: Any, runtime: Any) -> web.Response | None:
    """Gate an unauthenticated POST view; ``None`` means "carry on" (#785).

    Refuses a cross-site ``Origin`` with 403 and a body that is not declared
    ``application/json`` with 415. The Content-Type requirement is the CSRF
    half: it is not on the CORS simple-request list, so a cross-site page has
    to send a preflight, and Home Assistant answers no preflight for these
    routes.
    """
    if reject_cross_origin(request, runtime, "POST"):
        return web.json_response({"error": "forbidden_origin"}, status=403)

    headers = getattr(request, "headers", None) or {}
    content_type = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != JSON_CONTENT_TYPE:
        return web.json_response(
            {"error": "unsupported_media_type", "expected": JSON_CONTENT_TYPE},
            status=415,
        )
    return None
