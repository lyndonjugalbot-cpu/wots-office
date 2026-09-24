import { useState } from "react";
import { api } from "../api";
import type { Overview } from "../types";

interface Props {
  overview: Overview | null;
  connected: boolean;
  onOpenQueue: () => void;
  onRefresh: () => void;
  retro: boolean;
  onRetro: (v: boolean) => void;
  autoRotate: boolean;
  onAutoRotate: (v: boolean) => void;
}

export function TopBar(p: Props) {
  const [ticking, setTicking] = useState(false);
  const ov = p.overview;
  const pending = ov ? ov.pending.approvals + ov.pending.escalations : 0;

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
        WOTS OFFICE
        <span className={`conn ${p.connected ? "conn--on" : ""}`} title={p.connected ? "Connected" : "Can't reach the dashboard API"} />
      </div>

      <div className="hud">
        {ov?.dry_run && <span className="chip chip--awaiting_approval" title="No sends, no deploys, LLM spend capped">DRY RUN</span>}
        {ov && (
          <span className="hud__stat" title="LLM spend today / cap">
            $ {ov.spend_today.toFixed(2)} / {ov.spend_cap.toFixed(2)}
          </span>
        )}
        <button className={`btn btn--small ${pending ? "btn--primary" : ""}`} onClick={p.onOpenQueue}>
          Approvals {ov ? `(${ov.pending.approvals})` : ""}
          {ov && ov.pending.escalations > 0 && <span className="badge-red"> +{ov.pending.escalations} escalated</span>}
        </button>
        <button className="btn btn--small" onClick={tick} disabled={ticking} title="Run an Atlas tick now">
          {ticking ? "Ticking…" : "Tick now"}
        </button>
      </div>

      <div className="controls">
        <label className="toggle">
          <input type="checkbox" checked={p.retro} onChange={(e) => p.onRetro(e.target.checked)} /> Pixels
        </label>
        <label className="toggle">
          <input type="checkbox" checked={p.autoRotate} onChange={(e) => p.onAutoRotate(e.target.checked)} /> Spin
        </label>
      </div>
    </header>
  );
}
