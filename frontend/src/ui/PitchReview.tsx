import { useEffect, useState } from "react";
import { api } from "../api";
import type { LeadDetail, PitchMessage } from "../types";

function useDetail(id: string) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.lead(id).then(setDetail).catch((e: Error) => setError(e.message));
  }, [id]);
  return { detail, error, setError };
}

/** The pitch queue (spec v2 §12): the rendered email and its follow-up, editable; Approve or send back. */
export function PitchReview({ id, onDone, onOpenLead }: { id: string; onDone: () => void; onOpenLead: (id: string) => void }) {
  const { detail, error, setError } = useDetail(id);
  const [edits, setEdits] = useState<Record<string, { subject?: string; body?: string }>>({});
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  if (!detail) return <div className="queue__detail">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>;
  const lead = detail.item;
  const edit = (kind: string, field: "subject" | "body", value: string) =>
    setEdits((e) => ({ ...e, [kind]: { ...e[kind], [field]: value } }));
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const message = (kind: "initial" | "followup", label: string, m?: PitchMessage) =>
    m && (
      <div className="pitch">
        <h3>{label}</h3>
        <input className="pitch__subject" aria-label={`${label} subject`} value={edits[kind]?.subject ?? m.subject}
          onChange={(e) => edit(kind, "subject", e.target.value)} disabled={!detail.can_decide} />
        <textarea className="pitch__body" aria-label={`${label} body`} rows={kind === "initial" ? 16 : 9}
          value={edits[kind]?.body ?? m.body} onChange={(e) => edit(kind, "body", e.target.value)} disabled={!detail.can_decide} />
      </div>
    );

  return (
    <div className="queue__detail">
      <div className="review__head">
        <div>
          <h2>{lead.business_name}</h2>
          <p className="muted">
            To <b>{lead.email}</b> · {lead.country}{lead.contact_name ? ` · ${lead.contact_name}` : ""}
            {lead.preview_url && <> · <a className="linkbtn" href={lead.preview_url} target="_blank" rel="noreferrer">preview</a></>}
            {" · "}<button className="linkbtn" onClick={() => onOpenLead(id)}>timeline</button>
          </p>
        </div>
      </div>
      {detail.suppressed && <p className="warning panel">This address is suppressed ({detail.suppressed}); it won't be sent.</p>}
      {message("initial", "Pitch", detail.pitch.initial)}
      {message("followup", `Follow-up (sent only if there's no reply)`, detail.pitch.followup)}
      <p className="muted">Approving sends the pitch in {lead.business_name}'s local send window (Tue–Thu 9–11am), then the follow-up once, if they don't reply.</p>
      <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes (required to send it back: tell Echo what to change)" />
      {!detail.can_decide && <p className="muted">Only the CEO (or a manager they delegated approvals to) can decide this.</p>}
      <div className="plan__actions" hidden={!detail.can_decide}>
        <button className="btn" disabled={busy || !notes.trim()} onClick={() => run(() => api.rejectPitch(id, notes))}>Send back to redraft</button>
        <button className="btn btn--primary" disabled={busy} onClick={() => run(() => api.approvePitch(id, edits, notes))}>
          {Object.keys(edits).length ? "Approve with my edits" : "Approve"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** A lead that replied: read what they said, then close it. */
export function ReplyReview({ id, onDone, onOpenLead }: { id: string; onDone: () => void; onOpenLead: (id: string) => void }) {
  const { detail, error, setError } = useDetail(id);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  if (!detail) return <div className="queue__detail">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>;
  const lead = detail.item;
  const reply = [...detail.events].reverse().find((e) => e.to === "REPLIED");
  const run = async (won: boolean) => {
    setBusy(true);
    try {
      await api.close(id, won, notes);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="queue__detail">
      <h2>{lead.business_name}</h2>
      <p className="muted">{lead.email} · {lead.phone} · <button className="linkbtn" onClick={() => onOpenLead(id)}>timeline</button></p>
      {reply?.note && <blockquote className="reply">{reply.note}</blockquote>}
      <p className="muted">Reply to them from your own mailbox. When it's settled, close it here.</p>
      <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notes (optional)" />
      <div className="plan__actions" hidden={!detail.can_decide}>
        <button className="btn btn--danger" disabled={busy} onClick={() => run(false)}>Lost</button>
        <button className="btn btn--primary" disabled={busy} onClick={() => run(true)}>Won</button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
