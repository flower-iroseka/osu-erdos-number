"""Check that the two riskiest assumptions in this project actually hold.

Run this before building anything on top of it. It answers:

  1. Can we reach osu! at all from wherever this is running? GitHub Actions
     runners come from datacenter IPs, and osu! is behind Cloudflare.
  2. Does an OAuth token actually make the `s=ranked` filter work? Anonymous
     requests to the same endpoint silently ignore it and return loved maps too.
  3. Does the collector itself still parse what the endpoint returns? It runs
     for real here, on one page, against the authenticated API.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import sys

import fetch_beatsets
import osu_api

# Which statuses we expect once the filter is applied. `ranked` covers
# approved=2 as well, since osu! merged the two categories.
EXPECTED_STATUSES = {"ranked", "approved"}


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

    remaining = headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        print(f"   rate limit remaining: {remaining}")
    else:
        print("   no X-RateLimit-Remaining header; cannot pace requests from it")

    print("\n3. checking that the ranked filter was actually applied...")
    modes: set[str] = set()
    statuses: set[str] = set()
    for beatmapset in sets:
        for beatmap in beatmapset.get("beatmaps") or []:
            modes.add(beatmap.get("mode"))
            statuses.add(beatmap.get("status"))

    print(f"   statuses seen: {sorted(statuses)}")
    print(f"   modes seen:    {sorted(modes)}")

    unexpected = statuses - EXPECTED_STATUSES
    if unexpected:
        print(
            f"\nFAIL: the filter is not being applied. Got {sorted(unexpected)} "
            "but only ranked/approved were requested.\n"
            "Without a working filter the whole dataset would be wrong, so this "
            "has to be sorted out first."
        )
        return 1
    if not sets:
        print("\nFAIL: no beatmapsets came back; cannot tell whether the filter works.")
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

    print("\nPASS: network reachable and the ranked filter works.")
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
