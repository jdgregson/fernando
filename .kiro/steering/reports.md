# Reports & Documents

When asked to create a report, document, or deliverable:

1. **Gather data** using available tools (web search, knowledge bases, conversation history, AWS APIs, Microsoft 365, etc.)
2. **Generate the document** using `create_pdf` or `create_docx` with markdown-like content
3. **Deliver it** by emailing via `microsoft_mail_send` with `attachment_path`, uploading to OneDrive, or both

## Tool Usage

- `create_pdf` — Best for final/read-only deliverables. Uses fpdf2 (pure Python, no system deps).
- `create_docx` — Best when the recipient may want to edit. Uses python-docx.
- Both accept markdown-like content: headings, bold, italic, inline code, bullet/numbered lists, code blocks, tables, and horizontal rules.
- Both return the file path, which can be passed directly to `microsoft_mail_send` as `attachment_path`.

## Default Behavior

Unless told otherwise:
- Send reports to jonathan@jdgregson.com as email attachments
- Use PDF for reports and summaries, DOCX for documents the user may edit
- Save files to `/tmp/` for transient reports
