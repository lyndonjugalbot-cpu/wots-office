import { useEffect, useState } from "react";
import { api } from "../api";
import type { LeadDetail as Detail } from "../types";

/** Lead detail: the record, every artifact and the full timeline from `events` (spec §10). */
export function LeadDetail({ id, onClose }: { id: number; onClose: () => void }) {
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
          <strong>{detail?.lead.business_name ?? "Lead"}</strong>
          {detail && <span className={`chip chip--${detail.lead.status.toLowerCase()}`}>{detail.lead.status.replace(/_/g, " ")}</span>}
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal__scroll">
          {error && <p className="error">{error}</p>}
          {detail && (
            <>
              <dl className="facts">
                {(["category", "country", "region", "timezone", "phone", "email", "address", "contact_name", "assigned_to", "fix_count", "source", "disqualify_reason"] as const).map((k) =>
                  detail.lead[k] !== null && detail.lead[k] !== "" ? (
                    <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(detail.lead[k])}</dd></div>
                  ) : null,
                )}
              </dl>
              {detail.artifacts.length > 0 && (
                <>
                  <h3>Artifacts</h3>
                  <ul className="files">
                    {detail.artifacts.map((a, i) => (
                      <li key={i}>
                        <a className="linkbtn" href={api.fileUrl(id, a.path)} target="_blank" rel="noreferrer">
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
