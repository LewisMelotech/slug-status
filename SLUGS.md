# Custom script slugs (fork-only feature)

A **slug** is an optional, human-chosen alternative id for a `Script`. It is
canonical in the same sense the numeric primary key is: `/script/<slug>` and
`/api/script_ids/slug/<slug>/` resolve to exactly the same script that
`/script/<pk>` and `/api/script_ids/<pk>/` do.

This is not in upstream `AdmiralGT/botc-scripts`. Everything here lives on the
`custom-slugs` branch of this fork.

## Rules

A slug is stored lowercase, and mixed-case input is folded rather than rejected.

| Rule | Why |
| --- | --- |
| Lowercase ASCII letters and digits, single internal hyphens: `^[a-z0-9]+(?:-[a-z0-9]+)*$` | URL-safe, and exactly one spelling per slug |
| 2 to 50 characters | `Script.slug` is a `SlugField(max_length=50)` |
| **Must not parse as an integer** (`13108`, `007`, `-12`, `1_0`, `+9` are all refused) | A slug shares its URL position with the numeric script id, and clients route on "is this all digits?". An integer-looking slug would silently resolve to a different script |
| Must not be a reserved path segment (`search`, `upload`, `api`, `admin`, `all-roles`, …) | `/script/<slug>` would shadow an existing route. The full set is `scripts.slugs.RESERVED_SLUGS` |
| Unique, case-insensitively | `sects` and `SECTS` are the same slug |
| Optional | Unslugged scripts store `NULL`, and any number of them may coexist |

Rules are enforced by `scripts.slugs.validate_script_slug`, attached to the model
field, so every write path gets them: the API, the Django admin, and any
`full_clean()`.

## API

Reads are anonymous, as they were before. Writes need HTTP Basic auth as a user
holding the **`scripts.api_write_permission`** permission — the same credential
the existing upload API uses. No new permission, no new auth scheme.

### Look up a script by slug

```
GET /api/script_ids/slug/<slug>/
```

`200` with the usual `ScriptSerializer` body; `404` if no script has that slug.
The slug in the URL is case-folded, so `/slug/SECTS/` and `/slug/sects/` agree.

```json
{
  "pk": 7,
  "name": "Sects and Violets",
  "slug": "sects",
  "versions": {"1.0.0": "http://.../api/scripts/4/", "2.0.0": "http://.../api/scripts/5/"},
  "latest_version": "http://.../api/scripts/5/"
}
```

### Filter the script list by slug

```
GET /api/script_ids/?slug=<slug>
```

`200` with the standard paginated envelope; `count` is `0` when nothing matches.

### Set a slug

```
PATCH /api/script_ids/<pk>/slug/
Authorization: Basic <base64 user:password>
Content-Type: application/json

{"slug": "sects-and-violets"}
```

| Response | Meaning |
| --- | --- |
| `200` + the `ScriptSerializer` body | Set. The body's `slug` is the stored (normalised) value |
| `400 {"slug": ["..."]}` | Failed a validation rule, or the slug is taken |
| `403 {"detail": "Authentication credentials were not provided."}` | No credentials |
| `403 {"detail": "Invalid username/password."}` | Wrong credentials |
| `403 {"detail": "You do not have permission to perform this action."}` | Authenticated, but without `scripts.api_write_permission` |
| `404` | No script with that pk |

`slug` is **required** in the body: a `{}` body is a `400`, never a silent no-op.

### Clear a slug

Either of:

```
PATCH /api/script_ids/<pk>/slug/     {"slug": null}      (or {"slug": ""})
DELETE /api/script_ids/<pk>/slug/    (no body)
```

Both return `200` with the script body and `"slug": null`. A freed slug can
immediately be taken by another script.

### Slug in existing payloads

`slug` was **added** to two existing response shapes; nothing was removed or
renamed, so clients that do not know about slugs are unaffected.

- `ScriptSerializer` (`/api/script_ids/…`) gains `"slug"`.
- `VersionSerializer` (`/api/scripts/…`) gains `"slug"`, carried across the
  foreign key the same way `name` already is. It is the slug of the version's
  *script*, and is `null` for a script with no slug.

## Web

- `/script/<slug>` and `/script/<slug>/<version>` work alongside the numeric
  routes, which are unchanged. Slug URLs are case-folded.
- Those routes are registered **last** in the `script/` block of
  `scripts/urls.py`. Django's slug converter matches `[-a-zA-Z0-9_]+`, which
  also matches `123`, `search`, `upload` and `all_roles`, so a slug pattern
  registered any earlier would swallow them.
- They carry the route name `script_by_slug`. The numeric routes keep `script`,
  which `scripts/tables.py` reverses for every row link.
- The script page shows the slug as a short-link badge when one is set.

## Admin

`Script` now has a `ScriptAdmin`: `slug` appears in the changelist (editable
inline), is searchable, and is prepopulated from the name on the add form.
Mixed-case input is folded before the uniqueness check, so `SECTS` is reported
as a duplicate of an existing `sects` rather than failing at the unique index.
