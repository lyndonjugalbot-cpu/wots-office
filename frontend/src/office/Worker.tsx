import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useRef, useState } from "react";
import type { Group, Mesh } from "three";
import type { OfficeAgent } from "../types";
import { moveLabel } from "./labels";
import { STATUS_COLORS, looks, type V2, type Vec3, type WalkMap, type WanderSlot } from "./layout";

interface Props {
  member: OfficeAgent;
  home: Vec3; // the desk's world position; everything below is relative to it
  map: WalkMap;
  selected: boolean;
  onSelect: (id: string) => void;
}

type Mode = "seated" | "standing" | "walking" | "dwelling" | "returning" | "sitting";

interface Sim {
  mode: Mode;
  idle: number; // seconds with nothing to do
  wait: number; // how long to sit idle before wandering off
  outbound: V2[]; // route to the current spot (local coords)
  path: V2[];
  seg: number;
  pos: V2;
  yaw: number;
  stand: number; // 0 = seated, 1 = standing
  dwell: number;
  slot: WanderSlot | null;
  moving: boolean;
}

const SEAT: V2 = [0, 0.05];
const STAND_UP: V2 = [0.75, 0.35]; // beside the chair, clear of the desk
const SPEED = 1.5; // world units per second
const STAND_HEIGHT = 0.4;
const HALF_PI = Math.PI / 2;
// Which wander spots are taken, so two workers never stand in the same place
const taken = new Map<string, string>();

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const angleTo = (from: number, to: number) => from + ((((to - from + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) - Math.PI);
// The model faces -z at yaw 0; this yaw turns it to face direction (dx, dz)
const yawFor = (dx: number, dz: number) => Math.atan2(-dx, -dz);

/** A low-poly office worker. Sits and types when busy; when idle for a while, wanders the office. */
export function Worker({ member, home, map, selected, onSelect }: Props) {
  const walker = useRef<Group>(null);
  const body = useRef<Group>(null);
  const head = useRef<Group>(null);
  const armL = useRef<Group>(null);
  const armR = useRef<Group>(null);
  const hipL = useRef<Group>(null);
  const hipR = useRef<Group>(null);
  const kneeL = useRef<Group>(null);
  const kneeR = useRef<Group>(null);
  const gem = useRef<Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const { skin, hair, longHair } = looks(member.id);
  const isCeo = member.kind === "ceo";
  const status = useRef(member.status);
  status.current = member.status;
  const sim = useRef<Sim>({
    mode: "seated", idle: 0, wait: rand(6, 16), outbound: [], path: [], seg: 0, pos: [...SEAT], yaw: 0,
    stand: 0, dwell: 0, slot: null, moving: false,
  });

  const toLocal = (p: V2): V2 => [p[0] - home[0], p[1] - home[2]];

  /** Pick a free spot and plan a route along the walkways: aisle behind the desk -> corridor -> aisle -> spot. */
  const planWander = (s: Sim) => {
    const free = map.slots.filter((slot) => !taken.has(slot.key));
    if (!free.length) return false;
    const slot = free[Math.floor(Math.random() * free.length)];
    const start: V2 = [home[0] + STAND_UP[0], home[2] + STAND_UP[1]];
    const exitZ = home[2] + map.exitOffset;
    const corridor = map.corridors.reduce((a, b) =>
      Math.abs(start[0] - b) + Math.abs(slot.pos[0] - b) < Math.abs(start[0] - a) + Math.abs(slot.pos[0] - a) ? b : a);
    const world: V2[] = [[start[0], exitZ], [corridor, exitZ], [corridor, slot.aisle], [slot.pos[0], slot.aisle], slot.pos];
    s.outbound = world.map(toLocal).filter((p, i, all) => i === 0 || Math.hypot(p[0] - all[i - 1][0], p[1] - all[i - 1][1]) > 0.01);
    taken.set(slot.key, member.id);
    s.slot = slot;
    s.mode = "standing";
    return true;
  };

  const headHome = (s: Sim, visited: V2[]) => {
    if (s.slot && taken.get(s.slot.key) === member.id) taken.delete(s.slot.key);
    s.slot = null;
    s.path = [...visited].reverse().concat([STAND_UP]);
    s.seg = 0;
    s.mode = "returning";
  };

  useFrame(({ clock }, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const s = sim.current;
    const busy = status.current !== "idle";
    const t = clock.elapsedTime + member.id.length;
    s.moving = false;

    switch (s.mode) {
      case "seated":
        if (busy) s.idle = 0;
        else if ((s.idle += dt) > s.wait && !planWander(s)) s.idle = 0;
        break;
      case "standing":
        s.stand = Math.min(1, s.stand + dt * 2);
        s.pos = [SEAT[0] + (STAND_UP[0] - SEAT[0]) * s.stand, SEAT[1] + (STAND_UP[1] - SEAT[1]) * s.stand];
        if (busy) {
          headHome(s, []);
          s.mode = "sitting";
        } else if (s.stand >= 1) {
          s.path = s.outbound;
          s.seg = 0;
          s.mode = "walking";
        }
        break;
      case "walking":
      case "returning": {
        if (s.mode === "walking" && busy) {
          headHome(s, s.outbound.slice(0, s.seg)); // work arrived: turn round
          break;
        }
        const target = s.path[s.seg];
        if (!target) {
          if (s.mode === "walking") {
            s.mode = "dwelling";
            s.dwell = rand(6, 14);
          } else s.mode = "sitting";
          break;
        }
        const dx = target[0] - s.pos[0];
        const dz = target[1] - s.pos[1];
        const dist = Math.hypot(dx, dz);
        const stepLen = SPEED * dt;
        if (dist <= stepLen) {
          s.pos = [target[0], target[1]];
          s.seg++;
        } else {
          s.pos = [s.pos[0] + (dx / dist) * stepLen, s.pos[1] + (dz / dist) * stepLen];
          s.moving = true;
        }
        if (dist > 0.001) s.yaw += (angleTo(s.yaw, yawFor(dx, dz)) - s.yaw) * Math.min(1, dt * 10);
        break;
      }
      case "dwelling": {
        if (s.slot) {
          const face = toLocal(s.slot.face);
          s.yaw += (angleTo(s.yaw, yawFor(face[0] - s.pos[0], face[1] - s.pos[1])) - s.yaw) * Math.min(1, dt * 5);
        }
        if (busy || (s.dwell -= dt) <= 0) headHome(s, s.outbound.slice(0, -1));
        break;
      }
      case "sitting":
        s.yaw += (angleTo(s.yaw, 0) - s.yaw) * Math.min(1, dt * 8);
        s.stand = Math.max(0, s.stand - dt * 2);
        s.pos = [SEAT[0] + (STAND_UP[0] - SEAT[0]) * s.stand, SEAT[1] + (STAND_UP[1] - SEAT[1]) * s.stand];
        if (s.stand <= 0) {
          s.mode = "seated";
          s.idle = 0;
          s.wait = rand(12, 30); // sit a while before the next wander
        }
        break;
    }

    // ---- pose
    if (walker.current) {
      walker.current.position.set(s.pos[0], 0, s.pos[1]);
      walker.current.rotation.y = s.yaw;
    }
    const seatedAmount = 1 - s.stand;
    const swing = s.moving ? Math.sin(t * 9) * 0.55 : 0;
    if (hipL.current) hipL.current.rotation.x = HALF_PI * seatedAmount + swing;
    if (hipR.current) hipR.current.rotation.x = HALF_PI * seatedAmount - swing;
    if (kneeL.current) kneeL.current.rotation.x = -HALF_PI * seatedAmount - (s.moving ? Math.max(0, -swing) * 0.6 : 0);
    if (kneeR.current) kneeR.current.rotation.x = -HALF_PI * seatedAmount - (s.moving ? Math.max(0, swing) * 0.6 : 0);

    const st = status.current;
    const atDesk = s.mode === "seated";
    if (body.current) {
      const bob = s.moving ? Math.abs(Math.sin(t * 9)) * 0.04 : 0;
      const celebrate = atDesk && st === "done" ? Math.abs(Math.sin(t * 5)) * 0.08 : 0;
      body.current.position.y = STAND_HEIGHT * s.stand + bob + celebrate + (atDesk ? Math.sin(t * 1.4) * 0.01 : 0);
    }
    if (armL.current && armR.current) {
      if (atDesk) {
        // Typing when working, arms up when done
        const typing = st === "working" ? Math.sin(t * 18) * 0.18 : 0;
        const cheer = st === "done" ? 1.6 : 0;
        armL.current.rotation.x = 1.2 + typing + cheer + (cheer ? Math.sin(t * 6) * 0.2 : 0);
        armR.current.rotation.x = 1.2 - typing + cheer - (cheer ? Math.sin(t * 6) * 0.2 : 0);
      } else {
        const hang = 1.2 * seatedAmount;
        const dwelling = s.mode === "dwelling";
        armL.current.rotation.x = hang - swing * 0.8 + (dwelling ? 0.35 : 0);
        armR.current.rotation.x = hang + swing * 0.8 + (dwelling ? Math.max(0, Math.sin(t * 0.8)) * 1.3 : 0); // a sip
      }
    }
    if (head.current) {
      head.current.rotation.y = atDesk
        ? st === "waiting" ? Math.sin(t * 1.5) * 0.5 : st === "working" ? Math.sin(t * 0.7) * 0.08 : 0
        : s.mode === "dwelling" ? Math.sin(t * 0.6) * 0.4 : 0;
      head.current.rotation.x = atDesk ? (st === "error" ? -0.45 : st === "working" ? -0.05 : 0) : 0;
    }
    if (gem.current) {
      gem.current.rotation.y = t * 1.6;
      gem.current.position.y = 2.25 + STAND_HEIGHT * s.stand + Math.sin(t * 2) * 0.05;
    }

    // Name tag and speech bubble follow the person around the office
    const wx = home[0] + s.pos[0];
    const wz = home[2] + s.pos[1];
    moveLabel(`${member.id}:tag`, wx, 0.05, wz + 0.95);
    moveLabel(`${member.id}:bubble`, wx, 2.75 + STAND_HEIGHT * s.stand, wz);
  });

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(member.id);
  };
  const chairColor = isCeo ? "#7a1f1f" : "#3b3f4a";
  const leg = (x: number, hip: typeof hipL, knee: typeof kneeL) => (
    <group ref={hip} position={[x, 0.62, 0]} rotation={[HALF_PI, 0, 0]}>
      <mesh position={[0, -0.25, 0]} castShadow>
        <boxGeometry args={[0.2, 0.5, 0.2]} />
        <meshStandardMaterial color="#2f3b59" flatShading />
      </mesh>
      <group ref={knee} position={[0, -0.5, 0]} rotation={[-HALF_PI, 0, 0]}>
        <mesh position={[0, -0.24, 0]} castShadow>
          <boxGeometry args={[0.18, 0.48, 0.18]} />
          <meshStandardMaterial color="#2f3b59" flatShading />
        </mesh>
        <mesh position={[0, -0.52, -0.06]}>
          <boxGeometry args={[0.2, 0.08, 0.3]} />
          <meshStandardMaterial color="#1b1b1b" flatShading />
        </mesh>
      </group>
    </group>
  );
  const arm = (x: number, ref: typeof armL) => (
    <group ref={ref} position={[x, 1.15, 0.02]} rotation={[1.2, 0, 0]}>
      <mesh position={[0, -0.24, 0]} castShadow>
        <boxGeometry args={[0.13, 0.48, 0.14]} />
        <meshStandardMaterial color={member.color} flatShading />
      </mesh>
      <mesh position={[0, -0.52, 0]}>
        <boxGeometry args={[0.11, 0.1, 0.11]} />
        <meshStandardMaterial color={skin} flatShading />
      </mesh>
    </group>
  );

  return (
    <group
      onClick={click}
      onPointerOver={(e) => {
        e.stopPropagation();
        setHovered(true);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        setHovered(false);
        document.body.style.cursor = "";
      }}
    >
      {/* Office chair stays at the desk */}
      <group position={[0, 0, 0.1]}>
        <mesh position={[0, 0.48, 0]} castShadow>
          <boxGeometry args={[0.62, 0.1, 0.58]} />
          <meshStandardMaterial color={chairColor} flatShading />
        </mesh>
        <mesh position={[0, 0.95, 0.3]} castShadow>
          <boxGeometry args={[0.62, isCeo ? 1.05 : 0.8, 0.1]} />
          <meshStandardMaterial color={chairColor} flatShading />
        </mesh>
        <mesh position={[0, 0.24, 0]}>
          <cylinderGeometry args={[0.04, 0.04, 0.44, 6]} />
          <meshStandardMaterial color="#222" />
        </mesh>
        <mesh position={[0, 0.03, 0]} receiveShadow>
          <cylinderGeometry args={[0.32, 0.32, 0.05, 5]} />
          <meshStandardMaterial color="#222" flatShading />
        </mesh>
      </group>

      {/* The person moves as a whole (walker), with jointed hips, knees and shoulders */}
      <group ref={walker} position={[SEAT[0], 0, SEAT[1]]}>
        <group ref={body}>
          {leg(-0.13, hipL, kneeL)}
          {leg(0.13, hipR, kneeR)}
          <mesh position={[0, 0.93, 0.02]} castShadow>
            <boxGeometry args={[0.5, 0.6, 0.3]} />
            <meshStandardMaterial color={member.color} flatShading />
          </mesh>
          {isCeo && (
            <mesh position={[0, 0.95, -0.14]}>
              <boxGeometry args={[0.08, 0.4, 0.02]} />
              <meshStandardMaterial color="#b3001b" />
            </mesh>
          )}
          {arm(-0.31, armL)}
          {arm(0.31, armR)}
          <group ref={head} position={[0, 1.42, 0.02]}>
            <mesh castShadow>
              <icosahedronGeometry args={[0.22, 0]} />
              <meshStandardMaterial color={skin} flatShading />
            </mesh>
            <mesh position={[0, 0.1, 0.03]} castShadow>
              <boxGeometry args={[0.44, 0.16, 0.42]} />
              <meshStandardMaterial color={hair} flatShading />
            </mesh>
            {longHair && (
              <mesh position={[0, -0.08, 0.16]}>
                <boxGeometry args={[0.42, 0.32, 0.14]} />
                <meshStandardMaterial color={hair} flatShading />
              </mesh>
            )}
            {isCeo && (
              <mesh position={[0, 0.27, 0.02]}>
                <cylinderGeometry args={[0.16, 0.2, 0.14, 5]} />
                <meshStandardMaterial color="#ffd23f" metalness={0.6} roughness={0.3} flatShading />
              </mesh>
            )}
          </group>
        </group>

        <mesh ref={gem} position={[0, 2.25, 0]} scale={[1, 1.7, 1]}>
          <octahedronGeometry args={[0.14, 0]} />
          <meshStandardMaterial color={STATUS_COLORS[member.status]} emissive={STATUS_COLORS[member.status]}
            emissiveIntensity={0.55} flatShading />
        </mesh>

        {(selected || hovered) && (
          <mesh position={[0, 0.06, -0.1]} rotation={[-HALF_PI, 0, Math.PI / 4]}>
            <ringGeometry args={[0.85, 1.0, 4]} />
            <meshBasicMaterial color={selected ? "#39d353" : "#ffffff"} transparent opacity={selected ? 0.9 : 0.45} />
          </mesh>
        )}
      </group>
    </group>
  );
}
