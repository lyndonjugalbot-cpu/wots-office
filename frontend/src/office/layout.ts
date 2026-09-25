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
  exitOffset: number; // how far behind a desk the worker's aisle is
}

const EXIT_OFFSET = 1.4;
const BACK_AISLE = -5.0; // between the back row and the wall

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
