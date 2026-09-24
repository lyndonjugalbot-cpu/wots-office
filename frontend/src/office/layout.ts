import type { AgentStatus, OfficeAgent } from "../types";

export type Vec3 = [number, number, number];

export interface DeskSpot {
  position: Vec3;
  big: boolean;
}

const COLUMNS = [-4.6, 0, 4.6];
export const BACK_WALL_Z = -6;
export const ROOM_HALF_WIDTH = 8.5;

/** Atlas, the CEO and Hawk (QA) sit in the back row; the makers fill rows of three, in pipeline order. */
export function deskLayout(staff: OfficeAgent[]): { spots: Record<string, DeskSpot>; frontZ: number } {
  const spots: Record<string, DeskSpot> = {};
  const backRow: Record<string, number> = { atlas: 0, ceo: 1, hawk: 2 };
  for (const s of staff) {
    if (s.id in backRow) spots[s.id] = { position: [COLUMNS[backRow[s.id]], 0, -3.3], big: s.kind === "ceo" };
  }
  const workers = staff.filter((s) => !(s.id in backRow));
  workers.forEach((s, i) => {
    spots[s.id] = { position: [COLUMNS[i % 3], 0, 0.6 + Math.floor(i / 3) * 3.4], big: false };
  });
  const rows = Math.max(1, Math.ceil(workers.length / 3));
  return { spots, frontZ: Math.max(6, 0.6 + (rows - 1) * 3.4 + 3) };
}

export const STATUS_COLORS: Record<AgentStatus, string> = {
  idle: "#39d353",
  working: "#4aa3ff",
  waiting: "#ffb627",
  done: "#ffd23f",
  error: "#ff4f4f",
};

export const SCREEN_COLORS: Record<AgentStatus, string> = {
  idle: "#1f4d33",
  working: "#46ff9b",
  waiting: "#ffb627",
  done: "#ffe27a",
  error: "#ff5a5a",
};

/** Hair and skin tones picked from the staff id so each person looks consistent between visits. */
export function looks(id: string) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const skins = ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac", "#d4a373"];
  const hairs = ["#2b1d0e", "#4a2c17", "#8b5a2b", "#d9b56b", "#1a1a1a", "#a0522d", "#6d6d6d"];
  return { skin: skins[h % skins.length], hair: hairs[(h >> 3) % hairs.length], longHair: ((h >> 6) & 1) === 1 };
}
