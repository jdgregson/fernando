```space-style
html[data-theme="dark"] {
    --ui-accent-color: #3465a3;
    --root-background-color: #0d2848;
    --top-background-color: #0d2848;
    --panel-background-color: #0f1e38;
    --editor-background-color: #0d2848;
    --modal-background-color: #0f1e38;
    --modal-help-background-color: #25364b;
    --modal-hint-background-color: #153d6c;
    --root-color: #d4d4d4;
    --editor-heading-color: #5a9fd4;
    --editor-link-color: #3465a3;
    --editor-meta-color: #6a7a8a;
    --editor-directive-color: #6a7a8a;
    --editor-code-background-color: #0a1e3a;
    --editor-code-color: #d4d4d4;
    --editor-code-border-color: #143151;
    --editor-highlight-background-color: #143151;
    --editor-widget-background-color: rgb(17 45 80);
    --ui-border-color: #143151;
    --panel-border-color: rgb(20 49 81);
    --rhs-border-color: rgb(20 49 81);
    --modal-border-color: rgb(20 49 81);
    --top-border-color: rgb(20 49 81);
}

html {
    --ui-accent-color: #3465a3;
}
```

```space-lua
-- Override Ctrl-C to copy rendered text instead of raw markdown
-- Puts BOTH text/html (for Word) and text/plain (original markdown) on clipboard
command.define {
  name = "Edit: Copy Rendered",
  key = "Ctrl-c",
  mac = "Cmd-c",
  run = function()
    local selection = editor.getSelection()
    if selection.text == "" then
      return
    end
    local mdTree = markdown.parseMarkdown(selection.text)
    mdTree = markdown.expandMarkdown(mdTree)
    local html = markdown.markdownToHtml(markdown.renderParseTree(mdTree))
    local plaintext = selection.text  -- Original markdown source
    
    -- Escape for embedding in JavaScript template literal
    local function escapeForJS(s)
      s = string.gsub(s, "\\", "\\\\")
      s = string.gsub(s, "`", "\\`")
      s = string.gsub(s, "%$", "\\$")
      return s
    end
    
    local escapedHtml = escapeForJS(html)
    local escapedText = escapeForJS(plaintext)
    
    -- Inject JS to use Clipboard API with both MIME types
    local scriptEl = js.window.document.createElement("script")
    scriptEl.innerHTML = [[
      (async function() {
        const html = `]] .. escapedHtml .. [[`;
        const text = `]] .. escapedText .. [[`;
        try {
          await navigator.clipboard.write([
            new ClipboardItem({
              'text/html': new Blob([html], {type: 'text/html'}),
              'text/plain': new Blob([text], {type: 'text/plain'})
            })
          ]);
        } catch(e) {
          console.error('Clipboard API failed:', e);
          // Fallback to execCommand (loses plain text format)
          const container = document.createElement("div");
          container.innerHTML = html;
          container.style.position = "fixed";
          container.style.left = "-9999px";
          document.body.appendChild(container);
          const range = document.createRange();
          range.selectNodeContents(container);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          document.execCommand("copy");
          sel.removeAllRanges();
          document.body.removeChild(container);
        }
      })();
    ]]
    js.window.document.body.appendChild(scriptEl)
    js.window.document.body.removeChild(scriptEl)
    
    editor.flashNotification("Copied as rich text")
  end
}
```
