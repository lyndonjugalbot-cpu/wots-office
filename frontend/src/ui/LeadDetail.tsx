import { useEffect, useState } from "react";
import { api } from "../api";
import type { LeadDetail as Detail } from "../types";

/** Work item detail: the lead record, every artifact and the full timeline from `events`. */
export function LeadDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api.lead(id).then(setDetail).catch((e: Error) => setError(e.message));
    load();
    const timer = window.setInterval(load, 4000);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("keydown", onKey);
    };
  }, [id, onClose]);

  return (
    <div className="modal" role="dialog" aria-label="Lead detail" onClick={onClose}>
      <div className="modal__box modal__box--narrow panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">
          <strong>{detail?.item.business_name ?? "Lead"}</strong>
          {detail && <span className={`chip chip--${detail.item.status.toLowerCase()}`}>{detail.item.status.replace(/_/g, " ")}</span>}
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal__scroll">
          {error && <p className="error">{error}</p>}
          {detail && (
            <>
              <dl className="facts">
                {(["workflow_key", "category", "country", "region", "postcode", "timezone", "phone", "email", "address", "contact_name", "entity_type", "registry_id", "assigned_to", "fix_count", "source", "disqualify_reason"] as const).map((k) =>
                  detail.item[k] !== null && detail.item[k] !== "" ? (
                    <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(detail.item[k])}</dd></div>
                  ) : null,
                )}
              </dl>
              <Verification item={detail.item} />
              {detail.artifacts.length > 0 && (
                <>
                  <h3>Artifacts</h3>
                  <ul className="files">
                    {detail.artifacts.map((a, i) => (
                      <li key={i}>
                        <a className="linkbtn" href={api.fileUrl(detail.files_base, a.path)} target="_blank" rel="noreferrer">
                          {a.kind} v{a.version}
                        </a>{" "}
                        <span className="muted">by {a.by} · {a.path}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <h3>Timeline</h3>
              <ol className="timeline">
                {detail.events.map((e) => (
                  <li key={e.id} className={e.note?.startsWith("error:") ? "timeline--error" : ""}>
                    <span className="muted">{new Date(e.ts).toLocaleString()}</span>{" "}
                    <b>{e.actor}</b>{e.to && e.from !== e.to ? <> · {e.from ?? "∅"} → <b>{e.to}</b></> : null}
                    {e.note && <div className="timeline__note">{e.note}</div>}
                  </li>
                ))}
              </ol>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** What the Data Verifier checked: duplicates, websites and the business registry. */
function Verification({ item }: { item: Detail["item"] }) {
  const c = item.checks ?? {};
  if (!c.checked_at) return null;
  const socials = Object.entries(item.social_links ?? {});
  return (
    <>
      <h3>Verification</h3>
      {item.source === "osm" && <p className="muted">Found on OpenStreetMap (© OpenStreetMap contributors, ODbL).</p>}
      <ul className="checks">
        <li>Duplicates: {c.dedupe?.length ? c.dedupe.map((d) => `${d.business_name} (${d.match})`).join(", ") : "none"}</li>
        {c.website && (
          <li>
            Website: {c.website.listed ?? "none listed"}
            {c.website.domains.length > 0 && (
              <span className="muted"> · tried {c.website.domains.map((d) => `${d.domain} (${d.status}${d.evidence ? `: ${d.evidence}` : ""})`).join(", ")}</span>
            )}
          </li>
        )}
        {socials.length > 0 && <li>Social: {socials.map(([k, v]) => <a key={k} className="linkbtn" href={v} target="_blank" rel="noreferrer">{k} </a>)}</li>}
        {c.registry && (
          <li>
            {c.registry.source === "companies_house" ? "Companies House" : "ABN Lookup"}:{" "}
            {c.registry.match ? Object.values(c.registry.match).filter(Boolean).join(" · ") : "no match"}
          </li>
        )}
      </ul>
    </>
  );
}
