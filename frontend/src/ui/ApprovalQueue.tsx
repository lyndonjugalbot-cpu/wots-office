import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Lead, LeadDetail } from "../types";

import { PitchReview, ReplyReview } from "./PitchReview";

type Tab = "approvals" | "pitches" | "replies" | "escalations";
const STATUS: Record<Tab, string> = { approvals: "READY_FOR_APPROVAL", pitches: "PITCH_DRAFTED", replies: "REPLIED", escalations: "ESCALATED" };
const LABELS: Record<Tab, string> = { approvals: "Sites", pitches: "Pitches", replies: "Replies", escalations: "Escalated" };
const EMPTY: Record<Tab, string> = { approvals: "No sites waiting for approval.", pitches: "No pitches to approve.",
  replies: "No replies waiting.", escalations: "No escalations." };
const WIDTHS = [375, 768, 1440] as const;

export function ApprovalQueue({ onClose, onChanged, onOpenLead }: {
  onClose: () => void; onChanged: () => void; onOpenLead: (id: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("approvals");
  const [items, setItems] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    const leads = await api.leads(STATUS[tab]);
    setItems(leads);
    setSelected((cur) => (cur && leads.some((l) => l.id === cur) ? cur : leads[0]?.id ?? null));
  }, [tab]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 4000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const done = async () => {
    onChanged();
    await load();
  };

  return (
    <div className="modal" role="dialog" aria-label="Approval queue">
      <div className="queue panel">
        <div className="modal__head">
          <strong>CEO desk</strong>
          <div className="tabs tabs--inline" role="tablist">
            {(Object.keys(LABELS) as Tab[]).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{LABELS[t]}</button>
            ))}
          </div>
          <button className="close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="queue__body">
          <ul className="queue__list">
            {items.map((l) => (
              <li key={l.id}>
                <button className={`queue__item ${selected === l.id ? "active" : ""}`} onClick={() => setSelected(l.id)}>
                  <strong>{l.business_name}</strong>
                  <span className="muted">{l.country} · {l.category ?? "business"}{l.assigned_to ? ` · ${l.assigned_to}` : ""}</span>
                </button>
              </li>
            ))}
            {!items.length && <li className="muted queue__empty">{EMPTY[tab]}</li>}
          </ul>
          {selected && tab === "pitches" ? (
            <PitchReview key={selected} id={selected} onDone={done} onOpenLead={onOpenLead} />
          ) : selected && tab === "replies" ? (
            <ReplyReview key={selected} id={selected} onDone={done} onOpenLead={onOpenLead} />
          ) : selected ? (
            <Review key={selected} id={selected} mode={tab} onDone={done} onOpenLead={onOpenLead} />
          ) : (
            <div className="queue__detail muted">All clear.</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Review({ id, mode, onDone, onOpenLead }: { id: string; mode: Tab; onDone: () => void; onOpenLead: (id: string) => void }) {
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [width, setWidth] = useState<(typeof WIDTHS)[number]>(1440);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.lead(id).then(setDetail).catch((e: Error) => setError(e.message));
  }, [id]);

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

  if (!detail) return <div className="queue__detail">{error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>}</div>;
  const { item: lead, qa, copy } = detail;
  const scores = qa?.lighthouse?.scores ?? {};
  const assumptions = copy?.assumptions ?? [];
  const lastEscalation = [...detail.events].reverse().find((e) => e.to === "ESCALATED");

  return (
    <div className="queue__detail">
      <div className="review__head">
        <div>
          <h2>{lead.business_name}</h2>
          <p className="muted">
            {lead.category} · {lead.country}{lead.region ? ` (${lead.region})` : ""} · built by {lead.assigned_to ?? "?"}
            {lead.fix_count ? ` · ${lead.fix_count} fix${lead.fix_count > 1 ? "es" : ""}` : ""}
            {" · "}<button className="linkbtn" onClick={() => onOpenLead(id)}>timeline</button>
          </p>
        </div>
        {qa && <span className={`chip ${qa.passed ? "chip--done" : "chip--failed"}`}>{qa.passed ? "QA PASSED" : "QA FAILED"}</span>}
      </div>

      {mode === "escalations" && lastEscalation && (
        <p className="warning panel">Escalated: {lastEscalation.note}</p>
      )}

      {detail.has_site && (
        <>
          <div className="viewport-switch" role="group" aria-label="Preview width">
            {WIDTHS.map((w) => (
              <button key={w} className={`btn btn--small ${width === w ? "btn--primary" : ""}`} onClick={() => setWidth(w)}>{w}px</button>
            ))}
            <a className="linkbtn" href={api.fileUrl(detail.files_base, "site/index.html")} target="_blank" rel="noreferrer">Open in new tab</a>
          </div>
          <ScaledPreview width={width} title={`${lead.business_name} preview`} src={api.fileUrl(detail.files_base, "site/index.html")} />
        </>
      )}

      {qa && (
        <div className="review__qa">
          {Object.keys(scores).length > 0 && (
            <div className="scores">
              {Object.entries(scores).map(([k, v]) => (
                <span key={k} className={`score ${v >= 90 ? "score--good" : v >= 80 ? "score--ok" : "score--bad"}`}>{k} {v}</span>
              ))}
            </div>
          )}
          {qa.screenshots.length > 0 && (
            <div className="shots">
              {qa.screenshots.map((s) => (
                <a key={s} href={api.fileUrl(detail.files_base, s)} target="_blank" rel="noreferrer" title={`Screenshot at ${s.match(/(\d+)\.png/)?.[1]}px`}>
                  <img src={api.fileUrl(detail.files_base, s)} alt={`Screenshot at ${s.match(/(\d+)\.png/)?.[1]}px`} />
                </a>
              ))}
            </div>
          )}
          {qa.issues.length > 0 ? (
            <ul className="issues">
              {qa.issues.map((i, n) => (
                <li key={n} className={`issue issue--${i.severity}`}><b>{i.severity}</b> {i.description} <span className="muted">({i.where})</span></li>
              ))}
            </ul>
          ) : <p className="muted">QA found no issues.</p>}
        </div>
      )}

      {assumptions.length > 0 && (
        <div className="warning panel">
          <b>The copywriter assumed these:</b> {assumptions.join("; ")}. Check they're right for this business.
        </div>
      )}

      <p className="muted">
        {lead.phone} · {lead.email} · {lead.address}
      </p>

      <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)}
        placeholder={mode === "approvals" ? "Notes (required to reject: tell the designer what to change)" : "Notes for the designer (optional)"} />

      {!detail.can_decide && <p className="muted">Only the CEO (or a manager they delegated approvals to) can decide this.</p>}
      <div className="plan__actions" hidden={!detail.can_decide}>
        {mode === "approvals" ? (
          <>
            <button className="btn btn--danger" disabled={busy} onClick={() => run(() => api.disqualify(id, notes || "ceo_decision"))}>Disqualify</button>
            <button className="btn" disabled={busy || !notes.trim()} onClick={() => run(() => api.reject(id, notes))}>Reject with notes</button>
            <button className="btn btn--primary" disabled={busy} onClick={() => run(() => api.approve(id, notes))}>Approve</button>
          </>
        ) : (
          <>
            <button className="btn btn--danger" disabled={busy} onClick={() => run(() => api.resolve(id, "DISQUALIFIED", notes))}>Drop lead</button>
            {detail.resume_status && (
              <button className="btn" disabled={busy} onClick={() => run(() => api.resolve(id, detail.resume_status!, notes))}>
                Retry at {detail.resume_status}
              </button>
            )}
            {detail.send_back_to && (
              <button className="btn btn--primary" disabled={busy} onClick={() => run(() => api.resolve(id, detail.send_back_to!, notes))}>
                Send back to {lead.assigned_to}
              </button>
            )}
          </>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** Renders the site at a real device width, scaled down to fit the panel (like a browser's device mode). */
function ScaledPreview({ width, src, title }: { width: number; src: string; title: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(800);
  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(([entry]) => setAvailable(entry.contentRect.width));
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  const height = 420;
  const scale = Math.min(1, (available - 8) / width);
  return (
    <div className="preview" ref={box} style={{ height }}>
      <iframe title={title} src={src} sandbox="allow-scripts"
        style={{ width, height: height / scale, transform: `scale(${scale})`, transformOrigin: "top center" }} />
    </div>
  );
}
