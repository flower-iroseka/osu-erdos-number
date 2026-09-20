# osu! erdős

**https://flower-iroseka.github.io/osu-erdos-number/**

Type in a user id or username and the page tells you how many handshakes separate
that mapper from the mapper who has taken part in the most ranked beatmaps.

Inspired by [this post](https://x.com/_StanMa/status/2100676388475007390) from
[@_StanMa](https://x.com/_StanMa).

## What the number means

Pick a mode. Whoever appears on the most ranked or approved beatmapsets in that
mode is the top mapper for it — counted per beatmapset, not per difficulty, and
counting both the sets they hosted and the ones they were only a guest on.

The picker opens on **all**, which counts a beatmapset once however many modes it
covers. The four real modes each count only the sets that mode is played in.

Everyone who worked on the same beatmapset is connected. A mapper's number is the
number of those connections you have to walk to reach the top mapper. The top
mapper is 0; somebody who mapped with them is 1; and so on. If no chain exists, the
page says so.

The mode only changes who the top mapper is and the mapset count beside the
heading. The chain itself is searched over the whole collaboration graph, so two
people who worked on the same beatmapset are connected even if they mapped
different modes of it.

The page shows up to three shortest paths. It prefers paths that do not repeat a
mapper in the middle, so three paths usually mean three genuinely different routes
rather than the same people in a different order.

Click the arrows between two mappers on a path to see which beatmapsets connect
them.

## How it works

Static GitHub Pages site, no server. A scheduled GitHub Action collects the data
and force-pushes it to the `data` branch as a single commit; the page fetches
`graph.json` from there and does the graph search in the browser.

```
scripts/
  osu_api.py         OAuth token + retrying, rate-limited GET
  fetch_beatsets.py  walks /beatmapsets/search and accumulates a local store
  build_graph.py     counts sets per mapper, builds the edge list, exports
  smoke_test.py      checks the two assumptions this all rests on
```

Every beatmapset with status `ranked` or `approved` is included. `loved`,
`qualified`, `pending` and `graveyard` are not. Deleting or unranking a set cannot
be noticed by an incremental pass, so a full re-collection runs twice a year.

The Action publishes to the `data` branch:

| File | What it is |
| --- | --- |
| `graph.json` | what the page loads — mappers, edges, and the beatmapsets behind each edge |
| `meta.json` | when it was generated and the top mapper per mode |
| `beatsets.json` | the accumulated beatmapset store, so the next run can go incremental |
| `users.json` | user id to username cache |
| `users.csv` | per-mapper hosted and guest-difficulty beatmapset counts, every mode together |
| `users-<mode>.csv` | the same table restricted to one mode |

Both kinds of table are four columns and sorted by `total`: how many ranked or
approved beatmapsets the mapper hosted, how many they were only a guest on, and
the sum.

## Running it yourself

Python 3, standard library only, no dependencies.

```sh
export OSU_CLIENT_ID=...
export OSU_CLIENT_SECRET=...
python scripts/smoke_test.py    # reachability, the ranked filter, /users
python scripts/fetch_beatsets.py --full
python scripts/build_graph.py
```

`fetch_beatsets.py` without `--full` stops as soon as it reaches a beatmapset it
has already stored, which is what the monthly run does. `build_graph.py` writes to
`out/` unless `--out` says otherwise.

A full collection is about 1100 requests and 14 minutes.

## Credits

Data from the [osu! API v2](https://osu.ppy.sh/docs/index.html). Site built on the
Cayman theme via `jekyll-remote-theme`.
