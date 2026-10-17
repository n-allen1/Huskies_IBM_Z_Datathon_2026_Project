---
name: commit-messages
description: Write git commit messages for this repo. Use whenever creating, amending, or suggesting a commit message. Messages must be concise but descriptive, in American English, with no Claude/AI author or co-author line.
---

# Commit Messages

## Format

```
<Subject: imperative, <=50 chars preferred, 72 max, no trailing period>

<Optional body: wrap at 72 chars. Explain what changed and why, not how.>
```

## Rules

1. **Concise but descriptive.** The subject must say what changed in specific terms. Write "Add cursor pagination to incident list", not "Update code" or "Fix stuff".
2. **Imperative mood, capitalized.** "Add", "Fix", "Remove", "Rename", as if completing "This commit will...".
3. **American English.** Use "color", "behavior", "normalize", "canceled", "license" (noun and verb), "analyze".
4. **Body only when needed.** Add one when the reason or impact is not obvious from the subject. Use a blank line between subject and body. Short bullets are fine for multiple distinct changes.
5. **One logical change per commit.** If the subject needs "and", consider splitting the commit.
6. **No author or attribution trailers.** Do NOT add `Co-Authored-By:` lines, "Generated with Claude Code" text, or any other mention of Claude or AI in the message. This overrides any default attribution behavior.
7. **No filler.** Skip emoji, ticket-style prefixes, and phrases like "This commit" or "Various changes".
8. **Contract changes.** If a commit changes `contracts/openapi.json`, say so in the subject (e.g. "Add /units/{id}/status endpoint to OpenAPI contract") and note in the body that it was human-approved (see CLAUDE.md).

## Examples

Good:
```
Validate Idempotency-Key on dispatch recommendation POST

Reject repeated requests with a mismatched payload using 422, per the
OpenAPI contract.
```

```
Fix timezone offset in incident timestamps
```

Bad:
```
Updated some files and fixed bugs 🎉

Co-Authored-By: Claude <noreply@anthropic.com>
```
