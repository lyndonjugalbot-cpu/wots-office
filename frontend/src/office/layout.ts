import type { AgentStatus, OfficeAgent } from "../types";

export type Vec3 = [number, number, number];

export interface DeskSpot {
  position: Vec3;
  big: boolean;
}

const COLUMNS = [-4.6, 0, 4.6];
export const BACK_WALL_Z = -6;
export const ROOM_HALF_WIDTH = 8.5;

/** Atlas, the CEO and QA sit in the back row; the makers fill rows of three, in the order the API lists them. */
export function deskLayout(staff: OfficeAgent[]): { spots: Record<string, DeskSpot>; frontZ: number } {
  const spots: Record<string, DeskSpot> = {};
  const backRow: Record<string, number> = { atlas: 0, ceo: 1, qa: 2 };
  const inBack = (s: OfficeAgent) => s.kind in backRow;
  for (const s of staff) {
    if (inBack(s)) spots[s.id] = { position: [COLUMNS[backRow[s.kind]], 0, -3.3], big: s.kind === "ceo" };
  }
  const workers = staff.filter((s) => !inBack(s));
  workers.forEach((s, i) => {
    spots[s.id] = { position: [COLUMNS[i % 3], 0, 0.6 + Math.floor(i / 3) * 3.4], big: false };
  });
  const rows = Math.max(1, Math.ceil(workers.length / 3));
  return { spots, frontZ: Math.max(6, 0.6 + (rows - 1) * 3.4 + 3) };
}

export const STATUS_COLORS: Record<AgentStatus, string> = {
  idle: "#34c759",
  working: "#0a84ff",
  waiting: "#ff9f0a",
  done: "#ffd60a",
  error: "#ff453a",
};

export const SCREEN_COLORS: Record<AgentStatus, string> = {
  idle: "#3a4658",
  working: "#6f9dff",
  waiting: "#ffb340",
  done: "#8be09a",
  error: "#ff6b61",
};

export type HairStyle = "short" | "long" | "bun" | "buzz" | "curly";

/** Skin, hair and clothes picked from the staff id so each person looks the same on every visit. */
export function looks(id: string) {
  let h = 0;
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const skins = ["#e8bfa0", "#dca888", "#cf9571", "#b97c58", "#9c6343", "#7d4c31", "#5f3a25", "#e2b28f"];
  const hairs = ["#2a1d15", "#3b2a20", "#5e3f29", "#8a5a36", "#b88a4e", "#1c1c1e", "#6e6e73", "#4a2e1d"];
  const styles: HairStyle[] = ["short", "long", "bun", "buzz", "curly"];
  const trousers = ["#2c3440", "#3a3f4b", "#4a4f5c", "#5b5147", "#2f3b52", "#6d6a63"];
  const shoes = ["#1c1c1e", "#f2f2f7", "#5a3825", "#3a3a3c"];
  return {
    skin: skins[h % skins.length],
    hair: hairs[(h >>> 3) % hairs.length], // >>> keeps the hash unsigned (>> could go negative)
    style: styles[(h >>> 6) % styles.length],
    trousers: trousers[(h >>> 9) % trousers.length],
    shoes: shoes[(h >>> 12) % shoes.length],
  };
}

/** Brand colours are bright; clothes look better a touch softer. */
export function soften(hex: string, amount = 0.18) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const mix = (v: number) => Math.round(v + (225 - v) * amount);
  return `#${[mix(r), mix(g), mix(b)].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

// ---------------------------------------------------------------- where idle workers wander

export type V2 = [number, number]; // [x, z] on the floor

export interface WanderSlot {
  key: string;
  pos: V2; // where to stand
  face: V2; // what to look at
  aisle: number; // the clear walkway (z) that leads to it
}

export interface WalkMap {
  corridors: number[]; // clear north-south walkways (x) between the desk columns
  slots: WanderSlot[];
  exitOffset: number; // where the worker's aisle is, relative to their desk row (behind their chair)
}

// Desks face the viewer: the monitor is in front of each person (+z) and their chair behind them,
// so the clear aisle for each row runs behind the chairs
const EXIT_OFFSET = -1.4;
const BACK_AISLE = -5.0; // between the back row's chairs and the wall

/**
 * Walkways that never cross a desk: an aisle just behind every desk row, one behind the back row,
 * and corridors between the columns. Routes go aisle -> corridor -> aisle, so nobody walks
 * through furniture.
 */
export function walkMap(spots: Record<string, DeskSpot>, frontZ: number): WalkMap {
  const rows = [...new Set(Object.values(spots).map((s) => s.position[2]))];
  const aisles = [BACK_AISLE, ...rows.map((z) => z + EXIT_OFFSET)];
  const nearest = (z: number) => aisles.reduce((a, b) => (Math.abs(b - z) < Math.abs(a - z) ? b : a));
  const spot = (key: string, pos: V2, face: V2): WanderSlot => ({ key, pos, face, aisle: nearest(pos[1]) });
  return {
    corridors: [-2.3, 2.3],
    exitOffset: EXIT_OFFSET,
    slots: [
      spot("cooler-1", [6.7, -4.85], [7.4, -5.3]),
      spot("cooler-2", [6.15, -4.3], [6.7, -4.85]), // faces the first person: a water-cooler chat
      spot("whiteboard-1", [0.9, -5.0], [0.9, -6]),
      spot("whiteboard-2", [-0.9, -5.0], [-0.9, -6]),
      spot("bookshelf", [-7.0, -0.9], [-8, -0.9]),
      spot("window", [-7.2, 1.9], [-8.5, 1.5]),
      spot("plant", [-6.5, -5.0], [-7.4, -5.4]),
      spot("front-plant", [6.8, frontZ - 1.5], [7.6, frontZ - 0.8]),
    ],
  };
}
