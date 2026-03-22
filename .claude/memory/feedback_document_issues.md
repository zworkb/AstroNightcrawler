---
name: Document work on GitHub issues
description: Always write a summary comment on issues before closing them
type: feedback
---

When closing a GitHub issue, always add a comment documenting what was done: implementation approach, files changed, and any notable decisions or workarounds.

**Why:** The user wants a traceable history of all changes for future reference.

**How to apply:** Before or when closing an issue with `gh issue close`, add a comment via `gh issue comment` with a "## Implementation" section listing: what was changed, which files, and why.