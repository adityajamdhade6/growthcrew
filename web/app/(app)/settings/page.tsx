"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, money, useSession } from "@/lib/session";
import { ThemePicker } from "@/components/demo";
import { Badge, Button, Card, ErrorState, Field, inputClass, Loading, Notice, PageHeader } from "@/components/ui";

type Guide = { sentence_length: string; jargon_level: string; banned_phrases: string[]; rules: string[] };
type Brain = { brain: { voice: { guide: Guide; do_words: string[]; dont_words: string[] } } };
type Budget = { weekly_limit_usd: number; spent_this_week_usd: number };
type Models = { options: { model: string; input_per_mtok: number; output_per_mtok: number }[]; roles: { role: string; model: string; default: string }[] };
type Source = { source: string; rows: number; matched: number; last_upload: string | null };

const SOURCE_NAMES: Record<string, string> = { linkedin: "LinkedIn post analytics", gsc: "Google Search Console", ga4: "Google Analytics 4", email: "Email tool", ads: "Ad platform" };

function useSaver() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState("");
  async function save(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    setSaved("");
    try {
      await action();
      setSaved(message);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, saved, save };
}

function Voice({ workspace }: { workspace: string }) {
  const brain = useApi<Brain>(`/workspaces/${workspace}/brain`);
  const [rules, setRules] = useState<string>();
  const [banned, setBanned] = useState<string>();
  const { busy, error, saved, save } = useSaver();
  if (brain.loading) return <Loading rows={1} />;
  if (brain.error) return <ErrorState error={brain.error} retry={brain.reload} />;
  const guide = brain.data!.brain.voice.guide;
  const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);
  const rulesText = rules ?? guide.rules.join("\n");
  const bannedText = banned ?? guide.banned_phrases.join("\n");
  return (
    <div className="space-y-4">
      <Field label="Rules the writer follows" hint="One per line">
        <textarea className={inputClass} rows={Math.max(4, lines(rulesText).length + 1)} value={rulesText} onChange={(event) => setRules(event.target.value)} />
      </Field>
      <Field label="Banned words and phrases" hint="One per line. A draft using one is blocked.">
        <textarea className={inputClass} rows={Math.max(3, lines(bannedText).length + 1)} value={bannedText} onChange={(event) => setBanned(event.target.value)} />
      </Field>
      <p className="text-sm text-muted">Sentence length: {guide.sentence_length || "not set"} · Jargon: {guide.jargon_level}. Rules are also added automatically when you make the same edit three times.</p>
      {error && <ErrorState error={error} />}
      {saved && <Notice tone="good">{saved}</Notice>}
      <Button variant="primary" busy={busy} disabled={rules === undefined && banned === undefined} onClick={() => save(async () => { await api(`/workspaces/${workspace}/brain/field`, { method: "PUT", body: { path: "voice.guide", value: { ...guide, rules: lines(rulesText), banned_phrases: lines(bannedText) } } }); brain.reload(); }, "Voice rules saved as a new version of the brain.")}>
        Save voice rules
      </Button>
    </div>
  );
}

function BudgetCard({ workspace }: { workspace: string }) {
  const budget = useApi<Budget>(`/workspaces/${workspace}/budget`);
  const [limit, setLimit] = useState<string>();
  const { busy, error, saved, save } = useSaver();
  if (budget.loading) return <Loading rows={1} />;
  if (budget.error) return <ErrorState error={budget.error} retry={budget.reload} />;
  const value = limit ?? String(budget.data!.weekly_limit_usd);
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Spent this week: <strong className="text-ink">{money(budget.data!.spent_this_week_usd)}</strong>. When the limit is reached the team stops and you get an alert.</p>
      <Field label="Weekly limit (USD)">
        <input className={`${inputClass} max-w-40`} type="number" min={0} step={1} inputMode="decimal" value={value} onChange={(event) => setLimit(event.target.value)} />
      </Field>
      {error && <ErrorState error={error} />}
      {saved && <Notice tone="good">{saved}</Notice>}
      <Button variant="primary" busy={busy} disabled={limit === undefined || Number.isNaN(Number(value))} onClick={() => save(async () => { await api(`/workspaces/${workspace}/budget`, { method: "PUT", body: { weekly_limit_usd: Number(value) } }); budget.reload(); }, "Budget updated.")}>
        Save budget
      </Button>
    </div>
  );
}

function ModelsCard() {
  const models = useApi<Models>("/settings/models");
  const [override, setOverride] = useState<Models>();
  const { error, saved, save } = useSaver();
  if (models.loading) return <Loading rows={1} />;
  if (models.error) return <ErrorState error={models.error} retry={models.reload} />;
  const data = override ?? models.data!;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Applies to every workspace. A cheaper model lowers cost per piece; check the evals before relying on it.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {data.roles.map((role) => (
          <Field key={role.role} label={label(role.role)} hint={role.model !== role.default ? "changed" : undefined}>
            <select className={inputClass} value={role.model} onChange={(event) => save(async () => setOverride(await api<Models>("/settings/models", { method: "PUT", body: { role: role.role, model: event.target.value } })), `${label(role.role)} now uses ${event.target.value}.`)}>
              {data.options.map((option) => <option key={option.model} value={option.model}>{option.model} (${option.input_per_mtok} in / ${option.output_per_mtok} out per million tokens)</option>)}
            </select>
          </Field>
        ))}
      </div>
      {error && <ErrorState error={error} />}
      {saved && <Notice tone="good">{saved}</Notice>}
    </div>
  );
}

function Sources({ workspace }: { workspace: string }) {
  const sources = useApi<Source[]>(`/workspaces/${workspace}/sources`);
  const [message, setMessage] = useState<{ tone: "good" | "warn"; text: string }>();
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function upload(source: string, file: File | undefined) {
    if (!file) return;
    setBusy(source);
    setError(null);
    setMessage(undefined);
    try {
      const result = await api<{ rows: number; matched: number; unmatched_refs: string[] }>(`/workspaces/${workspace}/metrics/${source}`, { method: "POST", csv: await file.text() });
      setMessage(result.rows === 0
        ? { tone: "warn", text: "No rows were recognised in that file. Check that it is the CSV export and has a header row." }
        : { tone: result.matched === result.rows ? "good" : "warn", text: `${result.matched} of ${result.rows} rows matched to a piece.${result.unmatched_refs.length ? ` Unmatched: ${result.unmatched_refs.slice(0, 3).join(", ")}.` : ""}` });
      sources.reload();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(null);
    }
  }

  if (sources.loading) return <Loading rows={1} />;
  if (sources.error) return <ErrorState error={sources.error} retry={sources.reload} />;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Or upload a CSV export from any tool.</p>
      {error && <ErrorState error={error} />}
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <ul className="divide-y divide-line">
        {sources.data!.map((source) => (
          <li key={source.source} className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{SOURCE_NAMES[source.source] ?? source.source}</p>
              <p className="text-xs text-muted">
                {source.rows ? `${source.matched} of ${source.rows} rows matched · last upload ${new Date(source.last_upload!).toLocaleDateString()}` : "Nothing uploaded yet"}
              </p>
            </div>
            <label className={`inline-flex min-h-10 cursor-pointer items-center rounded-lg border border-line px-3.5 text-sm font-medium hover:bg-raised ${busy === source.source ? "opacity-50" : ""}`}>
              {busy === source.source ? "Uploading…" : source.rows ? "Upload newer CSV" : "Upload CSV"}
              <input type="file" accept=".csv,text/csv" className="sr-only" disabled={busy !== null} onChange={(event) => { upload(source.source, event.target.files?.[0]); event.target.value = ""; }} />
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

type Connector = { provider: string; connected: boolean; settings: Record<string, string>; connected_by: string; last_sync: { at: string; status: string; rows: number; matched: number; error: string } | null };

const PROVIDERS: Record<string, { name: string; hint: string; key: boolean; fields: [string, string][] }> = {
  google: { name: "Google Search Console and GA4", hint: "Read-only access, granted by signing in with Google.", key: false, fields: [["site_url", "Search Console property (e.g. sc-domain:example.com)"], ["ga4_property", "GA4 property id (digits)"]] },
  brevo: { name: "Brevo (newsletters)", hint: "Sends approved newsletters to your opted-in list and reads their results.", key: true, fields: [["list_id", "Contact list id"], ["sender_name", "Sender name"], ["sender_email", "Sender email"], ["postal_address", "Postal address for the footer"]] },
  hubspot: { name: "HubSpot CRM", hint: "Private app token with read-only contact and deal scopes. Counts leads and pipeline; no names are copied.", key: true, fields: [] },
};

function ConnectorRow({ workspace, item, reload }: { workspace: string; item: Connector; reload: () => void }) {
  const spec = PROVIDERS[item.provider];
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [values, setValues] = useState<Record<string, string>>(item.settings);
  const { busy, error, saved, save } = useSaver();
  if (!spec) return null;
  async function connectGoogle() {
    const result = await api<{ url: string }>(`/workspaces/${workspace}/connectors/google/start`, { method: "POST" });
    window.location.assign(result.url);
  }
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium">{spec.name} <Badge tone={item.connected ? "good" : "neutral"}>{item.connected ? "Connected" : "Not connected"}</Badge></p>
          <p className="text-xs text-muted">
            {item.last_sync ? `Last sync ${new Date(item.last_sync.at).toLocaleString()}: ${item.last_sync.status === "ok" ? `${item.last_sync.matched} of ${item.last_sync.rows} rows matched` : item.last_sync.error}` : spec.hint}
          </p>
        </div>
        <Button onClick={() => setOpen(!open)}>{open ? "Close" : item.connected ? "Change" : "Connect"}</Button>
      </div>
      {open && (
        <div className="mt-3 space-y-3">
          {error && <ErrorState error={error} />}
          {saved && <Notice tone="good">{saved}</Notice>}
          {spec.key && (
            <Field label="API key" hint="Stored encrypted. It is never shown again.">
              <input className={inputClass} type="password" autoComplete="off" value={key} onChange={(event) => setKey(event.target.value)} />
            </Field>
          )}
          {spec.fields.map(([name, title]) => (
            <Field key={name} label={title}>
              <input className={inputClass} value={values[name] ?? ""} onChange={(event) => setValues({ ...values, [name]: event.target.value })} />
            </Field>
          ))}
          <div className="flex flex-wrap gap-2">
            {item.provider === "google" && <Button variant="primary" busy={busy} onClick={() => save(connectGoogle, "")}>Sign in with Google</Button>}
            <Button variant={item.provider === "google" ? "secondary" : "primary"} busy={busy}
              onClick={() => save(async () => { await api(`/workspaces/${workspace}/connectors/${item.provider}`, { method: "PUT", body: { api_key: key || null, settings: values } }); setKey(""); reload(); }, "Saved.")}>
              Save
            </Button>
            {item.connected && <Button variant="danger" busy={busy} onClick={() => save(async () => { await api(`/workspaces/${workspace}/connectors/${item.provider}`, { method: "DELETE" }); reload(); }, "Disconnected.")}>Disconnect</Button>}
          </div>
        </div>
      )}
    </li>
  );
}

function Connectors({ workspace }: { workspace: string }) {
  const list = useApi<Connector[]>(`/workspaces/${workspace}/connectors`);
  const { busy, error, saved, save } = useSaver();
  const [returned] = useState(() => (typeof window === "undefined" ? null : new URLSearchParams(window.location.search)));
  const googleError = returned?.get("error");
  if (list.loading && !list.data) return <Loading rows={1} />;
  if (list.error) return <ErrorState error={list.error} retry={list.reload} />;
  const any = list.data!.some((item) => item.connected);
  return (
    <div className="mb-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">Connected sources sync every day. Exports dropped in the workspace&apos;s imports folder are picked up too.</p>
        {any && <Button busy={busy} onClick={() => save(() => api(`/workspaces/${workspace}/connectors/sync`, { method: "POST" }), "Syncing now. Results appear here in a minute.")}>Sync now</Button>}
      </div>
      {error && <div className="mt-2"><ErrorState error={error} /></div>}
      {saved && <div className="mt-2"><Notice tone="good">{saved}</Notice></div>}
      {googleError && <div className="mt-2"><Notice tone="bad">Google was not connected: {googleError}</Notice></div>}
      {returned?.get("connected") === "google" && <div className="mt-2"><Notice tone="good">Google is connected. Set the Search Console property and GA4 property below.</Notice></div>}
      <ul className="divide-y divide-line">{list.data!.map((item) => <ConnectorRow key={item.provider} workspace={workspace} item={item} reload={list.reload} />)}</ul>
    </div>
  );
}

function McpApproval({ workspace }: { workspace: string }) {
  const [token, setToken] = useState("");
  const { busy, error, save } = useSaver();
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">MCP clients such as Claude Desktop can read this workspace. To let one draft new content, give it a single-use approval, valid for 15 minutes. Drafts still wait for your approval here.</p>
      {error && <ErrorState error={error} />}
      {token && <textarea readOnly className={`${inputClass} font-mono text-xs`} rows={3} value={token} onFocus={(event) => event.target.select()} />}
      <Button busy={busy} onClick={() => save(async () => setToken((await api<{ token: string }>(`/workspaces/${workspace}/mcp/approvals`, { method: "POST", body: { action: "propose_content" } })).token), "")}>Create an approval token</Button>
    </div>
  );
}

function ApprovalChannels({ workspace }: { workspace: string }) {
  const [hook, setHook] = useState("");
  const [signing, setSigning] = useState("");
  const [member, setMember] = useState({ email: "", slack_user_id: "" });
  const { busy, error, saved, save } = useSaver();
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">Approvers can approve or reject from a Slack message or from the weekly email digest. Every decision is recorded with who made it and where.</p>
      {error && <ErrorState error={error} />}
      {saved && <Notice tone="good">{saved}</Notice>}
      <Field label="Slack incoming webhook URL"><input className={inputClass} value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://hooks.slack.com/services/..." /></Field>
      <Field label="Slack signing secret" hint="Stored encrypted."><input className={inputClass} type="password" value={signing} onChange={(e) => setSigning(e.target.value)} /></Field>
      <Button busy={busy} onClick={() => save(() => api(`/workspaces/${workspace}/connectors/slack/secrets`, { method: "PUT", body: { webhook_url: hook, signing_secret: signing } }), "Slack connected.")}>Connect Slack</Button>
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="Team member's email"><input className={inputClass} value={member.email} onChange={(e) => setMember({ ...member, email: e.target.value })} /></Field>
        <Field label="Their Slack member ID"><input className={inputClass} value={member.slack_user_id} onChange={(e) => setMember({ ...member, slack_user_id: e.target.value })} placeholder="U012ABCDEF" /></Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button busy={busy} onClick={() => save(() => api(`/workspaces/${workspace}/members/slack`, { method: "PUT", body: member }), "Linked.")}>Link Slack user</Button>
        <Button busy={busy} onClick={() => save(() => api(`/workspaces/${workspace}/slack/notify`, { method: "POST" }), "Posted to Slack.")}>Post pending drafts to Slack</Button>
        <Button busy={busy} onClick={() => save(() => api(`/workspaces/${workspace}/digest/send`, { method: "POST" }), "Digest sent.")}>Send the email digest now</Button>
      </div>
    </div>
  );
}

export default function Settings() {
  const { workspace, workspaces, user, signOut } = useSession();
  const name = workspaces.find((item) => item.workspace === workspace)?.name ?? workspace;
  return (
    <>
      <PageHeader title="Settings" subtitle={name} />
      <div className="max-w-3xl space-y-4">
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">Brand voice rules</h2>
            <Link href="/onboarding" className="text-sm font-medium text-accent">Review the whole brain</Link>
          </div>
          <Voice key={workspace} workspace={workspace} />
        </Card>
        <Card><h2 className="mb-3 text-base font-semibold">Budget limit</h2><BudgetCard key={workspace} workspace={workspace} /></Card>
        <Card>
          <div id="sources" className="mb-3 scroll-mt-20"><h2 className="text-base font-semibold">Connected data sources</h2></div>
          <Connectors key={`c-${workspace}`} workspace={workspace} />
          <Sources key={workspace} workspace={workspace} />
        </Card>
        <Card><h2 className="mb-3 text-base font-semibold">MCP access</h2><McpApproval key={workspace} workspace={workspace} /></Card>
        <Card>
          <h2 className="mb-3 flex items-center gap-2 text-base font-semibold">Model choices {!user.is_admin && <Badge>Admin only</Badge>}</h2>
          {user.is_admin ? <ModelsCard /> : <p className="text-sm text-muted">Ask an admin to change which model each team member uses.</p>}
        </Card>
        <Card>
          <h2 className="mb-3 text-base font-semibold">Appearance</h2>
          <ThemePicker />
        </Card>
        <Card>
          <h2 className="mb-3 text-base font-semibold">Approve from Slack and email</h2>
          <ApprovalChannels key={workspace} workspace={workspace} />
        </Card>
        <Card>
          <h2 className="mb-2 text-base font-semibold">Account</h2>
          <p className="mb-3 text-sm text-muted">Signed in as {user.email}</p>
          <Button onClick={signOut}>Sign out</Button>
        </Card>
      </div>
    </>
  );
}
