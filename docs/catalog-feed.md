# Command catalog feed

Nexus Remote ships with a curated catalog of commands for `bash`, PowerShell,
`cmd`, Cisco IOS and PAN-OS. The **Commands** page can additionally download a
vendor catalog and merge it into the completion engine without restarting the
app.

Updates are optional. With no feed URL configured the application stays fully
offline and uses only the bundled catalog.

## Feed format

The feed is a single JSON document served over **HTTPS**:

```json
{
  "schema": 1,
  "version": "2026.10.20",
  "vendor": "Example Corp",
  "environments": {
    "bash": [
      {"command": "apt list --upgradable", "summary": "list pending upgrades"},
      {"command": "systemd-analyze blame", "summary": "slow boot units"}
    ],
    "powershell": [
      {"command": "Get-MpComputerStatus", "summary": "Defender status"}
    ]
  }
}
```

Field rules:

| Field | Rules |
| --- | --- |
| `schema` | must be exactly `1` |
| `version` | optional string, up to 64 characters |
| `vendor` | optional string, up to 120 characters |
| `environments` | non-empty object; keys must be `bash`, `powershell`, `cmd`, `cisco` or `panos` |
| entries | objects with a non-empty single-line `command` (≤ 240 chars) and optional single-line `summary` (≤ 240 chars) |

Limits: 12 environments, 2 000 entries per environment, 10 000 entries in
total, 2 MB per document. Duplicate commands within an environment are
collapsed case-insensitively.

Anything invalid rejects the **whole** document; the previous catalog keeps
working.

## How updates are applied

1. The updater sends `If-None-Match` when the stored catalog was fetched from
   the same URL, so an unchanged feed costs one 304 response.
2. The response is read with a hard size cap, then validated against the schema
   above.
3. The catalog is written to a temporary file in the data directory and moved
   into place atomically (`catalog.json`).
4. The completion engine is rebuilt in place: the downloaded entries are added
   *after* the bundled ones.

Bundled entries always win. A feed can add commands, never replace or relabel a
built-in entry. Downloaded entries appear as **Catalog (downloaded)** — never
as **Verified** — and rank below verified built-ins and your own history. They
are text suggestions like any other: running one still passes through the policy
gate.

A failed update is not fatal: offline, TLS errors, HTTP errors, invalid JSON and
oversized documents all leave the last good catalog untouched and are recorded
in the audit log (`catalog.update_failed`). Successful updates are audited as
`catalog.updated`.

## Configuring

On the **Commands** page:

1. Paste the vendor's `https://` URL into the feed field.
2. Leave **Check at launch** enabled for a quiet background check a few seconds
   after the window opens, or disable it to update only on demand.
3. Press **Check for updates**.

The status line shows the number of known commands per environment and, after a
download, the feed version, entry count and fetch time.

## Hosting a feed

Any HTTPS endpoint works, for example a static file behind your vendor's CDN or
a GitHub release asset:

```text
https://raw.githubusercontent.com/<org>/<repo>/main/catalog.json
```

Serve it as `application/json`, send a stable `ETag` if possible, and keep the
document under the size limit. There is no signature requirement because the
feed can never override trusted entries or execute anything; the operator still
sees the provenance label and the policy decision for every command.
