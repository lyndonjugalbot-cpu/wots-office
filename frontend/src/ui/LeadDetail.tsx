import { useEffect, useState } from "react";
import type { PitchMessage } from "../types";
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
              <PreviewLine item={detail.item} />
              <ContactEditor id={id} item={detail.item} suppressed={detail.suppressed} pitch={detail.pitch} />
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

/** Where Dock published the approved site (Phase 3). */
function PreviewLine({ item }: { item: Detail["item"] }) {
  const p = item.checks?.preview;
  if (!p) return null;
  return (
    <p className="preview-line">
      <b>Preview:</b>{" "}
      {p.removed_at ? <span className="muted">taken down {new Date(p.removed_at + "Z").toLocaleDateString()}</span>
        : p.live ? <a className="linkbtn" href={p.url} target="_blank" rel="noreferrer">{p.url}</a>
        : <span className="muted">dry run: kept locally, not published</span>}
    </p>
  );
}

/** Add the email address a business publishes (e.g. on its Facebook page) so Echo can pitch it,
 * or put the address on the suppression list. */
function ContactEditor({ id, item, suppressed, pitch }: {
  id: string; item: Detail["item"]; suppressed: string | null; pitch: Detail["pitch"];
}) {
  const [email, setEmail] = useState(item.email ?? "");
  const [name, setName] = useState(item.contact_name ?? "");
  const [foundAt, setFoundAt] = useState(item.checks?.email?.found_at ?? "");
  const [message, setMessage] = useState("");
  const save = async () => {
    try {
      await api.contact(id, { email, contact_name: name, found_at: foundAt || undefined });
      setMessage("Saved.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  };
  const suppress = async () => {
    if (!window.confirm(`Never email ${item.email} again?`)) return;
    try {
      await api.suppress(id, "asked not to be contacted");
      setMessage("Suppressed.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  };
  const sent = (m?: PitchMessage) => m && (m.sent_at ? `sent ${new Date(m.sent_at).toLocaleString()}`
    : m.scheduled_for ? `scheduled for ${new Date(m.scheduled_for).toLocaleString()}` : m.status);
  return (
    <>
      <h3>Contact</h3>
      {suppressed && <p className="warning panel">Suppressed: {suppressed}. We won't email this address.</p>}
      {(pitch.initial || pitch.followup) && (
        <p className="muted">Pitch: {sent(pitch.initial)}{pitch.followup ? ` · follow-up: ${sent(pitch.followup)}` : ""}</p>
      )}
      <form className="contact-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email address" aria-label="Email address" />
        <input value={foundAt} onChange={(e) => setFoundAt(e.target.value)} placeholder="Where they publish it (a link), needed for AU leads" aria-label="Where the email is published" />
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Contact name (optional)" aria-label="Contact name" />
        <div className="plan__actions">
          {item.email && !suppressed && <button type="button" className="btn btn--danger" onClick={suppress}>Never email</button>}
          <button className="btn btn--primary">Save contact</button>
        </div>
      </form>
      {message && <p className="muted">{message}</p>}
    </>
  );
}
