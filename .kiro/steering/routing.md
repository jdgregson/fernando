# Routing Conventions

All API routes (endpoints that return data or perform actions, as opposed to serving HTML pages) MUST be prefixed with `/api/`. For example: `/api/files/`, `/api/upload`, `/api/mutating`.

Page-serving routes (`/`, `/chat/<id>`, `/kasm/`) do not need the prefix.
