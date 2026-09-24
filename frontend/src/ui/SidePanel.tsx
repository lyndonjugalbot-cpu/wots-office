import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Lead, OfficeAgent, Overview, WotsEvent } from "../types";

type Tab = "activity" | "pipeline" | "leads";

const WEBSITE_FLOW = ["NEW", "ENRICHED", "COPY_READY", "ASSETS_READY", "ASSIGNED", "BUILDING", "IN_QA", "NEEDS_FIX",
  "READY_FOR_APPROVAL", "APPROVED", "ESCALATED", "DISQUALIFIED"];

interface Props {
  overview: Overview | null;
  events: WotsEvent[];
  agents: OfficeAgent[];
  onOpenLead: (id: number) => void;
  onSelectAgent: (id: string) => void;
}

export function SidePanel({ overview, events, agents, onOpenLead, onSelectAgent }: Props) {
  const [tab, setTab] = useState<Tab>("activity");
  const [collapsed, setCollapsed] = useState(() => window.innerWidth < 760);

  return (
    <aside className={`sidepanel panel ${collapsed ? "sidepanel--collapsed" : ""}`}>
      <div className="tabs" role="tablist">
        {(["activity", "pipeline", "leads"] as Tab[]).map((t) => (
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
        </div>
      )}
    </aside>
  );
}

function Activity({ events, agents, onOpenLead, onSelectAgent }: {
  events: WotsEvent[]; agents: OfficeAgent[]; onOpenLead: (id: number) => void; onSelectAgent: (id: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const colors = Object.fromEntries(agents.map((a) => [a.id, a.color]));
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
            <button className="who" style={{ background: colors[e.actor] ?? "#666" }} onClick={() => onSelectAgent(e.actor)}>
              {e.actor}
            </button>
            <span>
              {e.lead_id && (
                <button className="linkbtn" onClick={() => onOpenLead(e.lead_id!)}>{e.business}</button>
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
  const counts = overview.counts.website ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div className="pipeline">
      <h3>Website leads ({total})</h3>
      <table>
        <tbody>
          {WEBSITE_FLOW.filter((s) => counts[s]).map((s) => (
            <tr key={s}><td>{s.replace(/_/g, " ").toLowerCase()}</td><td className="num">{counts[s]}</td></tr>
          ))}
          {!total && <tr><td className="muted">No leads yet</td></tr>}
        </tbody>
      </table>
      <h3>Designer load (WIP, {overview.wip_mode})</h3>
      <table>
        <tbody>
          {overview.designers.map((d) => (
            <tr key={d.name}>
              <td>{d.name} <span className="muted">{d.style}</span></td>
              <td className="num">{d.active} / {d.max}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Leads({ onOpenLead }: { onOpenLead: (id: number) => void }) {
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
            <span className="muted">{l.country}{l.assigned_to ? ` · ${l.assigned_to}` : ""}{l.fix_count ? ` · fixes ${l.fix_count}` : ""}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
