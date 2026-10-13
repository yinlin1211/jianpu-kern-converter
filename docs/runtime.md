# Official SVG Runtime

The repository does not bundle the third-party HC engine or the existing
website runtime. They remain local dependencies because of their upstream
licensing and size.

For the existing MiniUnSongData pipeline, set the runtime directory to:

```text
jp-svt/B_tools/jianpu_dataset/scripts/website_renderer/runtime/
```

It must contain:

```text
render_svg.js
core_codec.js
song_builder_new.js
util_5b7fbbde18.js
hc_12ab21fd54-0419-12.js
```

The local workbench starts with:

```bash
python3 web_server.py \
  --runtime-dir /path/to/website_renderer/runtime \
  --port 8765
```

Then open `http://127.0.0.1:8765/`. The browser sends JSON or notation to the
Python server; the server invokes the official Node runtime and returns the
actual SVG. No score renderer is reimplemented in the browser.

The standalone `render_jianpu.py` wrapper is useful for direct SVG generation.
For a `phrase.json`, the official runtime consumes `payload.songdata`; the
`notes`, `provenance`, and `verification` fields are preserved as metadata but
are not themselves rendered.
