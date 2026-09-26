import { useState } from "react";
import { api } from "../api";
import type { Me, Overview } from "../types";

interface Props {
  me: Me;
  office: string;
  onSwitchOffice: (slug: string) => void;
  overview: Overview | null;
  connected: boolean;
  onOpenQueue: () => void;
  onRefresh: () => void;
  autoRotate: boolean;
  onAutoRotate: (v: boolean) => void;
}

export function TopBar(p: Props) {
  const [ticking, setTicking] = useState(false);
  const ov = p.overview;
  const pending = ov ? ov.pending.approvals + ov.pending.pitches + ov.pending.replies + ov.pending.escalations : 0;

  const tick = async () => {
    setTicking(true);
    try {
      await api.tick();
    } finally {
      setTicking(false);
      p.onRefresh();
    }
  };

  return (
    <header className="topbar panel">
      <div className="brand">
        <span className="brand__gem" />
        Wots Office
        <span className={`conn ${p.connected ? "conn--on" : ""}`} title={p.connected ? "Connected" : "Can't reach the dashboard API"} />
        {p.me.offices.length > 1 ? (
          <select className="officepick" value={p.office} aria-label="Office" onChange={(e) => p.onSwitchOffice(e.target.value)}>
            {p.me.offices.map((o) => <option key={o.slug} value={o.slug}>{o.name}</option>)}
          </select>
        ) : (
          <span className="officepick officepick--static">{p.me.offices[0]?.name}</span>
        )}
      </div>

      <div className="hud">
        {ov?.dry_run && <span className="chip chip--awaiting_approval" title="No sends, no deploys, LLM spend capped">DRY RUN</span>}
        {ov && (
          <span className="hud__stat" title="LLM spend today / cap">
            $ {ov.spend_today.toFixed(2)} / {ov.spend_cap.toFixed(2)}
          </span>
        )}
        {ov && ov.credits !== null && (
          <span className={`hud__stat ${ov.credits <= 0 ? "badge-red" : ""}`} title="Credits left">
            {Math.floor(ov.credits)} credits
          </span>
        )}
        <button className={`btn btn--small ${pending ? "btn--primary" : ""}`} onClick={p.onOpenQueue}>
          Approvals {ov ? `(${ov.pending.approvals + ov.pending.pitches + ov.pending.replies})` : ""}
          {ov && ov.pending.escalations > 0 && <span className="badge-red"> +{ov.pending.escalations} escalated</span>}
        </button>
        <button className="btn btn--small" onClick={tick} disabled={ticking} title="Run an Atlas tick now">
          {ticking ? "Ticking…" : "Tick now"}
        </button>
      </div>

      <div className="controls">
        <label className="toggle">
          <input type="checkbox" checked={p.autoRotate} onChange={(e) => p.onAutoRotate(e.target.checked)} /> Spin
        </label>
        <button className="btn btn--small" title={`Signed in as ${p.me.user.email}`}
          onClick={() => api.logout().then(() => window.location.reload())}>
          Sign out
        </button>
      </div>
    </header>
  );
}
