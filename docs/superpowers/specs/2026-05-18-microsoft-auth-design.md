# Microsoft Entra ID Auth + Device Tokens — Design

**Date:** 2026-05-18
**Status:** Brainstorming → implementation planning

## Context

The app currently has zero authentication — every route at
`https://gpiplantmanager.com/*` is public. Search-engine indexing of
employee names and production data was discovered today; the bleeding
was stopped at 12:15 PM via `X-Robots-Tag` + `/robots.txt`
([commit `b948292`](https://github.com/dalesgruber/gpi-plant-manager/commit/b948292)).
But noindex only stops crawlers — direct-link access still works.

This spec covers the second pass: lock the door so only GPI employees
get in, while keeping the shop-floor TV displays working unattended.

## Goals

1. Every route requires authentication except a small bypass list
   (auth flow, healthz, robots.txt, static assets, favicon).
2. Only Microsoft Entra ID accounts in the `@gruberpallets.com` tenant
   can sign in.
3. Shop-floor TV displays at `/tv/*` continue to work without
   interactive login, via long-lived signed device tokens.
4. Sessions are sliding 7-day cookies — active users stay logged in,
   inactive users re-auth.
5. Local dev has a one-line env-var bypass.
6. No regressions in any existing functionality.

## Non-goals

- Multi-tenant auth (just `@gruberpallets.com`).
- Role-based access control (every authenticated user sees
  everything; can add roles later).
- Guest / external user accounts (add via Entra ID guest invites if
  needed later).
- Per-user audit log of who-saw-what (add later if compliance demands it).
- A formal CSRF token framework — `SameSite=Lax` cookies handle the
  common cases. Add explicit CSRF tokens later if needed.

## Architecture

### Components

1. **Entra ID app registration** (provisioned by Dale in the Azure
   portal — see "Manual setup" below).
2. **OIDC client** using `Authlib` (well-trodden FastAPI OIDC library,
   ~50 LOC of integration).
3. **Auth middleware** (`_require_auth`) — checks session cookie OR
   device token on every request; redirects unauthenticated traffic
   to `/auth/login`.
4. **Device-token store** — new Postgres table + small library.
5. **Admin UI** for minting and revoking device tokens.

### Files

| File | Purpose |
|---|---|
| `src/zira_dashboard/auth.py` (new) | OIDC client setup, session JWT sign/verify, domain check |
| `src/zira_dashboard/device_tokens.py` (new) | Generate, validate, revoke device tokens |
| `src/zira_dashboard/routes/auth.py` (new) | `/auth/login`, `/auth/callback`, `/auth/logout` |
| `src/zira_dashboard/routes/admin.py` (extend) | `/admin/devices` list/create/revoke |
| `src/zira_dashboard/templates/auth_login.html` (new) | Sign-in landing page |
| `src/zira_dashboard/templates/auth_denied.html` (new) | "This account isn't authorized" page |
| `src/zira_dashboard/templates/admin_devices.html` (new) | Device-token admin |
| `src/zira_dashboard/app.py` (extend) | Wire `_require_auth` middleware into the chain |

### Data flow

**Web user, first visit:**
1. User hits `https://gpiplantmanager.com/recycling`.
2. `_require_auth` finds no session cookie → 302 to
   `/auth/login?next=/recycling`.
3. `/auth/login` redirects to Microsoft's OIDC authorize endpoint.
4. User signs in (and may see a one-time consent screen).
5. Microsoft redirects to `/auth/callback?code=...`.
6. App exchanges code for tokens, validates the `preferred_username`
   claim (always present in Entra ID, equals UPN for work accounts)
   ends in `@gruberpallets.com`. The `email` claim is unreliable — it
   can be unset, set to a personal email, or differ from UPN. Fall
   back to `upn` if `preferred_username` is missing. If neither
   matches → "not authorized" page.
7. App sets `gpi_session` cookie (HTTP-only, Secure, SameSite=Lax,
   7-day Max-Age).
8. App 302s to `/recycling` (the original `next=`).

**Web user, return visit within session window:**
1. Request arrives with valid cookie.
2. Middleware verifies JWT signature + expiry.
3. If cookie's remaining lifetime is `< 6 days` → re-issue with fresh
   7-day expiry (sliding window).
4. Pass through to route.

**TV display:**
1. TV browser opens `https://gpiplantmanager.com/tv/recycling?device=<token>`.
2. Middleware sees no cookie but URL has `device` param.
3. Middleware verifies HMAC signature on token + looks up an
   un-revoked row in `device_tokens` table.
4. If valid AND request path starts with `/tv/` → pass through.
5. If valid but path is non-`/tv/*` → redirect to login. Tokens are
   strictly scoped to TV paths.
6. If invalid → redirect to login.

**Admin minting a device token:**
1. Authed admin visits `/admin/devices`.
2. Clicks "New device", enters a friendly name (e.g. "Bay 3 TV").
3. App generates 32 random bytes, HMAC-signs them, inserts a row.
4. UI shows the full URL `https://gpiplantmanager.com/tv/recycling?device=<token>`
   for the admin to copy.
5. Admin walks to the TV, navigates the browser to the URL once.
6. TV is now displaying authed content; cookie isn't set on the TV
   browser, so the token-in-URL is what keeps the session alive.

### Session cookie format

- Name: `gpi_session`.
- Value: JWT signed with `SESSION_SECRET` env var (HMAC-SHA256).
- Payload: `{sub, upn, name, exp}` where `sub` is the Microsoft OID
  (stable user ID across renames), `upn` is the user-principal-name
  (e.g. `dale@gruberpallets.com`), `name` is the display name, and
  `exp` is 7 days from issue.
- Flags: HttpOnly, Secure, SameSite=Lax, Path=/, Max-Age=604800.
- Sliding refresh: if remaining lifetime `< 6 days` on request,
  re-issue with fresh `exp`.

### Device token format

- Generated: 32 random bytes → urlsafe-base64 → 43 chars. This is the
  **token** value.
- URL form: `?device=<token>.<sig>` where
  `sig = HMAC-SHA256(SESSION_SECRET, token)` (base64-encoded). The
  signature is computed at mint time and embedded in the URL.
- Stored in DB: just the **token** half (NOT the signature). The
  signature is re-derived at validation time using `SESSION_SECRET`.
- Validation: split the URL param on `.`; recompute the HMAC of the
  token half using the current `SESSION_SECRET`; constant-time
  compare to the URL's signature half; then look up an un-revoked
  matching row in `device_tokens`.
- Defense in depth: even if the DB column leaks, an attacker can't
  forge a valid URL without `SESSION_SECRET` (DB has the token but
  not the signature). Even if `SESSION_SECRET` rotates, all existing
  device URLs invalidate at once (which doubles as a panic-button —
  rotate the secret to kick every TV back to the login page).
  Revocation of a single token is instant via setting `revoked_at`.

### Middleware order in `app.py`

1. `GZipMiddleware` (existing).
2. `_security_headers` (existing — HSTS, X-Robots-Tag, etc.).
3. **`_require_auth` (NEW).**
4. `_static_cache_headers` (existing).
5. Routers.

`_require_auth` bypass list (returned without auth check):
`/auth/*`, `/static/*`, `/healthz`, `/robots.txt`, `/favicon.ico`.

### Database schema

```sql
CREATE TABLE IF NOT EXISTS device_tokens (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    token        TEXT UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   TEXT NOT NULL,
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS device_tokens_active_idx
    ON device_tokens (token) WHERE revoked_at IS NULL;
```

`last_used_at` is updated on every successful TV refresh — at 30 s
refresh rate per TV that's ~2 writes/min/TV. Negligible.

### New Railway env vars

| Var | Source | Notes |
|---|---|---|
| `MS_TENANT_ID` | Entra ID app registration → Overview | Tenant GUID |
| `MS_CLIENT_ID` | Entra ID app registration → Overview | Application (client) ID |
| `MS_CLIENT_SECRET` | Entra ID app registration → Certificates & secrets | Generate a secret, copy the value once |
| `SESSION_SECRET` | Generate locally: `python -c "import secrets; print(secrets.token_urlsafe(32))"` | Signs both session cookies AND device tokens |
| `AUTH_DISABLED` | Local dev only — `1` to bypass | Defaults to off; logs a warning at startup if set in production |

## Error handling

| Failure | Behavior |
|---|---|
| Microsoft IDP unreachable | "Sign-in temporarily unavailable, please try again." 503 with the same auth_denied template, no stack traces in the UI. |
| Email domain mismatch | "This app is restricted to GPI employees. Sign in with your `@gruberpallets.com` account." page. |
| Cookie JWT signature invalid (e.g. after secret rotation) | Treat as no cookie — redirect to login. |
| Device token signature invalid | Redirect to login. |
| Device token revoked | Redirect to login. (Admin can re-mint and re-walk to the TV.) |
| `AUTH_DISABLED=1` in production | App logs `WARNING: auth disabled — do not run this way in production` on every startup. |

## Testing

**Unit tests** (no DB required):
- Session JWT sign/verify round-trip with valid + expired payloads.
- Device-token HMAC sign/verify round-trip.
- Domain validator — accepts `@gruberpallets.com`, rejects everything else.
- Bypass-list matcher — exact paths + prefix paths.

**Integration tests** (TestClient, monkeypatched session cookie):
- Unauthed `/recycling` → 302 to `/auth/login?next=/recycling`.
- Authed `/recycling` (valid cookie injected) → 200.
- `/tv/recycling?device=<valid>` → 200 without cookie.
- `/tv/recycling?device=<bad-sig>` → 302 to login.
- `/recycling?device=<valid>` (non-TV path with token) → 302 to login.
- `/static/foo.css`, `/robots.txt`, `/healthz` → 200 without auth.
- `AUTH_DISABLED=1` env var → all of the above return 200 without auth.

## Phasing within this PR sequence

This isn't a single commit — it's three sub-phases so we can roll back
any one of them without losing progress on the others.

| Sub-phase | What ships | Risk |
|---|---|---|
| **2a** Login flow + session cookie + middleware (initially with `AUTH_DISABLED=1` defaulted ON) | All the plumbing, no enforcement. Site is still public but `/auth/login` works end-to-end for testing. | Low. No user-visible change. |
| **2b** Device-token table + admin UI + middleware support for `?device=<token>` | New `/admin/devices` page. TVs can be re-keyed but nothing forces it yet. | Low. Still no enforcement. |
| **2c** Flip `AUTH_DISABLED` off in Railway env. Walk every TV to its new URL. | The cutover — site is now fully behind auth. | High blast radius. Dale should be present for it because if anything's wrong, the shop floor goes dark. |

## Open questions / decisions deferred

1. **Should `/changelog` and `/changelog/latest` be auth-gated?** They're
   used for the unread-indicator UX on the dashboards. **Proposed:
   yes, auth-gated like everything else** — the unread indicator is
   inside an already-authed dashboard, so users will be logged in by
   the time it fires. Public access isn't needed.
2. **Should TVs share one device token, or one per physical TV?**
   **Proposed: one per TV.** Cheap to generate, easier to revoke a
   compromised one, gives `last_used_at` per location for monitoring.
3. **Token-in-URL leakage** — device tokens land in browser history,
   server logs, etc. Acceptable for TVs (no humans logging in there)
   and the HMAC + DB-revoke design means leakage is recoverable. Worth
   documenting in the admin UI: "this URL contains a secret token —
   don't share or screenshot."

## Manual setup (Dale, in the Azure portal)

1. Sign in to <https://portal.azure.com> with a GPI admin account.
2. Navigate to **Microsoft Entra ID → App registrations → New
   registration**.
3. Name: `GPI Plant Manager`. Account types: **Accounts in this
   organizational directory only (single tenant)**.
4. Redirect URI: **Web** type, URL
   `https://gpiplantmanager.com/auth/callback`.
5. Click **Register**.
6. From the new app's **Overview** page: copy **Application (client) ID**
   and **Directory (tenant) ID** → send to Claude.
7. Navigate to **Certificates & secrets → New client secret**. Set
   expiry to 24 months. Copy the **Value** (not the ID) **immediately**
   — it's hidden after page reload → send to Claude.
8. Generate `SESSION_SECRET` locally:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` →
   keep this one yourself, never share, paste into Railway.
9. In Railway, add the four env vars (`MS_TENANT_ID`, `MS_CLIENT_ID`,
   `MS_CLIENT_SECRET`, `SESSION_SECRET`). Do not set `AUTH_DISABLED`
   in production.
