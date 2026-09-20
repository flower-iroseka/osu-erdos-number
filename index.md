---
layout: default
---

<!--
  No blank lines and no indentation in here. kramdown ends a raw HTML block at
  the first blank line, and would then wrap the rest of the markup in <p>.
-->
<div id="app" data-graph-url="{{ site.graph_url }}">
<div class="erdos-head">
<h1 id="erdos-title"><span id="top-name">&hellip;</span> number</h1>
<div class="mode-picker">
<button type="button" id="mode-button" aria-haspopup="listbox" aria-expanded="false" aria-label="Game mode"><span id="mode-icon"></span><span class="caret">&#9662;</span></button>
<ul id="mode-menu" role="listbox" hidden></ul>
</div>
</div>
<div class="query">
<input id="mapper-input" type="text" placeholder="user id or username" autocomplete="off" spellcheck="false" aria-label="user id or username">
<button type="button" id="go" aria-label="Go">&#9654;</button>
</div>
<p id="result"></p>
<div id="paths"></div>
<p id="status"></p>
</div>
