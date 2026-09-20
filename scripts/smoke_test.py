"""Check that the two riskiest assumptions in this project actually hold.

Run this before building anything on top of it. It answers:

  1. Can we reach osu! at all from wherever this is running? GitHub Actions
     runners come from datacenter IPs, and osu! is behind Cloudflare.
  2. Does an OAuth token actually make the `s=ranked` filter work? Anonymous
     requests to the same endpoint silently ignore it and return loved maps too.
     Checked both ways round: s=ranked must come back with ranked (and approved)
     and nothing else, and s=loved must come back with loved, which is what
     stops a filter that ignores the parameter from passing.
  3. Does the collector itself still parse what the endpoint returns? It runs
     for real here, on one page, against the authenticated API.
  4. Does /users?ids[]= hand back usernames for a batch of ids? The graph
     builder needs this, and it would otherwise only be found out late in a
     full collection.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys
import time
from collections import Counter

import build_graph
import fetch_beatsets
import osu_api

# Which statuses we expect once the filter is applied. `ranked` covers
# approved=2 as well, since osu! merged the two categories.
EXPECTED_STATUSES = {"ranked", "approved"}

# Enough pages that an approved map, which is a small share of the whole, has a
# fair chance of turning up. One page would prove almost nothing.
STATUS_PAGES = 12


def statuses_across(token: str, kind: str, pages: int) -> Counter:
    """Every difficulty status seen over a few pages of one search."""
    seen: Counter = Counter()
    cursor = None

    for _ in range(pages):
        params = {"s": kind}
        if cursor:
            params["cursor_string"] = cursor
        payload, headers = osu_api.api_get("beatmapsets/search", token, params=params)

        for beatmapset in payload.get("beatmapsets") or []:
            for beatmap in beatmapset.get("beatmaps") or []:
                seen[beatmap.get("status")] += 1

        cursor = payload.get("cursor_string")
        if not cursor:
            break
        time.sleep(max(0.4, osu_api.throttle_delay(headers)))

    return seen


def main() -> int:
    print("1. requesting a client-credentials token...")
    token = osu_api.get_token()
    # Deliberately no part of the token goes to stdout: this runs in GitHub
    # Actions, where the log is readable by anyone who can see the repo.
    print("   ok, got a token")

    print("\n2. fetching one page of ranked beatmapsets...")
    payload, headers = osu_api.api_get(
        "beatmapsets/search", token, params={"s": "ranked", "m": 0}
    )

    total = payload.get("total")
    sets = payload.get("beatmapsets") or []
    print(f"   ok, total ranked sets in osu!standard: {total}")
    print(f"   sets returned in this page: {len(sets)}")

    remaining = headers.get(osu_api.RATE_REMAINING_HEADER)
    if remaining is not None:
        print(f"   rate limit remaining: {remaining}")
    else:
        print("   no rate-limit header in the response; the collector falls back")
        print("   to a fixed delay between pages instead of pacing off it")

    print("\n3. checking the status filter, in both directions...")
    modes: set[str] = set()
    for beatmapset in sets:
        for beatmap in beatmapset.get("beatmaps") or []:
            modes.add(beatmap.get("mode"))
    print(f"   modes seen in s=ranked: {sorted(modes)}")

    ranked = statuses_across(token, "ranked", STATUS_PAGES)
    loved = statuses_across(token, "loved", 2)
    qualified = statuses_across(token, "qualified", 2)

    print(f"   s=ranked    over {STATUS_PAGES} pages: {dict(ranked)}")
    print(f"   s=loved     over 2 pages:  {dict(loved)}")
    print(f"   s=qualified over 2 pages:  {dict(qualified)}")

    if not ranked:
        print("\nFAIL: s=ranked came back with no difficulties at all.")
        return 1

    extra = set(ranked) - EXPECTED_STATUSES
    if extra:
        print(
            f"\nFAIL: s=ranked returned {sorted(extra)}, but only ranked/approved "
            "were asked for.\nWithout a working filter the whole dataset would be "
            "wrong, so this has to be sorted out first."
        )
        return 1
    if ranked.get("ranked", 0) == 0:
        print("\nFAIL: s=ranked returned no ranked difficulties at all.")
        return 1
    if ranked.get("approved", 0) == 0:
        print(
            f"   note: no approved maps in {STATUS_PAGES} pages. They are rare, so "
            "this is expected, but it means approved is only covered by the "
            "status check above, not seen directly."
        )
    else:
        print(f"   approved difficulties seen: {ranked['approved']}")

    # The other two searches have to come back with their own status, or the
    # check above proves nothing: a filter that ignored the parameter entirely
    # would pass it just as well.
    if not loved or not set(loved) <= {"loved"}:
        print(
            f"\nFAIL: s=loved returned {dict(loved)}, so the status parameter is "
            "not being honoured. That means s=ranked is not filtering either."
        )
        return 1
    if qualified and not set(qualified) <= {"qualified"}:
        print(f"\nFAIL: s=qualified returned {dict(qualified)}.")
        return 1

    # Run the collector itself rather than inspecting the response by hand. This
    # is the same code path the real run uses, so a field the endpoint stopped
    # returning shows up here instead of an hour into a collection.
    print("\n4. running the collector for one page...")
    try:
        collected, stats = fetch_beatsets.collect(token, known=set(), full=True, limit=1)
    except (KeyError, TypeError) as error:
        print(
            f"\nFAIL: the collector could not read the response: {error!r}\n"
            "It expects fields the endpoint is not returning, so it needs "
            "updating before a full run is worth starting."
        )
        return 1

    diff_count = sum(len(diffs) for _, diffs in collected.values())
    guest_count = sum(
        1 for host, diffs in collected.values() for _, user_id, _ in diffs if user_id != host
    )
    print(
        f"   collected {len(collected)} sets and {diff_count} difficulties "
        f"in {stats['pages']} page(s)"
    )
    print(f"   {guest_count} of those difficulties are by someone other than the set host")

    print("\n5. sample of what the collector stores:")
    for set_id, (host, diffs) in list(collected.items())[:3]:
        print(f"\n   set {set_id}  host user {host}")
        for beatmap_id, user_id, mode_int in diffs:
            marker = " " if user_id == host else "*"
            mode = fetch_beatsets.MODE_NAMES[mode_int]
            print(f"     {marker} {mode:<7} user {user_id:<10} beatmap {beatmap_id}")
    print("\n   (* marks a difficulty whose mapper is not the host, i.e. a guest mapper)")

    # build_graph.py resolves names in batches of 50 near the end of its run. If
    # the endpoint answers with something other than what we expect, that is a
    # bad place to find out, so it gets checked here instead.
    print("\n6. resolving a batch of usernames...")
    wanted = sorted({user_id for _host, diffs in collected.values() for _bid, user_id, _m in diffs})
    batch = wanted[:50]
    payload, _headers = osu_api.api_get("users", token, params={"ids[]": batch})

    # Say what came back before parsing it, so a shape we did not expect is
    # readable in the log rather than inferred from a TypeError.
    if isinstance(payload, dict):
        print(f"   payload is an object with keys: {sorted(payload)}")
    else:
        print(f"   payload is a {type(payload).__name__}")

    # Parsed with the same function the graph builder uses, so this checks the
    # real code path rather than a second copy of it that could drift.
    try:
        users = build_graph.parse_users(payload)
    except osu_api.OsuApiError as error:
        print(f"\nFAIL: {error}")
        return 1

    named = {str(user.get("id")): user.get("username") for user in users}
    missing = [uid for uid in batch if str(uid) not in named]
    blank = [uid for uid in batch if str(uid) in named and not named[str(uid)]]
    print(f"   asked for {len(batch)}, got {len(named)} back")

    if missing or blank:
        print(
            f"\nFAIL: {len(missing)} of {len(batch)} came back without a username and "
            f"{len(blank)} came back with an empty one.\nbuild_graph.py would end up "
            "with unnamed mappers all over the graph."
        )
        return 1

    for uid in batch[:5]:
        print(f"   {uid} -> {named[str(uid)]}")

    print("\nPASS: network reachable, the ranked filter works, and names resolve.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except osu_api.CredentialsMissing as error:
        sys.stdout.flush()  # keep the error below the progress lines in CI logs
        print(f"\n{error}", file=sys.stderr)
        sys.exit(2)
    except osu_api.OsuApiError as error:
        sys.stdout.flush()
        print(f"\nFAIL: {error}", file=sys.stderr)
        sys.exit(1)
