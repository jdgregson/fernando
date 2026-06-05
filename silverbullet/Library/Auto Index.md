```space-lua
-- Auto-index: when navigating to a page that doesn't exist or is empty,
-- and child pages exist under it as a folder, generate a listing.
local function autoIndex()
  local currentPage = editor.getCurrentPage()

  -- If page exists and has content, don't touch it
  if space.pageExists(currentPage) then
    local content = space.readPage(currentPage)
    if string.len(string.trim(content)) > 0 then
      return
    end
  end

  -- Check if any pages exist under this path as a folder prefix
  local prefix = currentPage .. "/"
  local pages = space.listPages()
  local children = {}
  for page in each(pages) do
    if string.startsWith(page.name, prefix) then
      table.insert(children, page.name)
    end
  end
  if #children == 0 then
    return
  end

  -- Sort alphabetically
  table.sort(children)

  -- Build an index page
  local lines = {"# " .. currentPage, ""}
  for child in each(children) do
    table.insert(lines, "- [[" .. child .. "]]")
  end
  local indexContent = table.concat(lines, "\n") .. "\n"
  space.writePage(currentPage, indexContent)
  editor.flashNotification("Auto-generated folder index")
end

event.listen {
  name = "editor:pageLoaded",
  run = autoIndex
}
```
