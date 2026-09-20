"""Thin client for the osu! API v2.

Only what this project needs: a client-credentials token and a JSON request
helper that backs off when osu! starts dropping our connections.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_BASE = "https://osu.ppy.sh/api/v2"

# osu! asks clients to identify themselves so they can get in touch if something
# goes wrong. Bump this if the fetch script ever gets its own repo URL.
USER_AGENT = "osu-erdos-number/0.1"

# The API allows 1200 requests/minute, but different endpoints cost different
# amounts of that budget. Slowing down before we hit the limit is cheaper than
# recovering from a 429.
RATE_FLOOR = 200


class OsuApiError(RuntimeError):
    pass


class CredentialsMissing(OsuApiError):
    pass


class RateLimited(OsuApiError):
    """Raised when we run out of retries because osu! kept throttling us."""


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CredentialsMissing(
            f"{name} is not set.\n"
            "Create an OAuth application at https://osu.ppy.sh/home/account/edit#oauth "
            "(any account can do this, no supporter needed), then export both "
            "OSU_CLIENT_ID and OSU_CLIENT_SECRET."
        )
    return value


def get_token(client_id: str | None = None, client_secret: str | None = None, timeout: int = 30) -> str:
    """Fetch a client-credentials token.

    This is not tied to any user, but osu! still treats the request as
    authenticated, which is what makes the search filters work. See the plan for
    why anonymous requests silently ignore `s` and `m`.
    """
    body = urllib.parse.urlencode(
        {
            "client_id": client_id or _credential("OSU_CLIENT_ID"),
            "client_secret": client_secret or _credential("OSU_CLIENT_SECRET"),
            "grant_type": "client_credentials",
            "scope": "public",
        }
    ).encode()

    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise OsuApiError(f"token request failed: HTTP {error.code} {detail}") from error

    token = payload.get("access_token")
    if not token:
        raise OsuApiError(f"token response contained no access_token: {payload}")

    return token


def api_get(
    path: str,
    token: str,
    params: dict | None = None,
    retries: int = 5,
    timeout: int = 45,
    sleep=time.sleep,
) -> tuple[dict, dict]:
    """GET a JSON endpoint and return (payload, response headers).

    Retries connection-level failures as well as 429/5xx. Both happen in
    practice: osu! sits behind Cloudflare, which tends to reset the connection
    outright rather than answering with a status code.
    """
    url = path if path.startswith("http") else f"{API_BASE}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }

    last_error: Exception | None = None

    for attempt in range(retries):
        if attempt:
            # 1s, 2s, 4s, 8s - long enough to outlast a short throttle window.
            sleep(2 ** (attempt - 1))

        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response), dict(response.headers)

        except urllib.error.HTTPError as error:
            last_error = error
            if error.code != 429 and error.code < 500:
                detail = error.read().decode("utf-8", "replace")[:400]
                raise OsuApiError(f"HTTP {error.code} for {url}: {detail}") from error

        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as error:
            # ConnectionResetError covers http.client.RemoteDisconnected, which
            # is what a Cloudflare reset looks like from here.
            last_error = error

    raise RateLimited(f"gave up on {url} after {retries} attempts: {last_error!r}")


def throttle_delay(remaining: str | None, floor: int = RATE_FLOOR) -> float:
    """Seconds to pause for, based on the rate-limit header osu! sends back.

    Returns 0 when we still have plenty of budget.
    """
    if remaining is None:
        return 0.0
    try:
        left = int(remaining)
    except ValueError:
        return 0.0

    if left >= floor:
        return 0.0
    # Spread whatever is left over the rest of the minute instead of burning it
    # all at once and then stalling.
    return 60.0 / max(left, 1)
