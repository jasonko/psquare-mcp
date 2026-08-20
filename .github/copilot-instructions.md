# Copilot Instructions

This repository's contributor and architecture guidance lives in [CLAUDE.md](../CLAUDE.md).

Please read and follow [CLAUDE.md](../CLAUDE.md) — it is the single source of truth for
architecture, key patterns (auth, JSON:API, HTML parsing, response formats), development
commands, and the release process. Multiple AI tools work on this codebase, so all shared
instructions are maintained there to avoid duplication and drift.

Per-endpoint traps are deliberately **not** in CLAUDE.md. They are documented in a docstring
beside the code they guard and pinned by a test, so they cannot drift out of sync — read the
docstring before changing any write body, form-field extractor, or request header.
