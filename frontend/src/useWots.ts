import { useCallback, useEffect, useRef, useState } from "react";
import { SignedOut, api, setOffice } from "./api";
import type { Me, OfficeAgent, Overview, WotsEvent } from "./types";

const POLL_MS = 2000;

/** Who's signed in, and which of their offices is on screen. */
export function useSession() {
  const [me, setMe] = useState<Me | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [office, pick] = useState<string | null>(null);

  useEffect(() => {
    api.me()
      .then((m) => {
        setMe(m);
        let saved: string | null = null;
        try {
          saved = localStorage.getItem("wots.office");
        } catch {
          /* private mode */
        }
        const slug = m.offices.find((o) => o.slug === saved)?.slug ?? m.offices[0]?.slug ?? null;
        setOffice(slug);
        pick(slug);
      })
      .catch((e) => setSignedOut(e instanceof SignedOut));
  }, []);

  const switchOffice = useCallback((slug: string) => {
    setOffice(slug);
    pick(slug);
    try {
      localStorage.setItem("wots.office", slug);
    } catch {
      /* private mode: the choice just won't persist */
    }
  }, []);

  return { me, signedOut, office, switchOffice };
}

/** Polls the dashboard API for one office. Atlas may run in another process, so the database is the source of truth. */
export function useWots(office: string | null) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [agents, setAgents] = useState<OfficeAgent[]>([]);
  const [events, setEvents] = useState<WotsEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [signedOut, setSignedOut] = useState(false);
  const lastId = useRef(0);

  const refresh = useCallback(async () => {
    if (!office) return;
    try {
      const [ov, room, fresh] = await Promise.all([api.overview(), api.office(), api.events(lastId.current)]);
      if (ov.office.slug !== office) return; // a reply for the office we just switched away from
      setOverview(ov);
      setAgents(room.agents);
      if (fresh.length) {
        lastId.current = fresh[fresh.length - 1].id;
        setEvents((prev) => [...prev, ...fresh].slice(-300));
      }
      setConnected(true);
    } catch (e) {
      setConnected(false);
      if (e instanceof SignedOut) setSignedOut(true);
    }
  }, [office]);

  useEffect(() => {
    // A new office: start its timeline from scratch
    lastId.current = 0;
    setEvents([]);
    setOverview(null);
    setAgents([]);
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { overview, agents, events, connected, signedOut, refresh };
}
