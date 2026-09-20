/* Load the graph, then answer one question per query.
 *
 * The graph is a few MB, small enough to hand to the browser and search there,
 * which is why there is no backend. See the plan for why precomputing every
 * mapper's answer in the workflow instead would be worse.
 */
(function () {
  "use strict";

  // Three is what the page is allowed to show, so there is never a reason to
  // count past four.
  var CAP = 4;
  var MAX_PATHS = 3;
  var MAX_SETS = 3;

  // Drawn here rather than shipped as images, so the page has no icon
  // dependency. currentColor lets CSS pick the colour.
  var ICONS = {
    osu:
      '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/>' +
      '<circle cx="12" cy="12" r="3.5" fill="currentColor"/>',
    taiko:
      '<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/>' +
      '<rect x="3.5" y="10.4" width="17" height="3.2" rx="1.6" fill="currentColor"/>',
    fruits:
      '<circle cx="12" cy="7.5" r="4" fill="currentColor"/>' +
      '<path d="M3 14h18a9 9 0 0 1-18 0z" fill="currentColor"/>',
    mania:
      '<rect x="2.5" y="9" width="4" height="12" rx="1" fill="currentColor"/>' +
      '<rect x="7.75" y="4" width="4" height="17" rx="1" fill="currentColor"/>' +
      '<rect x="13" y="7" width="4" height="14" rx="1" fill="currentColor"/>' +
      '<rect x="18.25" y="11" width="4" height="10" rx="1" fill="currentColor"/>'
  };

  var els = {
    app: document.getElementById("app"),
    topName: document.getElementById("top-name"),
    modeButton: document.getElementById("mode-button"),
    modeIcon: document.getElementById("mode-icon"),
    modeMenu: document.getElementById("mode-menu"),
    input: document.getElementById("mapper-input"),
    go: document.getElementById("go"),
    note: document.getElementById("top-note"),
    result: document.getElementById("result"),
    paths: document.getElementById("paths"),
    status: document.getElementById("status")
  };

  // The two explain buttons and the boxes they open, as [button, box] id pairs.
  // Both boxes ship hidden in the markup; the buttons are the only way to them.
  var EXPLAIN = [
    ["explain-erdos", "explain-erdos-text"],
    ["explain-why", "explain-why-text"]
  ];

  var graph = {
    modes: [],
    mode: 0,
    top: [],
    names: [],
    ids: null,
    byId: new Map(),
    byName: new Map(),
    offsets: null,
    neighbors: null,
    edgeOf: null,
    setIds: null,
    nodeCount: 0
  };

  function fetchJson(url) {
    return fetch(url).then(function (response) {
      if (!response.ok) {
        throw new Error(response.status + " " + response.statusText + " for " + url);
      }
      return response.json();
    });
  }

  /* Copy what we need out of the parsed JSON and let the rest be collected.
   * The raw arrays are several times larger than the typed ones below. */
  function build(data) {
    var users = data.users;
    var count = users.length;
    var names = new Array(count);
    var ids = new Uint32Array(count);

    for (var i = 0; i < count; i++) {
      var uid = users[i][0];
      var name = users[i][1];
      ids[i] = uid;
      names[i] = name || "user " + uid;
      graph.byId.set(uid, i);
      if (name) {
        var key = name.toLowerCase();
        var found = graph.byName.get(key);
        if (found) {
          found.push(i);
        } else {
          graph.byName.set(key, [i]);
        }
      }
    }

    graph.names = names;
    graph.ids = ids;
    graph.top = data.top;
    graph.modes = data.modes;
    graph.nodeCount = count;

    var edges = new Uint32Array(data.edges);
    var degree = new Uint32Array(count + 1);
    for (var e = 0; e < edges.length; e += 3) {
      degree[edges[e]]++;
      degree[edges[e + 1]]++;
    }

    // CSR: offsets into a flat neighbour list, ordered by node. Cheaper to walk
    // than a list of arrays, and the graph is read far more often than built.
    var offsets = new Uint32Array(count + 1);
    for (var v = 0; v < count; v++) {
      offsets[v + 1] = offsets[v] + degree[v];
    }

    var cursor = offsets.slice(0, count);
    var neighbors = new Uint32Array(offsets[count]);
    var edgeOf = new Uint32Array(offsets[count]);
    for (var index = 0, at = 0; at < edges.length; at += 3, index++) {
      var a = edges[at];
      var b = edges[at + 1];
      neighbors[cursor[a]] = b;
      edgeOf[cursor[a]++] = index;
      neighbors[cursor[b]] = a;
      edgeOf[cursor[b]++] = index;
    }

    graph.offsets = offsets;
    graph.neighbors = neighbors;
    graph.edgeOf = edgeOf;
    // Ragged on purpose: a bare id when a pair shares one set, an array when
    // they share more. Only used for display.
    graph.setIds = data.edge_sets || null;
  }

  function bfs(source) {
    var count = graph.nodeCount;
    var dist = new Int32Array(count).fill(-1);
    var queue = new Int32Array(count);
    var head = 0;
    var tail = 0;

    dist[source] = 0;
    queue[tail++] = source;

    while (head < tail) {
      var u = queue[head++];
      var next = dist[u] + 1;
      for (var e = graph.offsets[u]; e < graph.offsets[u + 1]; e++) {
        var v = graph.neighbors[e];
        if (dist[v] < 0) {
          dist[v] = next;
          queue[tail++] = v;
        }
      }
    }

    return dist;
  }

  function edgeIndex(a, b) {
    for (var e = graph.offsets[a]; e < graph.offsets[a + 1]; e++) {
      if (graph.neighbors[e] === b) {
        return graph.edgeOf[e];
      }
    }
    return -1;
  }

  /* The shortest path that reuses the fewest nodes already used by an earlier
   * one.
   *
   * Every shortest path between the same two nodes has the same number of
   * internal nodes, so "reuse the fewest" is just "collect the most weight",
   * with weight 1 on an unused node and 0 on a used one. That makes it a
   * longest-path DP over the DAG the distances induce, which is exact -- no
   * enumerating paths and guessing. */
  function bestPath(source, target, dist, buckets, used) {
    var count = graph.nodeCount;
    var best = new Int32Array(count);
    var next = new Int32Array(count).fill(-1);

    function weight(v) {
      // The two endpoints are in every path by definition, so they never count
      // as reused.
      return v === source || v === target || used.has(v) ? 0 : 1;
    }

    for (var d = 1; d < buckets.length; d++) {
      var bucket = buckets[d];
      for (var i = 0; i < bucket.length; i++) {
        var u = bucket[i];
        var top = -1;
        var choice = -1;
        for (var e = graph.offsets[u]; e < graph.offsets[u + 1]; e++) {
          var v = graph.neighbors[e];
          if (dist[v] !== d - 1) {
            continue;
          }
          var score = weight(v) + best[v];
          if (score > top || (score === top && v < choice)) {
            top = score;
            choice = v;
          }
        }
        best[u] = top;
        next[u] = choice;
      }
    }

    var path = [source];
    var current = source;
    while (current !== target) {
      current = next[current];
      if (current < 0) {
        return null;
      }
      path.push(current);
    }
    return path;
  }

  function shortPaths(source, target, dist) {
    var count = graph.nodeCount;
    var maxDist = dist[source];
    var buckets = [];
    var d;

    for (d = 0; d <= maxDist; d++) {
      buckets.push([]);
    }
    // Only up to dist[source]. The BFS reaches nodes farther from the target
    // than the source is, and those cannot sit on a shortest source-to-target
    // path, so they are left out rather than bucketed.
    for (var v = 0; v < count; v++) {
      if (dist[v] >= 0 && dist[v] <= maxDist) {
        buckets[dist[v]].push(v);
      }
    }

    // How many shortest paths run from each node down to the target. Capped,
    // because the page only ever needs to know whether there are more than
    // three -- without the cap this overflows on hubs.
    var total = new Uint8Array(count);
    total[target] = 1;
    for (d = 1; d <= maxDist; d++) {
      var bucket = buckets[d];
      for (var i = 0; i < bucket.length; i++) {
        var u = bucket[i];
        var sum = 0;
        for (var e = graph.offsets[u]; e < graph.offsets[u + 1]; e++) {
          var w = graph.neighbors[e];
          if (dist[w] === d - 1) {
            sum += total[w];
            if (sum >= CAP) {
              break;
            }
          }
        }
        total[u] = sum >= CAP ? CAP : sum;
      }
    }

    var want = Math.min(total[source], MAX_PATHS);
    var used = new Set();
    var paths = [];
    for (var k = 0; k < want; k++) {
      var path = bestPath(source, target, dist, buckets, used);
      if (!path) {
        break;
      }
      paths.push(path);
      for (var j = 1; j < path.length - 1; j++) {
        used.add(path[j]);
      }
    }

    return { paths: paths, more: total[source] > MAX_PATHS };
  }

  function arrow(a, b) {
    var wrap = document.createElement("span");
    wrap.className = "arrow-wrap";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "arrow";
    button.textContent = "→";
    wrap.appendChild(button);

    var index = edgeIndex(a, b);
    var stored = index >= 0 && graph.setIds ? graph.setIds[index] : null;
    if (stored === null || stored === undefined) {
      // A graph built before the set ids existed. The chain still works.
      return wrap;
    }

    var ids = (Array.isArray(stored) ? stored : [stored]).slice(0, MAX_SETS);
    var box = document.createElement("span");
    box.className = "arrow-maps";
    ids.forEach(function (id, i) {
      if (i > 0) {
        box.appendChild(document.createTextNode(" "));
      }
      var link = document.createElement("a");
      link.href = "https://osu.ppy.sh/beatmapsets/" + id;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "#" + id;
      box.appendChild(link);
    });
    wrap.appendChild(box);

    button.setAttribute("aria-expanded", "false");
    button.addEventListener("click", function () {
      var open = wrap.classList.toggle("open");
      button.setAttribute("aria-expanded", String(open));
      var row = wrap.closest(".path-row");
      if (row) {
        fitRow(row);
      }
    });

    return wrap;
  }

  /* The map list is positioned under its arrow so that opening it does not move
   * the arrow out from under the pointer. That means it would also land on top
   * of the next path, so the row has to be given room for whichever list is
   * open, and the room taken back when nothing is. */
  function fitRow(row) {
    var open = row.querySelectorAll(".arrow-wrap.open .arrow-maps");
    var tallest = 0;
    for (var i = 0; i < open.length; i++) {
      tallest = Math.max(tallest, open[i].offsetHeight);
    }
    row.style.paddingBottom = tallest ? tallest + 8 + "px" : "";
  }

  function pathRow(path) {
    var row = document.createElement("div");
    row.className = "path-row";

    path.forEach(function (node, i) {
      if (i > 0) {
        row.appendChild(arrow(path[i - 1], node));
      }
      var name = document.createElement("span");
      name.className = "path-name";
      name.textContent = graph.names[node];
      row.appendChild(name);
    });

    return row;
  }

  function resolve(text) {
    if (/^\d+$/.test(text)) {
      var index = graph.byId.get(Number(text));
      return index === undefined ? null : index;
    }
    var found = graph.byName.get(text.toLowerCase());
    return found ? found[0] : null;
  }

  /* Run a query and then show the answer.
   *
   * The header fills the first screen, so whatever run() writes lands below the
   * fold. Without the scroll it looks like the button did nothing. */
  function run(raw) {
    answer(raw);
    els.app.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function answer(raw) {
    var text = raw.trim();
    els.result.textContent = "";
    els.paths.textContent = "";
    els.status.textContent = "";

    var target = graph.top[graph.mode];
    if (target < 0) {
      els.result.textContent = "No data for this mode yet.";
      return;
    }
    var topName = graph.names[target];

    var source = text ? resolve(text) : null;
    if (source === null) {
      // Everything in the graph got there by having a ranked beatmap, so a name
      // we cannot find is a mapper who has none.
      els.result.textContent = "Please enter a mapper with ranked beatmaps.";
      return;
    }

    // A username that maps to more than one node means somebody renamed and
    // both names are in the data. Take the first and say so.
    if (!/^\d+$/.test(text)) {
      var matches = graph.byName.get(text.toLowerCase());
      if (matches && matches.length > 1) {
        els.status.textContent =
          "That name matches " + matches.length + " mappers; using the first one.";
      }
    }

    var dist = bfs(target);
    var steps = dist[source];
    if (steps < 0) {
      els.result.textContent =
        "This mapper hasn't established an erdős with " + topName + ".";
      return;
    }

    els.result.textContent =
      graph.names[source] + "'s " + topName + " number is: " + steps;

    var found = shortPaths(source, target, dist);
    found.paths.forEach(function (path) {
      els.paths.appendChild(pathRow(path));
    });
    if (found.more) {
      var more = document.createElement("p");
      more.className = "more";
      more.textContent = "... and more!";
      els.paths.appendChild(more);
    }
  }

  function iconSvg(mode) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[mode] || "";
    return svg;
  }

  /* The mark next to a mode's name, in a box of a fixed size so the picker does
   * not change width when the selection changes. "all" is not a mode, so it has
   * no glyph of its own and is spelled out instead. */
  function modeMark(mode) {
    var box = document.createElement("span");
    box.className = "mode-mark";
    if (mode === "all") {
      var text = document.createElement("span");
      text.className = "mode-text";
      text.textContent = "ALL";
      box.appendChild(text);
    } else {
      box.appendChild(iconSvg(mode));
    }
    return box;
  }

  function closeMenu() {
    els.modeMenu.hidden = true;
    els.modeButton.setAttribute("aria-expanded", "false");
  }

  /* How many ranked/approved beatmapsets the top mapper contributed a
   * difficulty to, as the creator or as a guest. The count comes from meta.json;
   * without it there is nothing to show, so the line stays empty.
   *
   * The number is per mode, so the sentence names the mode when it is not the
   * every-mode leaderboard. */
  function updateNote() {
    var index = graph.top[graph.mode];
    var entry = graph.metaTop ? graph.metaTop[graph.mode] : null;
    if (index < 0 || !entry || typeof entry.sets !== "number") {
      els.note.textContent = "";
      return;
    }
    // "participated in" rather than "hosted": the count includes sets the top
    // mapper was only a guest on, and the subtitle above says the same thing.
    // The scope goes at the end, so it reads the same either way round.
    var mode = graph.modes[graph.mode];
    els.note.textContent =
      graph.names[index] +
      " has participated in a total number of " +
      entry.sets +
      " ranked/approved mapsets in " +
      (mode === "all" ? "all modes" : mode + " mode") +
      ".";
  }

  function selectMode(index) {
    graph.mode = index;
    els.topName.textContent = graph.names[graph.top[index]] || "…";
    els.modeIcon.replaceChildren(modeMark(graph.modes[index]));
    updateNote();
    Array.prototype.forEach.call(els.modeMenu.children, function (item, i) {
      item.firstChild.setAttribute("aria-selected", String(i === index));
    });
    closeMenu();
    // Each mode has its own top mapper, so the last query has a new answer.
    if (els.input.value.trim()) {
      run(els.input.value);
    }
  }

  function buildModePicker() {
    els.modeIcon.appendChild(modeMark(graph.modes[0]));

    graph.modes.forEach(function (mode, i) {
      var item = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button";
      button.setAttribute("role", "option");
      button.appendChild(modeMark(mode));
      var label = document.createElement("span");
      label.textContent = mode;
      button.appendChild(label);
      button.addEventListener("click", function () {
        selectMode(i);
      });
      item.appendChild(button);
      els.modeMenu.appendChild(item);
    });
  }

  function submit() {
    var text = els.input.value.trim();
    if (!text) {
      return;
    }
    var url = new URL(window.location.href);
    url.searchParams.set("s", text);
    history.replaceState(null, "", url);
    run(text);
  }

  function wire() {
    els.go.addEventListener("click", submit);
    els.input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        submit();
      }
    });

    els.modeButton.addEventListener("click", function (event) {
      event.stopPropagation();
      if (els.modeMenu.hidden) {
        els.modeMenu.hidden = false;
        els.modeButton.setAttribute("aria-expanded", "true");
      } else {
        closeMenu();
      }
    });
    document.addEventListener("click", closeMenu);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeMenu();
      }
    });

    // Both explain boxes open and close on their own. They are independent on
    // purpose: the two sit in separate columns, so having both open at once is
    // readable and there is no reason to force one shut.
    EXPLAIN.forEach(function (pair) {
      var button = document.getElementById(pair[0]);
      var box = document.getElementById(pair[1]);
      if (!button || !box) {
        return;
      }
      button.addEventListener("click", function () {
        var opening = box.hidden;
        box.hidden = !opening;
        button.setAttribute("aria-expanded", String(opening));
      });
    });
  }

  function start(data) {
    build(data);
    buildModePicker();
    wire();
    // Mode 0 is "all" -- every mode counted together -- which is what the page
    // opens on. build_graph.py puts it first in the modes list for this reason.
    selectMode(0);
    els.status.textContent = "";

    var wanted = new URLSearchParams(window.location.search).get("s");
    if (wanted) {
      els.input.value = wanted;
      run(wanted);
    }
  }

  function fail(error) {
    els.result.textContent = "Could not load the graph.";
    els.status.textContent = String(error && error.message ? error.message : error);
  }

  var graphUrl = els.app.dataset.graphUrl;
  els.status.textContent = "loading the graph…";

  // Both start together: meta.json is tiny and carries the top mapper's name, so
  // the heading can be right before the graph has finished arriving.
  var pending = fetchJson(graphUrl);
  fetchJson(graphUrl.replace(/[^/]*$/, "meta.json"))
    .then(function (meta) {
      graph.top = meta.top.map(function (entry) {
        return entry.index;
      });
      graph.modes = meta.modes;
      graph.metaTop = meta.top;
      els.topName.textContent = meta.top[0].username || "…";
      updateNote();
    })
    .catch(function () {
      // Not fatal: the graph carries the same top indices and names. Only the
      // mapset count next to the heading is missing, so that line stays blank.
    });

  pending.then(start, fail);
})();
