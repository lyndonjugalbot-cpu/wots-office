import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { OfficeAgent, Overview, WotsEvent } from "./types";

const POLL_MS = 2000;

/** Polls the dashboard API. Atlas may run in another process, so the database is the source of truth. */
export function useWots() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [agents, setAgents] = useState<OfficeAgent[]>([]);
  const [events, setEvents] = useState<WotsEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const lastId = useRef(0);

  const refresh = useCallback(async () => {
    try {
      const [ov, office, fresh] = await Promise.all([api.overview(), api.office(), api.events(lastId.current)]);
      setOverview(ov);
      setAgents(office.agents);
      if (fresh.length) {
        lastId.current = fresh[fresh.length - 1].id;
        setEvents((prev) => [...prev, ...fresh].slice(-300));
      }
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { overview, agents, events, connected, refresh };
}
