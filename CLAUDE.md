# DispatchZ – Agent Instructions

## Commits

Follow the `commit-messages` skill (`.claude/skills/commit-messages/SKILL.md`) for every commit message: concise but descriptive, American English, and no Claude/AI author, co-author, or "generated with" lines (commits and PR descriptions alike).

## API Contract Governance (MANDATORY)

This project is **contract-first**. The canonical OpenAPI contract is **`contracts/openapi.json`** (OpenAPI 3.1, "DispatchZ API"). `contracts/swagger.html` is only a viewer. If another spec file appears, or the canonical one is unclear, stop and ask a human which is authoritative.

`scripts/gen_openapi.py` generates the contract and is treated as a contract source: editing it, or re-running it to overwrite `contracts/openapi.json`, is a contract change and needs the same human approval. Never hand-edit the JSON in a way the generator would not reproduce; change the generator, then regenerate (after approval).

The latest **human-approved, committed** version of that file is the source of truth. (The repo currently has no commits; until the first commit, the file as the human last handed it to you is the baseline. Recommend the human commit it as the approved baseline.) Implementation conforms to the contract, never the reverse.

### Operating rule

**Before changing an API endpoint, request/response schema, or related public interface, compare the intended change against the approved OpenAPI contract. If there is any deviation, request human approval before making that change.**

### What counts as a deviation (needs approval)

Adding/removing/renaming endpoints; changing methods or paths; adding/removing/renaming request or response properties; changing types, formats, constraints, nullability, or required-vs-optional; changing status codes, response structure, parameters, validation rules, auth/authz requirements (`bearerAuth`, scopes, roles), headers (e.g. `Idempotency-Key`), pagination/sort conventions, or documented semantics; DB/model changes that alter what the API exposes; undocumented endpoints or response fields. **Backward-compatible additions also require approval.**

Contract-compliant implementation work (internals, refactors, bug fixes, UI that uses existing fields) needs no approval.

### When a deviation is found: STOP that change and ask

Present a concise proposal, then explicitly ask "Approve this contract amendment?":

1. **Affected endpoint(s)** (METHOD /path)
2. **Current contract**
3. **Proposed change**
4. **Reason**
5. **Compatibility impact** (breaking / non-breaking)
6. **Affected components** (frontend, backend, DB, tests, SDKs, mocks)
7. **Recommendation** (amend vs. a contract-compliant alternative)

While waiting: do **not** edit `contracts/openapi.json`, do **not** implement the deviating change, and do **not** add stubs, flags, or hidden fields as a workaround. Continue only independent, contract-compliant work.

### What is NOT approval

Passing tests, a clean lint/diff tool, a feature request that doesn't mention the schema, or sign-off from another AI agent. Only an explicit statement from the human user in the conversation (or a human-reviewed, merged change to the contract) approves a change. Approval covers only the specific change described. Never edit tests to hide a contract mismatch.

### After approval

1. Update `contracts/openapi.json` first. 2. Validate it (lint). 3. Implement backend. 4. Update frontend clients/generated types. 5. Update validation, tests, docs, mocks. 6. Verify conformance to the updated spec. 7. Report the approved changes and results.

### Parallel work

Many agents/developers may work simultaneously. Read the relevant paths/schemas in the contract before building anything API-dependent. Never redefine shared shapes locally; use types/clients generated from the contract where practical. Identify contract dependencies before changing DB models or services. If you find that another component disagrees with the contract during integration, flag it to the human rather than "fixing" either side silently. Unrelated, compliant work is never blocked.

### Drift detection (lightweight)

Verification is **not** authorization; passing checks never replace human approval.

- Before finishing API work: `git diff -- contracts/openapi.json` must be empty unless a change was approved.
- Lint/validate the spec when tooling exists (e.g. `npx @redocly/cli lint contracts/openapi.json` or Spectral).
- Spec-diff against the last approved commit when tooling exists (e.g. `oasdiff breaking` / `oasdiff changelog`).
- Run contract-driven tests against the Prism mock (`http://localhost:4010`) and/or schema-validate real responses against the spec.
- Recommended CI (not yet configured): lint + oasdiff on PRs, and a CODEOWNERS rule requiring human review of `contracts/openapi.json`.

### End-of-task report (for any API-related task)

State: (1) whether the OpenAPI contract was consulted; (2) whether the implementation remains contract-compliant; (3) whether contract changes were proposed or approved; (4) which validation/tests were run and their results.
