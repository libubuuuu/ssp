---
name: project-ssp-2026-05-23-daily
description: 2026-05-23 auth登录过期修复：access token 24h + proactive refresh cookie修复，commit 38eebb2
metadata: 
  node_type: memory
  type: project
  originSessionId: ea970383-b924-47d6-82d5-cd1a1bbb168e
---

access token 24h + proactive refresh cookie路径修复，commit 38eebb2，green active.

**Changes:**
- `JWT_ACCESS_EXPIRATION_HOURS`: 1h → 24h (`services/auth.py`)
- `ACCESS_COOKIE_MAX_AGE`: 3600 → 86400 (`api/auth.py`) — cookie TTL must match JWT TTL
- `AuthFetchInterceptor.tsx` proactive refresh guard: `!token || !refresh` → `!token` — cookie-only users now get proactive refresh
- proactive refresh body: `{refresh_token:refresh}` → `refresh ? {refresh_token:refresh} : {}` — empty body falls back to cookie on server

**Why:** commit `884b47a` reduced access token from 7d→1h. Cookie-only users (P8 transition) had no `refresh_token` in localStorage so proactive refresh silently skipped them. After 1h, reactive refresh tried cookie — if cookie present, silent recovery; if gone, user kicked out.

**Deploy note:** green venv had wrong shebangs (`#!/root/ssp/backend/venv/bin/python3`), fixed with Python script before green start. See [[feedback-ssp-venv-shebang-fix]].

**How to apply:** Next auth-related task, note that access token is now 24h. The `tokens_invalid_before` revocation mechanism is the primary security backstop for longer token window.
