import { useCallback, useState } from "react";
import { Scene } from "./office/Scene";
import { AgentPanel } from "./ui/AgentPanel";
import { ApprovalQueue } from "./ui/ApprovalQueue";
import { IntakeBar } from "./ui/IntakeBar";
import { Labels } from "./ui/Labels";
import { LeadDetail } from "./ui/LeadDetail";
import { SidePanel } from "./ui/SidePanel";
import { TopBar } from "./ui/TopBar";
import { useSession, useWots } from "./useWots";

export default function App() {
  const session = useSession();
  if (session.signedOut) return <SignIn />;
  if (!session.me) return <div className="app" />;
  if (!session.office) return <SignIn message="Your account isn't a member of any office yet." />;
  return <Office session={session} />;
}

function SignIn({ message }: { message?: string }) {
  return (
    <div className="signin">
      <div className="panel signin__box">
        <h1><span className="brand__gem" /> WOTS OFFICE</h1>
        <p>{message ?? "Sign in with the link printed in the terminal by `bin/wots run`."}</p>
        <p className="muted">Lost it? Run <code>bin/wots login-link</code> for a new one (links last 10 minutes).</p>
      </div>
    </div>
  );
}

function Office({ session }: { session: ReturnType<typeof useSession> }) {
  const { overview, agents, events, connected, signedOut, refresh } = useWots(session.office);
  const [autoRotate, setAutoRotate] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [leadId, setLeadId] = useState<string | null>(null);

  const selectAgent = useCallback((id: string | null) => {
    setSelected(id);
    if (id === "ceo") setQueueOpen(true); // the CEO's desk is the approval queue
  }, []);

  const all: Record<string, number> = {};
  for (const perFlow of Object.values(overview?.counts ?? {})) {
    for (const [s, n] of Object.entries(perFlow)) all[s] = (all[s] ?? 0) + n;
  }
  const inFlight = Object.entries(all)
    .filter(([s]) => !["APPROVED", "DISQUALIFIED", "READY_FOR_APPROVAL", "ESCALATED", "WON", "LOST"].includes(s))
    .reduce((n, [, v]) => n + v, 0);
  const board = {
    title: "PIPELINE",
    lines: [
      `${inFlight} in progress`,
      `${overview?.pending.approvals ?? 0} to approve`,
      `${all.APPROVED ?? 0} approved`,
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
  if (signedOut) return <SignIn message="Your session has expired." />;

  return (
    <div className="app">
      <Scene agents={agents} board={board} selected={selected} onSelect={selectAgent} autoRotate={autoRotate} />
      <Labels agents={agents} selected={selected} />

      <TopBar
        me={session.me!}
        office={session.office!}
        onSwitchOffice={(slug) => {
          setSelected(null);
          setLeadId(null);
          setQueueOpen(false);
          session.switchOffice(slug);
        }}
        overview={overview}
        connected={connected}
        onOpenQueue={() => setQueueOpen(true)}
        onRefresh={refresh}
        autoRotate={autoRotate}
        onAutoRotate={setAutoRotate}
      />

      <SidePanel overview={overview} events={events} agents={agents} onOpenLead={setLeadId} onSelectAgent={selectAgent}
        onTeamChanged={refresh} />

      {agent && agent.id !== "ceo" && (
        <AgentPanel key={agent.id} agent={agent} events={events} onClose={() => setSelected(null)}
          onOpenLead={setLeadId} onOpenQueue={() => setQueueOpen(true)} />
      )}

      <div className="bottom">
        {!connected && <p className="warning panel">Can't reach the dashboard API. Is `wots run` running?</p>}
        <IntakeBar overview={overview} onImported={refresh} />
      </div>

      {queueOpen && (
        <ApprovalQueue
          onClose={closeQueue}
          onChanged={refresh}
          onOpenLead={setLeadId}
        />
      )}
      {leadId !== null && <LeadDetail key={leadId} id={leadId} onClose={closeLead} />}
    </div>
  );
}
