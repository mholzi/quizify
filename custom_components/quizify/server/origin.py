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
    Compares the browser-supplied ``Origin`` against the host the request came
    in on plus the URLs Home Assistant knows itself by. A request with **no**
    ``Origin`` keeps passing: non-browser clients (the standalone dev server,
    the tests, a script) never send one, and an attacker cannot make a browser
    omit it.

``check_unauthenticated_post``
    The same gate plus a mandatory ``Content-Type: application/json``, which
    forces a cross-site caller into a preflight that HA will not answer.
"""

from __future__ import annotations

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
    instance is not connected to the cloud.
    """
    try:
        from homeassistant.components.cloud import (  # noqa: PLC0415
            async_remote_ui_url,
        )

        return _normalize(async_remote_ui_url(hass))
    except Exception:  # noqa: BLE001 — no cloud, not logged in, older HA
        return None


def allowed_origin_hosts(request: Any, runtime: Any) -> set[str]:
    """Every ``host[:port]`` a legitimate Quizify page can be served from."""
    hosts: set[str] = set()
    own = _normalize(getattr(request, "host", None))
    if own:
        hosts.add(own)

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


def is_origin_allowed(request: Any, runtime: Any) -> bool:
    """Whether *request* may be served, judged by its ``Origin`` header.

    ``True`` when no ``Origin`` was sent (non-browser client) or when its host
    matches one of :func:`allowed_origin_hosts`. ``False`` — the cross-site
    case — for anything else, ``Origin: null`` (a sandboxed iframe or a
    ``file://`` page) included.
    """
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
        "Refusing cross-origin %s from %s (Origin=%r, allowed=%s)",
        what,
        getattr(request, "remote", None),
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
