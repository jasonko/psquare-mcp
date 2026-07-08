# ParentSquare MCP Server

[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io) [![PyPI](https://img.shields.io/pypi/v/parentsquare-mcp)](https://pypi.org/project/parentsquare-mcp/)

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives Claude access to [ParentSquare](https://www.parentsquare.com), a school-parent communication platform. Since ParentSquare has no public API, this server scrapes the web interface using saved session cookies.

Available on the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.thehesiod/psquare` and on [PyPI](https://pypi.org/project/parentsquare-mcp/) as `parentsquare-mcp`.

## Disclaimer

> **This project is not affiliated with, endorsed by, or sponsored by ParentSquare, Inc.** "ParentSquare" and all related names, logos, and trademarks are the property of ParentSquare, Inc.
>
> This server communicates with ParentSquare's **undocumented internal APIs** (scraping the web UI and calling its non-public `/api/v2/` JSON endpoints) — these are not published, not guaranteed to be stable, and may change or be blocked at any time without notice. Use of those interfaces may violate ParentSquare's Terms of Service; you are responsible for reviewing the ToS and deciding whether your use is acceptable.
>
> **Use at your own risk.** The authors and contributors accept no responsibility for any consequences of using this software, including but not limited to: account suspension or termination, data loss or corruption, missed or incorrect notifications, MFA lockouts, leaked session cookies, IP blocks, or any other direct or indirect damages. No warranty is provided — see [LICENSE](LICENSE) for the full MIT no-warranty clause.
>
> If ParentSquare publishes an official API, this project should be considered deprecated in favor of that.

## Features

### Feed & Posts
- **`get_feeds`** — Browse paginated school feed with titles, authors, summaries, and attachment names
- **`get_post`** — Full post details with body text, comments, poll results, signup items, and **inline image/PDF content** (Claude can "see" attached calendars, flyers, etc.)
- **`get_group_feed`** — Posts from a specific group

### Calendar
- **`get_calendar_events`** — Events from ICS calendar as structured JSON (title, start/end, location, description)
- Falls back to guiding Claude to search feed posts for image/PDF calendars when ICS is empty

### Communication
- **`list_conversations`** / **`get_conversation`** — Read message threads
- **`get_directory`** — Staff directory as structured JSON (name, role, phone, user_id)
- **`get_staff_member`** — Full staff details with email, office hours, and **inline profile photo**

### Media & Files
- **`list_photos`** — Photo gallery with URLs
- **`list_files`** — Document files
- **`download_file`** — Download any attachment to local disk

### Participate
- **`list_signups`** — Sign-up and RSVP posts with progress tracking (e.g. "53/103 Items")
- **`list_notices`** — Alerts and secure documents
- **`list_polls`** — Polls with vote counts and winning options
- **`list_forms`** — Permission slips and signable forms
- **`list_payments`** — Payment items with prices and summary stats
- **`list_volunteer_hours`** — Logged volunteer hours with totals

### Groups & Discovery
- **`list_schools`** — Schools and students as structured JSON
- **`list_school_features`** — Available sections per school (parsed from sidebar)
- **`list_groups`** — Groups with member counts, descriptions, and membership status
- **`list_links`** — Quick-access links (Google Drive, external sites)

### Student
- **`get_student_dashboard`** — School, grade, classes, and teachers as structured JSON

### Admin (roster: students & guardians)
Read tools are always available; **write tools are disabled by default** and only run when `PS_ENABLE_WRITES` is set (see below). Every write is recorded to a local audit log. v1 is create/edit only — no destructive operations.
- **`list_students`** — School roster (id, name, grade, SIS id, guardians) as structured JSON, with optional `grade` / `name_contains` filters
- **`list_parents`** — Guardian roster (user_id, name, email, phone, linked students) as structured JSON, with optional `name_contains` / `student_name_contains` filters; provides the `user_id` needed by `edit_parent` / `link_guardian_to_student`
- **`list_grades`** — A school's grades and their `grade_id` values (needed for add/edit)
- **`get_student`** — Admin detail for one student (name, grade, SIS id, linked guardians, classes)
- **`add_student`** *(write)* — Create a student in a grade
- **`edit_student`** *(write)* — Update a student's name, SIS id, or grade (unchanged fields preserved)
- **`add_parent`** *(write)* — Create a guardian linked to a student
- **`edit_parent`** *(write)* — Update a guardian's name, email, or phone (existing links preserved)
- **`link_guardian_to_student`** *(write)* — Link an existing guardian to an additional student

### Authentication
- **`submit_mfa_code`** — Complete MFA verification with a 6-digit code
- Supports MCP elicitation for inline MFA prompts
- Session cookies persisted to `~/.parentsquare_cookies.json`
- Credentials loaded from environment variables, 1Password, or LastPass CLI on session expiry

## Setup

### Enabling admin write tools

The admin write tools (`add_student`, `edit_student`, `add_parent`, `edit_parent`,
`link_guardian_to_student`) modify the live school roster, so they are **off by
default**. To enable them, set `PS_ENABLE_WRITES=1` (or `true`/`yes`/`on`) in the
server's environment and restart. Every write attempt (including blocked ones) is
appended as JSONL to `PS_AUDIT_LOG` (default `~/.parentsquare_audit.log`). Read
tools (`list_students`, `list_parents`, `list_grades`, `get_student`) work regardless.

### Prerequisites

Credentials can be provided in either of two ways (checked in this order):

1. **Environment variables** — set `PS_USERNAME` and `PS_PASSWORD`
2. **A credential manager** selected by `PS_CREDENTIAL_PROVIDER` (default `1password`):
   - **[1Password CLI](https://developer.1password.com/docs/cli/)** (`op`) — with a "Parentsquare" item containing `username` and `password` fields
   - **[LastPass CLI](https://github.com/LastPass/lastpass-cli)** (`lpass`) — set `PS_CREDENTIAL_PROVIDER=lastpass`. Run `lpass login <your-lastpass-email>` in a terminal first (may prompt for MFA). The item read defaults to `parentsquare.com` and can be overridden with `PS_LASTPASS_ITEM` (an exact entry name or entry ID).

### Install in Claude Code

```bash
claude mcp add --transport stdio parentsquare -- uvx --from "parentsquare-mcp @ git+https://github.com/thehesiod/psquare-mcp" parentsquare-mcp
```

To enable PDF text extraction for post attachments (optional, AGPL-3.0 licensed):

```bash
claude mcp add --transport stdio parentsquare -- uvx --from "parentsquare-mcp[pdf] @ git+https://github.com/thehesiod/psquare-mcp" parentsquare-mcp
```

### That's It

No further configuration needed. The server **auto-discovers** your schools, students, and user ID from ParentSquare on first use. Authentication is handled automatically — when the session expires, the server loads your credentials from environment variables (or 1Password CLI) and re-authenticates (including MFA if needed).

To use environment variables with Claude Code, add an `env` block to your MCP config:

```json
{
  "mcpServers": {
    "parentsquare": {
      "command": "uvx",
      "args": ["parentsquare-mcp"],
      "env": {
        "PS_USERNAME": "your@email.com",
        "PS_PASSWORD": "your-password"
      }
    }
  }
}
```

> **Security note:** environment variables place your password in plaintext inside your MCP config file. If you chose a password manager specifically to avoid that, prefer the 1Password or LastPass CLI path.

To use the LastPass CLI instead of 1Password, log in once (`lpass login <your-lastpass-email>`) and set the provider in your MCP config:

```json
{
  "mcpServers": {
    "parentsquare": {
      "command": "uvx",
      "args": ["parentsquare-mcp"],
      "env": {
        "PS_CREDENTIAL_PROVIDER": "lastpass",
        "PS_LASTPASS_ITEM": "parentsquare.com"
      }
    }
  }
}
```

`PS_LASTPASS_ITEM` is optional (defaults to `parentsquare.com`).

## How It Works

The server uses `requests` + `BeautifulSoup` to scrape ParentSquare's server-rendered HTML pages. Each tool follows the pattern:

1. **Fetch** the HTML page via `PSClient.get_page()` or JSON via `PSClient.get_json()` (auto-relogins on session expiry)
2. **Parse** with a dedicated parser in `parsers/` that extracts structured data into dataclasses
3. **Return** results as either structured JSON dicts (for data-lookup tools) or markdown text (for content-rich tools)

Data-lookup tools (`list_schools`, `get_directory`, `get_calendar_events`, `get_student_dashboard`, `get_staff_member`) return structured JSON for easy programmatic access. Content tools (`get_post`, `get_feeds`, `get_conversation`) return markdown.

On first use, the server auto-discovers your schools, students, and user ID from ParentSquare (no config file needed).

For `get_post`, image attachments are downloaded and returned as MCP `Image` objects (so Claude can see them), and PDF attachments have their text extracted via pymupdf. `get_staff_member` also returns inline profile photos.

Groups use a GraphQL endpoint (`/graphql`) instead of HTML scraping. The directory and staff details use the internal `/api/v2/` JSON:API.

## Dependencies

| Package | Purpose | License |
|---------|---------|---------|
| `mcp` | Model Context Protocol SDK | MIT |
| `requests` | HTTP client | Apache 2.0 |
| `beautifulsoup4` | HTML parsing | MIT |
| `icalendar` | ICS calendar parsing | BSD |
| `pymupdf` | PDF text extraction (optional) | AGPL-3.0 |

## License

MIT — see [LICENSE](LICENSE). Note: the optional `pymupdf` dependency is AGPL-3.0 licensed.

mcp-name: io.github.thehesiod/psquare
