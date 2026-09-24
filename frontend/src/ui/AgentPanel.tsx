import type { OfficeAgent, WotsEvent } from "../types";

const ROLES: Record<string, string> = {
  ceo: "You. Approve builds and pitches; nothing leaves the building without you.",
  atlas: "The orchestrator. Plain code, not an LLM: assigns work within WIP limits, routes fixes, retries and escalates.",
  ledger: "Enriches new leads: local timezone, phone format, tidy contact details. (Verification arrives in Phase 2.)",
  quill: "Writes the site copy in local spelling, and never invents facts.",
  iris: "Makes the brand palette and a text logo. (Real logo and hero design arrive in Phase 3.)",
  pixel: "Web designer. Builds the one-page site from the category template.",
  nova: "Web designer. Builds the one-page site from the category template.",
  hawk: "QA: browser checks at 3 widths, Lighthouse, contact details, placeholders, noindex, and a proofread.",
};

interface Props {
  agent: OfficeAgent;
  events: WotsEvent[];
  onClose: () => void;
  onOpenLead: (id: number) => void;
  onOpenQueue: () => void;
}

export function AgentPanel({ agent, events, onClose, onOpenLead, onOpenQueue }: Props) {
  const mine = events.filter((e) => e.actor === agent.id).slice(-12).reverse();
  return (
    <aside className="agentpanel panel" aria-label={`${agent.name} details`}>
      <button className="close" onClick={onClose} aria-label="Close">×</button>
      <div className="agentpanel__head">
        <span className="swatch" style={{ background: agent.color }} />
        <div>
          <h2>{agent.name}</h2>
          <p className="muted">{agent.title}{agent.style ? ` · ${agent.style}` : ""}</p>
        </div>
      </div>
      <p className={`status status--${agent.status}`}>
        {agent.status.toUpperCase()}
        {agent.bubble && <span> · {agent.bubble}</span>}
      </p>
      <p>{ROLES[agent.id] ?? ""}</p>
      {agent.kind === "ceo" && (
        <button className="btn btn--primary" onClick={onOpenQueue}>Open the approval queue</button>
      )}
      <h3>Recent work</h3>
      {mine.length ? (
        <ul className="tasklist">
          {mine.map((e) => (
            <li key={e.id} className={`task ${e.note?.startsWith("error:") ? "task--failed" : ""}`}>
              <div className="task__head">
                {e.lead_id && <button className="linkbtn" onClick={() => onOpenLead(e.lead_id!)}>{e.business}</button>}
                {e.to && e.from !== e.to && <span className="task__status">{e.to}</span>}
              </div>
              {e.note && <p className="task__report">{e.note}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nothing yet.</p>
      )}
    </aside>
  );
}
