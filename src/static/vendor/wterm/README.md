# Vendored wterm

Pinned to `@wterm/dom` and `@wterm/core` 0.5.7. Build dependencies and package integrity hashes are recorded in `package-lock.json`.

Rebuild from this directory:

```sh
npm ci --ignore-scripts
npm run build
```

`build.mjs` applies checked source transformations during bundling: calculate the scroll extent from the rendered DOM, do not reset scroll position solely because the core has no scrollback, and expose host input-scroll and post-render callbacks. These preserve host-added iOS and keyboard spacing and let single-pane keyboard input follow the cursor instead of jumping to the terminal bottom. A changed upstream implementation fails the build rather than silently dropping the patch.

Fernando owns grid fitting using rendered row metrics, including its existing iOS spacer. For a focused single pane, the keyboard reduces the terminal element's height and grid size without adding scrollable content. Built-in automatic resizing is disabled so the renderer does not introduce a competing visual-viewport resize policy. Hidden panes use the public rendering-pause API.

The upstream Apache-2.0 license is included in `LICENSE`.
