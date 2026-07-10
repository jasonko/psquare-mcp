# ParentSquare MCP Server

## Architecture

MCP server that scrapes ParentSquare's web UI. Runs as stdio transport. While there's no documented public API, ParentSquare has an internal JSON:API at `/api/v2/` that some tools use (e.g. directory).

```
server.py          — MCP tool definitions, inline image/PDF fetching
client.py          — HTTP client with auto-relogin on session expiry
auth.py            — Cookie persistence (~/.parentsquare_cookies.json), credential loading (env vars → 1Password/LastPass), MFA flow
audit.py           — Write-gate (PS_ENABLE_WRITES) + JSONL audit log for admin write tools
config.py          — URL templates and constants (no personal data — auto-discovered at runtime)
models.py          — Dataclasses for all parsed entities
download.py        — File download with conflict handling
parsers/           — One module per page type (feeds, calendar, media, messages, etc.); parsers/admin.py holds roster/edit-form parsing + write-body builders
export_cookies.py  — CLI helper to bootstrap cookies from browser DevTools
```

## Key Patterns

### Authentication
- Cookies are lazy-loaded from `~/.parentsquare_cookies.json` on startup (no network call)
- On session expiry (detected by redirect to `/signin` **or** missing `gon.user_id` on the root page), credentials are loaded via `load_credentials()`: first from `PS_USERNAME`/`PS_PASSWORD` env vars, then from the provider named by `PS_CREDENTIAL_PROVIDER` (default `1password`). Options:
  - `1password` — `op item get Parentsquare` (item named "Parentsquare" with fields labeled `username` and `password`)
  - `lastpass` — `lpass show --json <item>` where the item defaults to `parentsquare.com` and is overridable with `PS_LASTPASS_ITEM` (exact name or entry ID). Requires a prior `lpass login <email>`; `lpass status --quiet` is checked first (short timeout) so requests never hang on an interactive prompt. Credential values are never logged or included in error messages.
- **Important**: ParentSquare's root page (`/`) returns HTTP 200 even for unauthenticated users, so `/signin` redirect alone is not sufficient to detect expired sessions. `discover_account()` and `is_session_valid()` also check for `gon.user_id` in the page content.
- MFA code submission verifies the session is actually authenticated after the code is accepted
- MFA state persists to disk (`.parentsquare_mfa_state.json`) so it survives server restarts
- The server supports MCP elicitation for inline MFA code entry
- **User-Agent must include "Chrome"** — ParentSquare returns 403 `browser_unsupported` otherwise. The server sets this in `app_lifespan`.
- The `ps_s` session cookie is **httpOnly** — it can't be read via `document.cookie`, which is why `export_cookies` requires the Network tab in DevTools
- `ps_s` rotates on every request. `PSClient` calls `_save_cookies_if_changed()` after each successful request to persist the latest value.
- GraphQL requests (used by `list_groups`) require a CSRF token extracted from a page's `<meta name="csrf-token">` tag. MFA submit also requires a CSRF token from the MFA page.

### JSON:API (`/api/v2/`)
ParentSquare has an internal JSON:API (not publicly documented). Discovered by inspecting JS bundle XHR calls:
- **`/api/v2/schools/{id}`** — school info (name, phone, address, timezone). Used by `get_directory`.
- **`/api/v2/schools/{id}/directory`** — staff directory (JSON:API format with `included` array containing staff records). Used by `get_directory`.
- **`/api/v2/schools/{id}/users/{user_id}`** — individual staff details (email, photo, virtual phone, office hours). Used by `get_staff_member`.
- **`/api/v2/users/{id}/virtual_phone_search`** — POST with `{"staff_ids": [...]}` to batch-fetch virtual phone numbers. Used by `get_directory`.
- **`/api/v2/sections/{id}/staff`** and **`/api/v2/sections/{id}/students`** — per-section (class) directory lookups.
- Use `client.get_json()` for GET and `client.post_json()` for POST (handles CSRF tokens automatically).
- Many pages that appear empty in HTML are actually shell pages that load data via this API. If an HTML parser returns no data, check the JS bundle for `/api/v2/` XHR calls.

### HTML Parsing
- All parsing uses BeautifulSoup with `html.parser`
- Two distinct image patterns exist in the DOM:
  - `img.feed-image-thumbnail` — gallery/attached images (outside description div)
  - `<img>` inside `.description` div — inline embedded images
- S3/CloudFront download links carry original filenames in `response-content-disposition` query params
- URL deduplication via `_url_path_key()` prevents returning the same image as both thumbnail and full-size

### Response Formats
- **Structured JSON** (`-> dict`): `list_schools`, `get_calendar_events`, `get_directory`, `get_student_dashboard` — return dicts that FastMCP serializes as JSON. Better for data-lookup where Claude filters/extracts fields.
- **Mixed list** (`-> list`): `get_post`, `get_staff_member` — return a list of text + MCP `Image` objects for inline media.
- **Markdown text** (`-> str`): all other tools — formatted markdown for content-rich responses.

### Inline Content
- `get_post`: images downloaded as MCP `Image` objects (5 MB per image, 10 MB total cap), PDFs text-extracted via pymupdf
- `get_staff_member`: profile photo returned as inline `Image`
- This lets Claude "see" attached calendars, flyers, staff photos etc. without extra tool calls

### Admin write tools (roster: students & guardians)
Reverse-engineered from the admin roster UI and verified live (endpoints/bodies documented in Jason's vault note "ParentSquare admin API mapping"). v1 scope is **create/edit only** — no destructive ops (delete/unlink are deferred to the website).

- **Write gate:** every write tool checks `writes_enabled()` (env `PS_ENABLE_WRITES`, default off) and returns a friendly message if disabled. Reads (`list_students`, `list_parents`, `list_grades`, `get_student`) are ungated.
- **Audit:** `audit_write(tool, args, ok, detail)` appends JSONL to `PS_AUDIT_LOG` (default `~/.parentsquare_audit.log`) for every attempt, including blocked ones.
- **Writes are `application/x-www-form-urlencoded` Rails posts**, not JSON — use `PSClient.post_form(path, data)` (auto-injects `utf8=✓` + `authenticity_token`, sends `X-CSRF-Token`). It does **not** raise on 4xx/5xx; interpret the result with `write_succeeded()` (**success = HTTP 200 + `text/javascript` body: either a reload script or an `alert-success` flash**; an `alert-danger`/`alert-error` flash or an HTML error page, e.g. 404, is failure). `parse_flash_message()` pulls ParentSquare's own flash text so `_write_result` can surface it (e.g. invite counts).
- **No created id is returned** — after add_student/add_parent, re-query `list_students` / the roster to get the new id.
- **Rails method-override:** edit uses a POST with `_method=patch`.
- **Endpoints:** add_student `POST /schools/{id}/students`; edit_student `POST /schools/{id}/students/{sid}` (`_method=patch`); add_parent `POST /schools/{id}/users`; edit_parent + link_guardian `POST /schools/{id}/users/{uid}/update_institute_user` (`_method=patch`).
- **Parent invitations** (verified live; see vault note "ParentSquare invitations API mapping"): `invite_parent` = `POST /schools/{id}/users/{user_id}/invite` (CSRF-only form body, via `post_form(path, {})`) — emails one guardian, resend uses the same endpoint. `bulk_invite_parents` = `POST /schools/{id}/users/invite` with a **JSON** body `{"ids": "<comma-joined user_ids>", "role": "PARENT", "selected": N}` (via `post_form`-style CSRF but JSON) — sends email/text, auto-skips already-registered ids, and the response is UJS `text/javascript` (not JSON), so it uses **`PSClient.post_json_raw()`** (POST JSON, return raw `Response`). "Invite all" = `bulk_invite_parents` over every `list_parents` guardian with `registered=false` (no dedicated endpoint). Target = guardian `user_id` from `list_parents`.
- **Roster feeds** (positional-array JSON, whole roster in one call, client-side paging): `GET /schools/{id}/roster/students_data` (14 cols, surfaced by `list_students`) and `.../parents_data` (12 cols, surfaced by `list_parents` — the only tool that exposes a guardian `user_id`) — parsed in `parsers/admin.py`.
- **Edit forms** (`.../{sid}/edit`, `.../{uid}/edit_institute_user?role=PARENT`) return JS-escaped HTML; `parsers/admin.py` extracts current field values (and the parent's shared `contact_id`) so edits preserve unchanged fields. `PSClient.get_text()` fetches these.
- **Guardian links (nested attrs):** omitting `kids_attributes` on a parent PATCH leaves existing links untouched (verified) — so `edit_parent` sends none and `link_guardian_to_student` sends only the new kid under a unique numeric key. `edit_parent` email/phone are **one** contact record addressed at indices `[0]`(email)/`[2]`(phone) with the same `contact_id`.
- **grade_id** is per-school (from `list_grades` / the roster add-modal `<select name="student[grade_id]">`). add_parent/link resolve the kid's grade_id from its edit form.


## Known Gotchas

### Schools Without ICS Calendars
Some schools don't use the ICS calendar feature. Instead, monthly calendars are posted as **image attachments** in feed posts (e.g. weekly update posts). When `get_calendar_events` returns empty, Claude should:
1. Browse feeds looking for posts with calendar-like attachment names or body text mentioning "calendar"
2. Open those posts to view the inline calendar images
3. Read the calendar image content to answer date questions

### Feed Text: Expanded vs Truncated
ParentSquare renders both a truncated and expanded (full) version of each post's text in the feed HTML. The expanded version is hidden via `display: none` CSS. The feed parser prefers the expanded version, giving Claude full post text without extra HTTP requests. This is critical — key phrases like "review the attached calendar" or "February Break" are often past the truncation boundary.

## Development

```bash
uv run parentsquare-mcp              # Run the MCP server
uv run parentsquare-export-cookies   # Bootstrap cookies from browser
uv run --group dev pytest            # Run the unit tests
```

### Adding a New Parser
1. Create `parsers/<name>.py` with a `parse_*` function that takes `BeautifulSoup` and returns dataclass(es)
2. Add dataclass(es) to `models.py`
3. Add the tool in `server.py` using the `@mcp.tool` decorator
4. Wire through `_with_mfa_retry` for auth handling
5. Add the URL template to `config.py` if needed

### Account Discovery
Schools, students, and user ID are auto-discovered at runtime from ParentSquare pages (`gon.*` script variables, sidebar student links, and the school switcher AJAX endpoint). School names are fetched via `/api/v2/schools/{id}`. No config file needed.

## Release Process

Publishing is automated via `.github/workflows/publish.yml`. Uses **PyPI Trusted Publishers** (OIDC) and GitHub OIDC for the MCP Registry — no API tokens stored anywhere.

### One-time setup (pypi.org)

Before the first CI-driven release, add a "pending" trusted publisher on pypi.org:

1. Log into [pypi.org](https://pypi.org) as the account that owns the project.
2. Go to [Manage → Publishing → Add a new pending publisher](https://pypi.org/manage/account/publishing/).
3. Fill in:
   - **PyPI Project Name**: `parentsquare-mcp`
   - **Owner**: `thehesiod`
   - **Repository name**: `psquare-mcp`
   - **Workflow name**: `publish.yml`
   - **Environment name**: *(leave blank, or set e.g. `release` for a manual-approval gate)*
4. Save. The publisher activates on the first successful tag push that runs `publish.yml`.

*(The MCP Registry namespace `io.github.thehesiod/*` is already auto-authorized for the `thehesiod` GitHub account via OIDC — no pypi-style pending-publisher setup needed.)*

### Cutting a release

1. Bump the version in **both** `pyproject.toml` and `server.json` (the workflow fails if they don't match — both `version` and `packages[0].version` in `server.json`).
2. Commit: `chore: bump to X.Y.Z`.
3. Tag + push (tags are bare semver — no `v` prefix):
   ```bash
   git tag X.Y.Z
   git push origin main --tags
   ```
4. The workflow:
   - Verifies versions match the tag across `pyproject.toml` + `server.json`
   - Builds wheel + sdist with `uv build`
   - Publishes to PyPI via OIDC
   - Publishes to the MCP Registry via `mcp-publisher login github-oidc`
   - Creates a GitHub Release with auto-generated notes and the built artifacts

### Ownership proof for the MCP Registry

The PyPI package README must contain the literal line `mcp-name: io.github.thehesiod/psquare` (see the bottom of `README.md`). The registry's publisher validates this by fetching the published PyPI artifact and looking for that string. Removing the line will break future registry publishes.

## Open Improvement Areas

- **Feed search**: No keyword search/filter on `get_feeds` — Claude must paginate and scan titles/summaries manually. A search tool or keyword parameter would help.
- **CloudFront URL expiry**: S3/CloudFront signed URLs expire. Cached attachment URLs from older sessions may 403.
