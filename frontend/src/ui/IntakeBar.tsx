import { useRef, useState } from "react";
import { api } from "../api";

/** Intake (spec §10): CSV lead imports. Ad captures arrive with the ad_refresh scope in Phase 5. */
export function IntakeBar({ onImported }: { onImported: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const handle = async (fn: () => Promise<{ created: number; skipped: string[] }>) => {
    setBusy(true);
    setMessage("");
    try {
      const out = await fn();
      setMessage(`Imported ${out.created} lead${out.created === 1 ? "" : "s"}` +
        (out.skipped.length ? `; skipped ${out.skipped.length}: ${out.skipped.join("; ")}` : "") +
        ". Atlas picks them up on the next tick.");
      onImported();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  };

  return (
    <div className="commandbar panel">
      <span className="commandbar__label">Intake · website leads</span>
      <div className="commandbar__row intake">
        <input ref={file} type="file" accept=".csv,text/csv" aria-label="Lead CSV file" disabled={busy}
          onChange={(e) => e.target.files?.[0] && handle(() => api.importCsv(e.target.files![0], "website"))} />
        <button className="btn" disabled={busy} onClick={() => handle(api.importSamples)}>Load 4 sample leads</button>
      </div>
      <p className="muted intake__help">CSV columns: business_name, country (US, UK, AU), plus optional category, description, region, address, phone, email, contact_name.</p>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
