import type { OfficeAgent, WotsEvent } from "../types";

/** What each kind of desk does, by employee type (any hire of a type does the same job). */
const ROLES: Record<string, string> = {
  ceo: "You. Approve builds and pitches; nothing leaves the building without you.",
  atlas: "The orchestrator. Plain code, not an LLM: runs the workflow, assigns work within WIP limits, routes fixes, retries and escalates.",
  lead_researcher: "Finds leads. For now, passes imported CSV leads on to verification. (Scraping arrives in Phase 2.)",
  data_verifier: "Checks new leads: local timezone, phone format, tidy contact details. (Registry checks arrive in Phase 2.)",
  copywriter: "Writes the site copy in local spelling, and never invents facts.",
  graphic_designer: "Makes the brand palette and a text logo. (Real logo and hero design arrive in Phase 3.)",
  web_developer: "Builds the one-page site in their own style, then fixes what QA or you send back.",
  qa_tester: "QA: browser checks at 3 widths, Lighthouse, contact details, placeholders, noindex, and a proofread.",
  deployment: "Deploys approved sites to a preview URL. (Arrives in Phase 3.)",
  ad_researcher: "Finds businesses running weak ads. (Arrives in Phase 5.)",
  creative_strategist: "Turns an ad capture into a creative brief. (Arrives in Phase 5.)",
  cold_email: "Drafts and sends pitches after your approval. (Needs the outreach terms; arrives in Phase 4.)",
};

interface Props {
  agent: OfficeAgent;
  events: WotsEvent[];
  onClose: () => void;
  onOpenLead: (id: string) => void;
  onOpenQueue: () => void;
}

export function AgentPanel({ agent, events, onClose, onOpenLead, onOpenQueue }: Props) {
  const actor = agent.kind === "ceo" ? "You" : agent.kind === "atlas" ? "atlas" : agent.name;
  const mine = events.filter((e) => e.actor === actor).slice(-12).reverse();
  const role = ROLES[agent.kind === "ceo" || agent.kind === "atlas" ? agent.kind : agent.type ?? ""];
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
      <p>{role ?? ""}</p>
      {!agent.available && <p className="warning panel">Hired, but this role's work arrives in a later phase, so they wait at their desk.</p>}
      {agent.kind === "ceo" && (
        <button className="btn btn--primary" onClick={onOpenQueue}>Open the approval queue</button>
      )}
      <h3>Recent work</h3>
      {mine.length ? (
        <ul className="tasklist">
          {mine.map((e) => (
            <li key={e.id} className={`task ${e.note?.startsWith("error:") ? "task--failed" : ""}`}>
              <div className="task__head">
                {e.item_id && <button className="linkbtn" onClick={() => onOpenLead(e.item_id!)}>{e.business}</button>}
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
