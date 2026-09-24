import { useCallback, useState } from "react";
import { Scene } from "./office/Scene";
import { AgentPanel } from "./ui/AgentPanel";
import { ApprovalQueue } from "./ui/ApprovalQueue";
import { IntakeBar } from "./ui/IntakeBar";
import { Labels } from "./ui/Labels";
import { LeadDetail } from "./ui/LeadDetail";
import { SidePanel } from "./ui/SidePanel";
import { TopBar } from "./ui/TopBar";
import { useWots } from "./useWots";

function saved<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function remember(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode: preferences just won't persist */
  }
}

export default function App() {
  const { overview, agents, events, connected, refresh } = useWots();
  const [retro, setRetro] = useState(() => saved("wots.retro", false));
  const [autoRotate, setAutoRotate] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [leadId, setLeadId] = useState<number | null>(null);

  const selectAgent = useCallback((id: string | null) => {
    setSelected(id);
    if (id === "ceo") setQueueOpen(true); // the CEO's desk is the approval queue
  }, []);

  const counts = overview?.counts.website ?? {};
  const inFlight = Object.entries(counts)
    .filter(([s]) => !["APPROVED", "DISQUALIFIED", "READY_FOR_APPROVAL", "ESCALATED"].includes(s))
    .reduce((n, [, v]) => n + v, 0);
  const board = {
    title: "PIPELINE",
    lines: [
      `${inFlight} in progress`,
      `${overview?.pending.approvals ?? 0} to approve`,
      `${counts.APPROVED ?? 0} approved`,
      overview?.pending.escalations ? `${overview.pending.escalations} escalated!` : "",
    ].filter(Boolean),
    footer: overview?.dry_run ? "DRY RUN" : "LIVE",
  };
  const agent = agents.find((a) => a.id === selected);
  const closeQueue = useCallback(() => {
    setQueueOpen(false);
    setSelected(null);
  }, []);
  const closeLead = useCallback(() => setLeadId(null), []);

  return (
    <div className="app">
      <Scene agents={agents} board={board} selected={selected} onSelect={selectAgent} retro={retro} autoRotate={autoRotate} />
      <Labels agents={agents} selected={selected} />

      <TopBar
        overview={overview}
        connected={connected}
        onOpenQueue={() => setQueueOpen(true)}
        onRefresh={refresh}
        retro={retro}
        onRetro={(v) => {
          setRetro(v);
          remember("wots.retro", v);
        }}
        autoRotate={autoRotate}
        onAutoRotate={setAutoRotate}
      />

      <SidePanel overview={overview} events={events} agents={agents} onOpenLead={setLeadId} onSelectAgent={selectAgent} />

      {agent && agent.id !== "ceo" && (
        <AgentPanel key={agent.id} agent={agent} events={events} onClose={() => setSelected(null)}
          onOpenLead={setLeadId} onOpenQueue={() => setQueueOpen(true)} />
      )}

      <div className="bottom">
        {!connected && <p className="warning panel">Can't reach the dashboard API. Is `wots run` running?</p>}
        <IntakeBar onImported={refresh} />
      </div>

      {queueOpen && (
        <ApprovalQueue
          onClose={closeQueue}
          onChanged={refresh}
          onOpenLead={setLeadId}
        />
      )}
      {leadId !== null && <LeadDetail id={leadId} onClose={closeLead} />}
    </div>
  );
}
