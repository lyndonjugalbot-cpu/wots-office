import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Overview, TradeList } from "../types";

/** Intake: CSV lead imports into the office's website workflow. Ad captures arrive with ad_refresh in Phase 5. */
export function IntakeBar({ overview, onImported }: { overview: Overview | null; onImported: () => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [country, setCountry] = useState("US");
  const [trade, setTrade] = useState("plumber");
  const [source, setSource] = useState("osm");
  const [catalogue, setCatalogue] = useState<TradeList | null>(null);
  const [regions, setRegions] = useState("");
  const [limit, setLimit] = useState(50);

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

  useEffect(() => {
    api.trades().then(setCatalogue).catch(() => setCatalogue(null));
  }, []);
  // Companies House only covers the UK
  useEffect(() => {
    if (country !== "UK" && source === "companies_house") setSource("osm");
  }, [country, source]);

  const findLeads = async () => {
    const areas = regions.split(",").map((r) => r.trim()).filter(Boolean);
    if (!areas.length) {
      setMessage("Name at least one town, e.g. Manchester, Leeds");
      return;
    }
    setBusy(true);
    setMessage("Searching…");
    try {
      const out = await api.research(country, trade, areas, limit, source);
      setMessage(`${out.summary} Ledger verifies them next.`);
      onImported();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const website = overview?.workflows.find((w) => w.key === "website" && w.active);
  const canEdit = overview?.office.roles.some((r) => ["owner", "ceo", "manager"].includes(r)) ?? false;
  if (overview && (!website || !canEdit)) return null;

  return (
    <div className="commandbar panel">
      <span className="commandbar__label">Intake · find leads, or import a CSV</span>
      <div className="commandbar__row intake">
        <input ref={file} type="file" accept=".csv,text/csv" aria-label="Lead CSV file" disabled={busy}
          onChange={(e) => e.target.files?.[0] && handle(() => api.importCsv(e.target.files![0], "website"))} />
        <button className="btn" disabled={busy} onClick={() => handle(api.importSamples)}>Load 4 sample leads</button>
      </div>
      <form className="commandbar__row intake" onSubmit={(e) => { e.preventDefault(); findLeads(); }}>
        <select value={country} onChange={(e) => setCountry(e.target.value)} aria-label="Country" disabled={busy}>
          <option>US</option><option>UK</option><option>AU</option>
        </select>
        <select value={trade} onChange={(e) => setTrade(e.target.value)} aria-label="Trade" disabled={busy}>
          {(catalogue?.trades ?? [{ key: "plumber", label: "Plumbers", uk_registry: true }]).map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        <input value={regions} onChange={(e) => setRegions(e.target.value)} placeholder="Towns, e.g. Manchester, Leeds" aria-label="Towns" disabled={busy} />
        <input type="number" min={1} max={200} value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="How many" disabled={busy} className="intake__count" />
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source" disabled={busy}>
          {(catalogue?.sources ?? []).filter((s) => s.countries.includes(country)).map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
        <button className="btn btn--primary" disabled={busy || !regions.trim()}>Find leads</button>
      </form>
      <p className="muted intake__help">
        Free sources. {source === "osm" ? "Business data © OpenStreetMap contributors." : "UK limited companies from Companies House."}
        {overview && ` Today: ${overview.research[source as "osm" | "companies_house"]?.requests_today ?? 0} of ${overview.research[source as "osm" | "companies_house"]?.max_per_day ?? 0} requests.`}
      </p>
      <p className="muted intake__help">CSV columns: business_name, country (US, UK, AU), plus optional category, description, region, address, phone, email, contact_name.</p>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
