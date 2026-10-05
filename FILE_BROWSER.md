# Native Files sessions

Create a Files session from New Session. Multiple sessions, duplicate views in both panes, and independent browser tabs are supported. Each view starts in the running user's home directory and can navigate throughout `/`, subject to the Fernando process user's filesystem permissions. No container or separate file-management server is involved.

The single top bar contains reload, home, an editable breadcrumb path, and a hamburger menu. The menu is a right-side flyout: it overlays content on coarse-pointer/mobile devices and pushes the content on desktop. It contains New items (upload/new folder/new file), current-folder search, hidden items, and a view switch labeled Tile mode in list view or List mode in tile view. Pressed controls have square corners without the inset edge marker. List view adds only the table heading; tile view has no heading. There is no bottom status/action bar. Errors and operation feedback appear as temporary or dismissible overlays without consuming layout space.

The desktop flyout stays open until its own toggle is used, including during path editing, search submission, navigation, and Escape. Its left border is `1px solid #143151`. Column headings have 4px vertical cell padding around 22px-minimum-height buttons, so hover backgrounds do not touch the header edges.

Tap/click a path segment to navigate. Breadcrumb links have `min-height: 22px`. Click blank space in the path field to edit; a stationary hold of at least 500 ms switches to editing on release, retaining the user gesture for mobile keyboard focus. F2, or Enter when the path field itself is focused, also starts editing. Enter submits, Escape/blur cancels, and typed `~`/`~/...` expands to home. Movement/cancellation does not activate long-press editing. Search has an explicit SVG clear button for iOS, and resets when navigating home, navigating directories, or opening files.

## File interactions and defaults

- Fernando Settings → General includes file-browser default List/Tiles and Show hidden files by default. They apply to newly opened/reloaded file-browser views (`file_browser_view`, `file_browser_hidden`).
- File actions are exclusively in a classic context menu: right-click on desktop or stationary 500 ms long press on mobile. Includes Open, Rename, Copy file name(s), Copy/Move to, Download, Delete, Select items, Select all, and Clear selection.
- Desktop letters cycle through visible filenames starting with that letter. Ctrl/Command toggles individual selection; Shift selects a range; Ctrl/Command-A selects visible items. Dragging blank space draws a selection rectangle with edge autoscroll.
- Select items is a true toggle on both desktop and mobile, changing to Exit selection mode while enabled. The same menu command hides the checkboxes again; ordinary modifier-key multi-selection does not force them visible. The command is also available from the background context menu. Mobile supports tapping multiple entries and an additional Done/count control in the top bar. UI text selection is disabled; path/rename/text-editor inputs retain editing and selection.
- In list and tile views, selecting an item and slowly clicking/tapping it again enters inline rename. Rapid double-click still opens. Enter or blur commits; Escape cancels. Desktop F2 and context Rename also work.
- Desktop drag/drop moves selected files/folders into a folder or another Files view, with Ctrl/Option to copy. Drag payloads are opaque, short-lived capabilities referring to origin-local records, never an API key or trusted arbitrary external path list. Copies/moves refuse existing destinations and folder-into-itself recursion. OS files can be dropped to upload into the current folder or a folder item; external folder upload is explicitly rejected. Mobile retains long-press context selection rather than competing native file-drag gestures.
- Dropping Files entries into an ACP chat stages their existing filesystem paths in the standard attachment UI. It does not upload, move, or automatically send them. Pending attachment chips, user-message attachment chips, and assistant-generated attachment chips can be dragged into another chat. Duplicate paths are not staged twice, and the usual remove control works. Reference chips dropped back into Files are copied, never implicitly moved; destination filenames come from actual source path basenames, not display labels.
- The current directory refreshes every five seconds while visible and outside editing, renaming, held gestures, drag selection, dialogs, or the file context menu.

Fernando's sidebar uses the view's current path: `~` for home, `~/...` underneath it, and an absolute path elsewhere. Long labels retain the final 50 Unicode characters, preceded by an ellipsis. Hovering the sidebar item shows the full absolute path. Duplicate views keep independent locations; the active matching view determines the label, otherwise the tab's last reported location is used. The path-derived label replaces manual session naming in the sidebar.

## Views and editing

- Images use the existing `image-viewer.js` dialog with pan/zoom.
- STL uses the existing `stl_viewer.html` and Three.js renderer.
- PDF uses the browser's inline PDF support, with an explicit Open in browser link for viewers that work better in a separate tab.
- Other regular files open in self-hosted Ace 1.44.0 with syntax highlighting, find/replace, and Save/Ctrl-S/Command-S. Text must be UTF-8 and at most 8 MiB; binary input is rejected rather than silently corrupted.
- Save checks the original content hash before writing, preserves existing file mode, and reports a conflict if an agent or another view changed the file.
- Create file/folder, rename, move, copy, delete, upload, and download operate with OS permissions. Copy/move/upload/create do not replace existing destinations. Delete is permanent and confirmed. Cross-filesystem moves report the OS error rather than silently converting to copy/delete.
- Uploads use the existing nginx request-size limit (50 MiB).

## Integration

`file-session.js` is the adapter and application-specific creation wiring. `file-browser.js` is the application, communicating through the shared pane SDK. Session identities/names are stored in `data/file_browser_sessions.json`; directory locations are per-view, carried by URL serialization and tab-local sessionStorage keyed by pane and session. Opening the same session in both panes does not synchronize their directories.

`file-drag.js` is the shared drag protocol for Files and chat. It creates, validates, consumes, and expires one-use origin-local drag records. A reference drag is distinguished from a filesystem drag, and no authentication secret is put on the drag clipboard.

The generic host contract accepts `context(key, state)` and a `navigate` result containing `{location}`; the host persists that location through normal URL synchronization. No file-specific host branches are required.

`src/routes/file_browser.py` registers an authenticated blueprint. All routes require the existing API key, and mutations additionally require the key in the custom header. Full filesystem access is intentional and owner-authorized, isolated to the new endpoints; existing attachment/viewer allowlists remain unchanged. File content is served as an attachment unless explicitly previewing an image/PDF. Active image content is sandboxed, MIME sniffing is disabled, and responses are no-store/no-referrer. OS errors are returned explicitly. Non-regular file streams are rejected to avoid blocking on devices/pipes.

## Checks

`./venv/bin/python -m unittest discover -s tests -p test_file_browser.py`

`node --test tests/test_pane_contract.js tests/test_pane_native_focus.js`

`node --test tests/test_file_browser_ui.js` verifies view defaults, flyout state, context actions, copy names, letter cycling, modifier/marquee/mobile selection, inline rename in both views, authenticated internal drag/drop, explicit search clearing, and path editing/long-press behavior.

`node --test tests/test_chat_file_drops.js` verifies chat references, existing chip rendering/removal, chat-to-chat drags, deduplication, and expired/unknown capability rejection without file uploads or automatic message sends.

Semgrep's upload path-traversal finding was reviewed: directory selection is intentionally unrestricted for the authenticated owner, and the filename rejects separators, dot/dot-dot, and NUL. Exclusive file creation prevents overwriting existing files or following a destination symlink. The explicit traversal rejection is covered by the API tests.
