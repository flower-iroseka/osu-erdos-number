"""Turn the collected beatmapsets into the graph the site loads.

Two mappers are linked when they both contributed a difficulty to the same
beatmapset. Every pair inside a set is linked, not only host to guest: two
people who each made a difficulty for a third person's set did work together.

Each edge carries a mask of the game modes both mappers contributed to in that
set. The site uses the whole graph, but the mask is what would allow filtering
per mode later without collecting everything again.

Each edge also carries the ids of the beatmapsets behind it, which the site shows
when you click an arrow in a path. Those ids are only for display; the path
search ignores them.

Mappers who never worked with anyone are still kept as nodes. They have no
edges, but the site has to be able to tell "this mapper has no ranked maps"
apart from "this mapper has ranked maps but has never collaborated".

    python scripts/build_graph.py                    # uses the cached usernames
    python scripts/build_graph.py --fetch-usernames  # look up any that are missing
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import osu_api

MODE_NAMES = ["osu", "taiko", "fruits", "mania"]

# /users?ids[]= accepts 50 at a time.
USER_BATCH = 50

DEFAULT_STORE = Path("data/beatsets.json")
DEFAULT_USER_CACHE = Path("data/users.json")
DEFAULT_OUT = Path("out")


def load_json(path: Path, fallback):
    if not path.is_file():
        return fallback
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def mode_masks(diffs) -> dict[int, int]:
    """Map each mapper in a set to the modes they contributed a difficulty of."""
    per_user: dict[int, int] = defaultdict(int)
    for _beatmap_id, user_id, mode in diffs:
        per_user[user_id] |= 1 << mode
    return per_user


def count_sets_per_mode(sets: dict) -> list[Counter]:
    """For each mode, how many beatmapsets each mapper contributed a difficulty to.

    A set counts once per mapper no matter how many difficulties they made in it.
    """
    counts = [Counter() for _ in MODE_NAMES]

    for _set_id, (_host, diffs) in sets.items():
        for user_id, mask in mode_masks(diffs).items():
            for mode in range(len(MODE_NAMES)):
                if mask >> mode & 1:
                    counts[mode][user_id] += 1

    return counts


def pick_top(counts: list[Counter]) -> list[int | None]:
    """The mapper with the most sets in each mode.

    Ties break on the lowest user id so a rerun picks the same person.
    """
    tops = []
    for counter in counts:
        if not counter:
            tops.append(None)
            continue
        tops.append(min(counter.items(), key=lambda item: (-item[1], item[0]))[0])
    return tops


def build_edges(sets: dict) -> tuple[dict, dict, list[int]]:
    """Link every pair of mappers who share a set.

    Returns (edge masks, the set ids behind each edge, contributors per set),
    all keyed by ordered user id pair.
    """
    edges: dict[tuple[int, int], int] = {}
    edge_sets: dict[tuple[int, int], list[int]] = {}
    contributors: list[int] = []

    for set_id, (_host, diffs) in sets.items():
        per_user = mode_masks(diffs)
        users = sorted(per_user)
        contributors.append(len(users))

        for index, first in enumerate(users):
            for second in users[index + 1:]:
                # Modes both of them worked on here. Zero is normal and means
                # they contributed to different modes of a multi-mode set.
                shared = per_user[first] & per_user[second]
                key = (first, second)
                edges[key] = edges.get(key, 0) | shared
                # Kept so the site can show which maps a pair worked on. Most
                # pairs have exactly one.
                edge_sets.setdefault(key, []).append(int(set_id))

    return edges, edge_sets, contributors


def resolve_usernames(
    user_ids: list[int],
    cache_path: Path,
    token: str | None,
    fetch: bool,
    delay: float = 0.5,
) -> dict[str, str]:
    """user_id (as a string) to username, cached so reruns stay offline."""
    cache = load_json(cache_path, {})
    missing = [uid for uid in user_ids if str(uid) not in cache]
    print(f"usernames: {len(cache)} cached, {len(missing)} to look up")

    if missing and not fetch:
        print("   --fetch-usernames not given, leaving them unnamed")
        return cache

    for start in range(0, len(missing), USER_BATCH):
        batch = missing[start:start + USER_BATCH]
        payload, headers = osu_api.api_get("users", token, params={"ids[]": batch})

        # The endpoint answers with a bare list here, not an object.
        for user in payload or []:
            cache[str(user["id"])] = user.get("username") or ""

        done = min(start + USER_BATCH, len(missing))
        if (start // USER_BATCH) % 10 == 0:
            print(f"   resolved {done}/{len(missing)}", flush=True)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")

        pause = max(delay, osu_api.throttle_delay(headers))
        if pause:
            time.sleep(pause)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    if fetch:
        named = sum(1 for uid in user_ids if cache.get(str(uid)))
        if user_ids and named / len(user_ids) < 0.9:
            print(
                f"   WARNING: only {named}/{len(user_ids)} mappers have a username. "
                "If that is far below what you expect, the /users request is "
                "probably not being built or read correctly.",
                flush=True,
            )

    return cache


def report_diagnostics(
    sets: dict,
    edges: dict,
    edge_sets: dict,
    contributors: list[int],
    masks: Counter,
) -> None:
    """Print the numbers that would reveal the data not meaning what we assume."""
    print("\ndiagnostics")

    repeat = Counter(len(ids) for ids in edge_sets.values())
    print("  beatmapsets per collaborating pair:")
    for count in sorted(repeat)[:6]:
        print(f"    {count}: {repeat[count]} pairs")
    print(f"    most by any pair: {max(repeat) if repeat else 0}")

    histogram = Counter(contributors)
    print("  contributors per beatmapset:")
    for size in sorted(histogram)[:8]:
        print(f"    {size:>3} mappers: {histogram[size]} sets")
    biggest = max(contributors) if contributors else 0
    print(f"    largest: {biggest} mappers in one set")
    if biggest > 10:
        print(
            f"    a set with {biggest} mappers contributes "
            f"{biggest * (biggest - 1) // 2} edges on its own, which can distort "
            "degree-based statistics"
        )

    print("  edge mode masks:")
    for mask, count in sorted(masks.items()):
        names = [MODE_NAMES[m] for m in range(len(MODE_NAMES)) if mask >> m & 1]
        label = "+".join(names) if names else "(different modes, no overlap)"
        print(f"    {label}: {count} edges")

    degree = Counter()
    for first, second in edges:
        degree[first] += 1
        degree[second] += 1
    if degree:
        values = sorted(degree.values())
        print(f"  degree: max {values[-1]}, median {values[len(values) // 2]}, mean {sum(values) / len(values):.1f}")

    # Component sizes tell us how often the site will report a mapper as
    # unreachable rather than showing a number.
    adjacency: dict[int, list[int]] = defaultdict(list)
    for first, second in edges:
        adjacency[first].append(second)
        adjacency[second].append(first)

    for user in set(mode_masks_from_sets(sets)) - set(adjacency):
        adjacency.setdefault(user, [])

    seen: set[int] = set()
    sizes: list[int] = []
    for node in adjacency:
        if node in seen:
            continue
        stack = [node]
        size = 0
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            size += 1
            stack.extend(adjacency[current])
        sizes.append(size)
    sizes.sort(reverse=True)
    isolated = sum(1 for size in sizes if size == 1)
    print(f"  components: {len(sizes)}, largest {sizes[0]} ({100 * sizes[0] / len(adjacency):.0f}%)")
    print(f"  mappers with no collaborators at all: {isolated}")


def mode_masks_from_sets(sets: dict) -> set[int]:
    users: set[int] = set()
    for _set_id, (host, diffs) in sets.items():
        users.add(host)
        for _beatmap_id, user_id, _mode in diffs:
            users.add(user_id)
    return users


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--user-cache", type=Path, default=DEFAULT_USER_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--fetch-usernames", action="store_true")
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    store = load_json(args.store, None)
    if not store or not store.get("sets"):
        print(f"no collected sets in {args.store}; run fetch_beatsets.py first", file=sys.stderr)
        return 1

    sets = store["sets"]
    print(f"loaded {len(sets)} beatmapsets from {args.store}")

    counts = count_sets_per_mode(sets)
    tops = pick_top(counts)
    for mode, user_id in enumerate(tops):
        if user_id is None:
            print(f"  {MODE_NAMES[mode]}: no data")
        else:
            print(f"  {MODE_NAMES[mode]}: user {user_id} with {counts[mode][user_id]} sets")

    edges, edge_sets, contributors = build_edges(sets)
    incidences = sum(len(ids) for ids in edge_sets.values())
    print(f"built {len(edges)} unique collaborations across {incidences} pair-set pairs")

    users = sorted(mode_masks_from_sets(sets))
    cache = resolve_usernames(users, args.user_cache, None if not args.fetch_usernames
                              else osu_api.get_token(), args.fetch_usernames, args.delay)

    degree = Counter()
    for first, second in edges:
        degree[first] += 1
        degree[second] += 1

    # Rank by degree so the busiest mappers get the small indices. It keeps the
    # neighbour lists for popular nodes close together in the adjacency the site
    # builds, and makes tie-breaking more predictable.
    ordered = sorted(users, key=lambda uid: (-degree.get(uid, 0), uid))
    index_of = {uid: index for index, uid in enumerate(ordered)}

    masks = Counter(mask for mask in edges.values())
    report_diagnostics(sets, edges, edge_sets, contributors, masks)

    flat_edges: list[int] = []
    # One entry per edge, aligned with the triples above. A single id is stored
    # bare, several as an array, which keeps the common case small.
    flat_edge_sets: list[int | list[int]] = []
    for (first, second), mask in edges.items():
        flat_edges.extend((index_of[first], index_of[second], mask))
        ids = sorted(edge_sets[(first, second)])
        flat_edge_sets.append(ids[0] if len(ids) == 1 else ids)

    graph = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "modes": MODE_NAMES,
        "top": [index_of[uid] if uid is not None else -1 for uid in tops],
        "users": [[uid, cache.get(str(uid))] for uid in ordered],
        "edges": flat_edges,
        "edge_sets": flat_edge_sets,
    }

    meta = {
        "generated": graph["generated"],
        "modes": MODE_NAMES,
        "top": [
            {
                "mode": MODE_NAMES[mode],
                "index": index_of[uid] if uid is not None else -1,
                "user_id": uid,
                "username": cache.get(str(uid)) if uid is not None else None,
            }
            for mode, uid in enumerate(tops)
        ],
    }

    args.out.mkdir(parents=True, exist_ok=True)
    graph_path = args.out / "graph.json"
    meta_path = args.out / "meta.json"

    payload = json.dumps(graph, separators=(",", ":")).encode()
    graph_path.write_bytes(payload)
    meta_path.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")

    packed = gzip.compress(payload, 9)
    print(f"\nwrote {graph_path}: {len(payload) / 1e6:.2f} MB raw, {len(packed) / 1e6:.2f} MB gzipped")
    print(f"wrote {meta_path}: {meta_path.stat().st_size} bytes")
    print(f"{len(ordered)} mappers, {len(edges)} edges")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except osu_api.CredentialsMissing as error:
        sys.stdout.flush()
        print(f"\n{error}", file=sys.stderr)
        sys.exit(2)
    except osu_api.OsuApiError as error:
        sys.stdout.flush()
        print(f"\nFAIL: {error}", file=sys.stderr)
        sys.exit(1)
