# MCP and live data

## GrowthCrew as an MCP server

Ask Claude Desktop (or any MCP client) about your marketing, from live GrowthCrew data.

```json
{
  "mcpServers": {
    "growthcrew": {
      "command": "uv",
      "args": ["--directory", "/path/to/growthcrew", "run", "growthcrew", "mcp", "serve",
               "--user", "you@example.com"]
    }
  }
}
```

The server acts as that GrowthCrew user and sees only their workspaces. Tools:

| Tool | Changes anything? |
|---|---|
| `list_workspaces`, `what_did_we_learn`, `get_strategy`, `list_drafts`, `get_experiment_results`, `get_playbook`, `get_signals` | No (read-only) |
| `propose_content` | Writes new drafts, which wait for approval. Needs an approval token. |

An approval token is minted by a person (Settings → MCP access, or
`growthcrew mcp approve --user you@example.com --workspace acme`). It is signed, names one
workspace and one action, expires in 15 minutes and works once. Nothing over MCP can approve,
schedule or publish.

## Connectors

| Source | How | Access |
|---|---|---|
| Search Console, GA4 | Sign in with Google (OAuth) | `webmasters.readonly`, `analytics.readonly`; a wider grant is refused |
| Brevo | API key | Sends only approved newsletters, to a consented list, through `workflow.publish`, with an unsubscribe link and postal address. Reads campaign results. |
| HubSpot | Private app token | Contacts and deals, read-only; counts only, no personal data stored |
| Any MCP server | Its Streamable HTTP URL, tool name and field mapping | Public URLs only unless `GROWTHCREW_ALLOW_LOCAL_MCP=1`; only mapped numeric fields are read |
| LinkedIn and anything else | CSV dropped in `workspaces/<brand>/imports/` | Picked up once by the daily sync |

Secrets are encrypted at rest (`GROWTHCREW_ENCRYPTION_KEY`, or derived from
`GROWTHCREW_SECRET`; rotating the secret means reconnecting). Google needs
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GROWTHCREW_PUBLIC_URL` on the server.

## Scheduling

`growthcrew scheduler --loop --analyse` syncs each workspace daily and writes weekly
learnings on Mondays; without `--loop` it runs once, for cron. Contract tests replay recorded
responses in `tests/fixtures/connectors/`; CI never calls a live API.
