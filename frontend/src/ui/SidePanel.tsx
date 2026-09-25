import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Lead, OfficeAgent, Overview, Team as TeamData, WotsEvent } from "../types";

type Tab = "activity" | "pipeline" | "leads" | "team";

interface Props {
  overview: Overview | null;
  events: WotsEvent[];
  agents: OfficeAgent[];
  onOpenLead: (id: string) => void;
  onSelectAgent: (id: string) => void;
  onTeamChanged: () => void;
}

export function SidePanel({ overview, events, agents, onOpenLead, onSelectAgent, onTeamChanged }: Props) {
  const [tab, setTab] = useState<Tab>("activity");
  const [collapsed, setCollapsed] = useState(() => window.innerWidth < 760);

  return (
    <aside className={`sidepanel panel ${collapsed ? "sidepanel--collapsed" : ""}`}>
      <div className="tabs" role="tablist">
        {(["activity", "pipeline", "leads", "team"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""}
            onClick={() => { setTab(t); setCollapsed(false); }}>
            {t}
          </button>
        ))}
        <button className="tabs__toggle" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand" : "Collapse"}>
          {collapsed ? "▸" : "▾"}
        </button>
      </div>
      {!collapsed && (
        <div className="sidepanel__body">
          {tab === "activity" && <Activity events={events} agents={agents} onOpenLead={onOpenLead} onSelectAgent={onSelectAgent} />}
          {tab === "pipeline" && <Pipeline overview={overview} />}
          {tab === "leads" && <Leads onOpenLead={onOpenLead} />}
          {tab === "team" && <Team onChanged={onTeamChanged} />}
        </div>
      )}
    </aside>
  );
}

function Activity({ events, agents, onOpenLead, onSelectAgent }: {
  events: WotsEvent[]; agents: OfficeAgent[]; onOpenLead: (id: string) => void; onSelectAgent: (id: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  // Events name their actor; desks are keyed by id. "You" is the CEO's desk.
  const byName = Object.fromEntries(agents.map((a) => [a.kind === "ceo" ? "You" : a.name, a]));
  byName.atlas = byName.Atlas;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [events.length]);
  if (!events.length) return <p className="muted">Nothing yet. Import some leads below to get the office working.</p>;
  return (
    <div className="activity">
      {events.map((e) => {
        const change = e.to && e.from !== e.to;
        const error = e.note?.startsWith("error:");
        return (
          <div key={e.id} className={`activity__row ${error ? "activity__row--error" : ""}`}>
            <button className="who" style={{ background: byName[e.actor]?.color ?? "#666" }}
              onClick={() => byName[e.actor] && onSelectAgent(byName[e.actor].id)}>
              {e.actor}
            </button>
            <span>
              {e.item_id && (
                <button className="linkbtn" onClick={() => onOpenLead(e.item_id!)}>{e.business}</button>
              )}
              {change && <> → <b>{e.to}</b></>}
              {e.note && <span className="muted"> {change ? "· " : ""}{e.note.slice(0, 140)}</span>}
            </span>
          </div>
        );
      })}
      <div ref={end} />
    </div>
  );
}

function Pipeline({ overview }: { overview: Overview | null }) {
  if (!overview) return <p className="muted">Loading…</p>;
  return (
    <div className="pipeline">
      {overview.workflows.map((wf) => {
        const counts = overview.counts[wf.key] ?? {};
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        return (
          <div key={wf.key}>
            <h3>{wf.key.replace(/_/g, " ")} ({total}){wf.active ? "" : " · inactive"}</h3>
            <table>
              <tbody>
                {wf.states.filter((s) => counts[s]).map((s) => (
                  <tr key={s}><td>{s.replace(/_/g, " ").toLowerCase()}</td><td className="num">{counts[s]}</td></tr>
                ))}
                {!total && <tr><td className="muted">Nothing yet</td></tr>}
              </tbody>
            </table>
            {wf.waiting_for.length > 0 && (
              <p className="muted">Waits at the {wf.waiting_for.join(", ").replace(/_/g, " ")} step: {wf.missing_reason ?? "not hired yet"}.</p>
            )}
          </div>
        );
      })}
      <h3>Designer load (WIP)</h3>
      <table>
        <tbody>
          {overview.designers.map((d) => (
            <tr key={d.name}>
              <td>{d.name} <span className="muted">{d.style} · {d.mode}</span></td>
              <td className="num">{d.active} / {d.max}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Leads({ onOpenLead }: { onOpenLead: (id: string) => void }) {
  const [leads, setLeads] = useState<Lead[] | null>(null);
  useEffect(() => {
    const load = () => api.leads().then(setLeads).catch(() => setLeads([]));
    load();
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, []);
  if (!leads) return <p className="muted">Loading…</p>;
  if (!leads.length) return <p className="muted">No leads yet.</p>;
  return (
    <ul className="history">
      {leads.map((l) => (
        <li key={l.id}>
          <button className="history__item" onClick={() => onOpenLead(l.id)}>
            <span className={`chip chip--${l.status.toLowerCase()}`}>{l.status.replace(/_/g, " ")}</span>
            <span className="history__goal">{l.business_name}</span>
            <span className="muted">{l.workflow_key === "website" ? "" : `${l.workflow_key.replace(/_/g, " ")} · `}{l.country}{l.assigned_to ? ` · ${l.assigned_to}` : ""}{l.fix_count ? ` · fixes ${l.fix_count}` : ""}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The office's employees, and (for the CEO or owner) hiring from the catalogue. */
function Team({ onChanged }: { onChanged: () => void }) {
  const [team, setTeam] = useState<TeamData | null>(null);
  const [type, setType] = useState("");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.team().then(setTeam).catch((e: Error) => setMessage(e.message));
  useEffect(() => {
    load();
  }, []);

  if (!team) return <p className="muted">{message || "Loading…"}</p>;
  const hireable = team.catalogue.filter((t) => t.status !== "planned");
  const chosen = team.catalogue.find((t) => t.key === type);
  const enumFields = Object.entries(chosen?.config_schema ?? {}).filter(([, f]) => f.type === "enum");

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setMessage("");
    try {
      await fn();
      setMessage(done);
      await load();
      onChanged();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="team">
      {team.members.map((m) => (
        <div key={m.id} className="team__row">
          <span>
            <b>{m.name}</b> {!m.working && <span className="soon" title="Hired; their type arrives in a later phase">later phase</span>}
            <small className="muted">{m.type_name}{typeof m.config.style_profile === "string" ? ` · ${m.config.style_profile}` : ""}</small>
          </span>
          {team.can_manage && (
            <button className="btn btn--small" disabled={busy}
              onClick={() => window.confirm(`Let ${m.name} go?`) && act(() => api.fire(m.id), `${m.name} has left the office.`)}>
              Let go
            </button>
          )}
        </div>
      ))}
      {team.can_manage && (
        <form onSubmit={(e) => {
          e.preventDefault();
          if (type && name.trim()) act(() => api.hire(type, name.trim(), config), `Welcome, ${name.trim()}!`).then(() => setName(""));
        }}>
          <h3>Hire</h3>
          <select value={type} onChange={(e) => { setType(e.target.value); setConfig({}); }} aria-label="Employee type">
            <option value="">Choose a role…</option>
            {hireable.map((t) => <option key={t.key} value={t.key}>{t.name}{t.risk === "high" ? " (needs outreach terms)" : ""}</option>)}
          </select>
          {chosen && <p className="muted">{chosen.description}</p>}
          {enumFields.map(([field, f]) => (
            <select key={field} value={config[field] ?? String(f.default ?? "")} aria-label={field}
              onChange={(e) => setConfig({ ...config, [field]: e.target.value })}>
              {f.values?.map((v) => <option key={v} value={v}>{field.replace(/_/g, " ")}: {v.replace(/_/g, " ")}</option>)}
            </select>
          ))}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" maxLength={40} aria-label="Name" />
          <button className="btn btn--primary" disabled={busy || !type || !name.trim()}>Hire</button>
        </form>
      )}
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
