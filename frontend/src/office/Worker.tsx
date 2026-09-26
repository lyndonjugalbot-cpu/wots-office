import { RoundedBox } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useRef, useState } from "react";
import type { Group, Mesh } from "three";
import type { OfficeAgent } from "../types";
import { moveLabel } from "./labels";
import { STATUS_COLORS, looks, soften, type HairStyle, type V2, type Vec3, type WalkMap, type WanderSlot } from "./layout";
import { CharacterBody, clipDuration, type AnimState, type CastMember } from "./people";
import type { AnimationClip } from "three";
import { Suspense } from "react";

interface Props {
  member: OfficeAgent;
  character?: CastMember; // a realistic person (Mixamo); without one, a drawn person
  clips?: AnimationClip[];
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

// The desk is in front of the person (+z, towards the viewer) and the chair behind them
const SEAT: V2 = [0, -0.05];
const STAND_UP: V2 = [0.75, -0.4]; // beside the chair, clear of the desk
const AT_DESK_YAW = Math.PI; // facing the desk (and the viewer)
const SPEED = 1.5; // world units per second
const STAND_HEIGHT = 0.4;
const HALF_PI = Math.PI / 2;
// Which wander spots are taken, so two workers never stand in the same place
const taken = new Map<string, string>();

const rand = (a: number, b: number) => a + Math.random() * (b - a);
// ?wander in the URL: idle people get up after a second (for checking walking without waiting)
const QUICK_WANDER = typeof location !== "undefined" && new URLSearchParams(location.search).has("wander");
const angleTo = (from: number, to: number) => from + ((((to - from + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) - Math.PI);
// The model faces -z at yaw 0; this yaw turns it to face direction (dx, dz)
const yawFor = (dx: number, dz: number) => Math.atan2(-dx, -dz);

/** An office worker. Sits and types when busy; when idle for a while, wanders the office. */
const WALK_CLIP_SPEED = 1.65; // m/s the walk animation covers at normal speed (1.7 m per loop at 1.75 m tall)

export function Worker({ member, character, clips, home, map, selected, onSelect }: Props) {
  const anim = useRef<AnimState>({ clip: "sit_idle", timeScale: 1 });
  const standTime = character ? clipDuration(clips, "stand_up", 2.3) : 0.5;
  const sitTime = character ? clipDuration(clips, "sit_down", 2.2) : 0.5;
  const walker = useRef<Group>(null);
  const body = useRef<Group>(null);
  const head = useRef<Group>(null);
  const armL = useRef<Group>(null);
  const armR = useRef<Group>(null);
  const elbowL = useRef<Group>(null);
  const elbowR = useRef<Group>(null);
  const hipL = useRef<Group>(null);
  const hipR = useRef<Group>(null);
  const kneeL = useRef<Group>(null);
  const kneeR = useRef<Group>(null);
  const gem = useRef<Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const { skin, hair, style, trousers, shoes } = looks(member.id);
  const isCeo = member.kind === "ceo";
  const status = useRef(member.status);
  status.current = member.status;
  const sim = useRef<Sim>({
    mode: "seated", idle: 0, wait: QUICK_WANDER ? rand(0.5, 2) : rand(6, 16), outbound: [], path: [], seg: 0, pos: [...SEAT], yaw: AT_DESK_YAW,
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
    s.path = [...visited].reverse().concat([STAND_UP, SEAT]); // round the chair, then sit
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
      case "standing": // getting up in place, then walking out round the chair
        s.stand = Math.min(1, s.stand + dt / standTime);
        if (busy) {
          headHome(s, []);
          s.mode = "sitting";
        } else if (s.stand >= 1) {
          s.path = s.outbound; // starts beside the chair
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
      case "sitting": // facing the desk and sitting down
        s.yaw += (angleTo(s.yaw, AT_DESK_YAW) - s.yaw) * Math.min(1, dt * 8);
        s.stand = Math.max(0, s.stand - dt / sitTime);
        if (s.stand <= 0) {
          s.mode = "seated";
          s.idle = 0;
          s.wait = QUICK_WANDER ? rand(1, 3) : rand(12, 30); // sit a while before the next wander
        }
        break;
    }

    // ---- which animation a realistic person plays
    const st0 = status.current;
    const a = anim.current;
    a.timeScale = 1;
    if (s.mode === "seated") {
      a.clip = st0 === "working" ? "typing" : st0 === "waiting" ? "sit_waiting" : st0 === "done" ? "sit_done"
        : st0 === "error" ? "sit_error" : "sit_idle";
    } else if (s.mode === "standing") a.clip = "stand_up";
    else if (s.mode === "sitting") a.clip = "sit_down";
    else if (s.mode === "dwelling") a.clip = s.slot?.key.startsWith("cooler") ? "drink" : "idle";
    else if (s.moving) {
      a.clip = "walk";
      a.timeScale = SPEED / WALK_CLIP_SPEED;
    } else a.clip = "idle";

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
    if (armL.current && armR.current && elbowL.current && elbowR.current) {
      let [sl, sr, el, er] = [0.1, 0.1, 0.15, 0.15];
      if (atDesk) {
        // Typing when working (forearms on the keyboard), arms up when done
        const typing = st === "working" ? Math.sin(t * 16) * 0.08 : 0;
        [sl, sr, el, er] = [0.55 + typing, 0.55 - typing, 1.0, 1.0];
        if (st === "done") {
          const wave = Math.sin(t * 6) * 0.2;
          [sl, sr, el, er] = [2.7 + wave, 2.7 - wave, 0.25, 0.25];
        } else if (st === "idle") [sl, sr, el, er] = [0.35, 0.35, 1.3, 1.3]; // resting on the desk
      } else {
        const dwelling = s.mode === "dwelling";
        const sip = dwelling ? Math.max(0, Math.sin(t * 0.8)) : 0;
        const sitting = seatedAmount;
        sl = -swing * 0.7 + sitting * 0.5 + (dwelling ? 0.25 : 0);
        sr = swing * 0.7 + sitting * 0.5 + sip * 0.5;
        el = 0.2 + sitting * 0.6 + (dwelling ? 1.2 : 0); // a cup held in the left hand
        er = 0.2 + sitting * 0.6 + sip * 1.8; // a sip
      }
      const ease = Math.min(1, dt * 10);
      armL.current.rotation.x += (sl - armL.current.rotation.x) * ease;
      armR.current.rotation.x += (sr - armR.current.rotation.x) * ease;
      elbowL.current.rotation.x += (el - elbowL.current.rotation.x) * ease;
      elbowR.current.rotation.x += (er - elbowR.current.rotation.x) * ease;
    }
    if (head.current) {
      head.current.rotation.y = atDesk
        ? st === "waiting" ? Math.sin(t * 1.5) * 0.5 : st === "working" ? Math.sin(t * 0.7) * 0.08 : 0
        : s.mode === "dwelling" ? Math.sin(t * 0.6) * 0.4 : 0;
      head.current.rotation.x = atDesk ? (st === "error" ? -0.45 : st === "working" ? -0.05 : 0) : 0;
    }
    if (gem.current) {
      gem.current.position.y = (character ? 1.55 + 0.45 * s.stand : 1.95 + STAND_HEIGHT * s.stand) + Math.sin(t * 2) * 0.03;
    }

    // Name tag and speech bubble follow the person around the office
    const wx = home[0] + s.pos[0];
    const wz = home[2] + s.pos[1];
    // At the desk the tag sits in front of it; on the move it stays at the person's feet
    moveLabel(`${member.id}:tag`, wx, 0.05, wz + (s.mode === "seated" ? 1.55 : 0.45));
    moveLabel(`${member.id}:bubble`, wx, (character ? 1.95 + 0.45 * s.stand : 2.35 + STAND_HEIGHT * s.stand), wz);
  });

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(member.id);
  };
  const shirt = isCeo ? "#2b2f36" : soften(member.color);
  const mat = (color: string, roughness = 0.75) => <meshStandardMaterial color={color} roughness={roughness} />;

  const leg = (x: number, hip: typeof hipL, knee: typeof kneeL) => (
    <group ref={hip} position={[x, 0.62, 0]} rotation={[HALF_PI, 0, 0]}>
      <mesh position={[0, -0.25, 0]} castShadow>
        <capsuleGeometry args={[0.085, 0.33, 6, 16]} />
        {mat(trousers)}
      </mesh>
      <group ref={knee} position={[0, -0.5, 0]} rotation={[-HALF_PI, 0, 0]}>
        <mesh position={[0, -0.23, 0]} castShadow>
          <capsuleGeometry args={[0.072, 0.32, 6, 16]} />
          {mat(trousers)}
        </mesh>
        <RoundedBox args={[0.13, 0.08, 0.27]} radius={0.035} smoothness={3} position={[0, -0.5, -0.05]} castShadow>
          {mat(shoes, 0.5)}
        </RoundedBox>
      </group>
    </group>
  );
  const arm = (x: number, shoulder: typeof armL, elbow: typeof elbowL) => (
    <group ref={shoulder} position={[x, 1.13, 0.01]} rotation={[0.5, 0, 0]}>
      <mesh position={[0, -0.15, 0]} castShadow>
        <capsuleGeometry args={[0.06, 0.2, 6, 16]} />
        {mat(shirt)}
      </mesh>
      <group ref={elbow} position={[0, -0.3, 0]} rotation={[0.9, 0, 0]}>
        <mesh position={[0, -0.13, 0]} castShadow>
          <capsuleGeometry args={[0.05, 0.18, 6, 16]} />
          {mat(isCeo ? shirt : skin, 0.6)}
        </mesh>
        <mesh position={[0, -0.29, 0]} scale={[0.9, 1.15, 0.75]} castShadow>
          <sphereGeometry args={[0.052, 16, 12]} />
          {mat(skin, 0.6)}
        </mesh>
      </group>
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
      <Chair ceo={isCeo} />

      {/* The person moves as a whole (walker), with jointed hips, knees, shoulders and elbows */}
      <group ref={walker} position={[SEAT[0], 0, SEAT[1]]}>
        {character && clips ? (
          <Suspense fallback={null}>
            <CharacterBody member={character} clips={clips} state={anim} />
          </Suspense>
        ) : (
        <group ref={body}>
          {leg(-0.1, hipL, kneeL)}
          {leg(0.1, hipR, kneeR)}
          {/* hips and torso */}
          <mesh position={[0, 0.66, 0.02]} scale={[1.45, 0.7, 1]} castShadow>
            <sphereGeometry args={[0.15, 24, 16]} />
            {mat(trousers)}
          </mesh>
          <mesh position={[0, 0.93, 0.02]} scale={[1.28, 1, 0.82]} castShadow>
            <capsuleGeometry args={[0.17, 0.3, 8, 24]} />
            {mat(shirt)}
          </mesh>
          {isCeo && (
            <>
              <mesh position={[0, 1.07, -0.12]} rotation={[0.15, 0, 0]}>
                <planeGeometry args={[0.12, 0.2]} />
                <meshStandardMaterial color="#f5f5f7" side={2} />
              </mesh>
              <mesh position={[0, 0.98, -0.135]} rotation={[0.1, 0, 0]}>
                <boxGeometry args={[0.045, 0.24, 0.01]} />
                <meshStandardMaterial color={member.color} roughness={0.5} />
              </mesh>
            </>
          )}
          {arm(-0.27, armL, elbowL)}
          {arm(0.27, armR, elbowR)}
          <mesh position={[0, 1.27, 0.02]} castShadow>
            <cylinderGeometry args={[0.055, 0.065, 0.12, 16]} />
            {mat(skin, 0.6)}
          </mesh>
          <group ref={head} position={[0, 1.45, 0.02]}>
            <Head skin={skin} hair={hair} style={style} />
          </group>
        </group>
        )}

        {/* status: a small glowing dot above the head */}
        <mesh ref={gem} position={[0, 1.95, 0]}>
          <sphereGeometry args={[0.075, 24, 16]} />
          <meshStandardMaterial color={STATUS_COLORS[member.status]} emissive={STATUS_COLORS[member.status]}
            emissiveIntensity={0.9} roughness={0.3} />
        </mesh>

        {(selected || hovered) && (
          <mesh position={[0, 0.04, -0.05]} rotation={[-HALF_PI, 0, 0]}>
            <ringGeometry args={[0.62, 0.72, 64]} />
            <meshBasicMaterial color={selected ? "#0a84ff" : "#ffffff"} transparent opacity={selected ? 0.9 : 0.55} />
          </mesh>
        )}
      </group>
    </group>
  );
}

/** A face that reads as human at office distance: rounded head, eyes, brows, nose, ears and hair. */
function Head({ skin, hair, style }: { skin: string; hair: string; style: HairStyle }) {
  // The cap covers the crown and the back; a slight forward tilt keeps the hairline above the brows
  const cap = (radius: number, cover = 0.62) => (
    <mesh rotation={[-0.12, 0, 0]} scale={[0.99, 1.12, 1.03]} castShadow>
      <sphereGeometry args={[radius, 32, 16, 0, Math.PI * 2, 0, Math.PI * cover]} />
      <meshStandardMaterial color={hair} roughness={0.85} side={2} />
    </mesh>
  );
  return (
    <group>
      <mesh scale={[0.95, 1.08, 1]} castShadow>
        <sphereGeometry args={[0.16, 32, 24]} />
        <meshStandardMaterial color={skin} roughness={0.6} />
      </mesh>
      {/* ears */}
      {[-1, 1].map((s) => (
        <mesh key={s} position={[s * 0.152, -0.01, 0.01]} scale={[0.5, 1, 0.8]}>
          <sphereGeometry args={[0.035, 12, 8]} />
          <meshStandardMaterial color={skin} roughness={0.6} />
        </mesh>
      ))}
      {/* eyes, brows, nose (the face looks towards -z) */}
      {[-1, 1].map((s) => (
        <group key={`e${s}`} position={[s * 0.055, 0.02, -0.145]}>
          <mesh scale={[1, 1.2, 0.6]}>
            <sphereGeometry args={[0.018, 12, 8]} />
            <meshStandardMaterial color="#1c1c1e" roughness={0.2} />
          </mesh>
          <mesh position={[0, 0.045, 0.004]} rotation={[0, 0, s * -0.12]}>
            <boxGeometry args={[0.05, 0.011, 0.01]} />
            <meshStandardMaterial color={hair} roughness={0.9} />
          </mesh>
        </group>
      ))}
      <mesh position={[0, -0.025, -0.158]} scale={[0.8, 1, 1]}>
        <sphereGeometry args={[0.02, 12, 8]} />
        <meshStandardMaterial color={skin} roughness={0.6} />
      </mesh>
      <mesh position={[0, -0.075, -0.142]} rotation={[0, 0, Math.PI]}>
        <torusGeometry args={[0.025, 0.006, 6, 12, Math.PI]} />
        <meshStandardMaterial color="#8a4b4b" roughness={0.6} />
      </mesh>
      {/* hair */}
      {style === "buzz" ? cap(0.164, 0.55) : cap(0.172)}
      {/* a fringe so the hairline reads from the front */}
      {style !== "buzz" && (
        <mesh position={[0, 0.085, -0.1]} rotation={[0.9, 0, 0]} scale={[1, 0.55, 0.45]}>
          <sphereGeometry args={[0.13, 20, 12, 0, Math.PI * 2, 0, Math.PI / 2]} />
          <meshStandardMaterial color={hair} roughness={0.85} side={2} />
        </mesh>
      )}
      {style === "long" && (
        <mesh position={[0, -0.1, 0.09]} castShadow>
          <capsuleGeometry args={[0.13, 0.18, 8, 16]} />
          <meshStandardMaterial color={hair} roughness={0.85} />
        </mesh>
      )}
      {style === "bun" && (
        <mesh position={[0, 0.1, 0.14]} castShadow>
          <sphereGeometry args={[0.07, 20, 14]} />
          <meshStandardMaterial color={hair} roughness={0.85} />
        </mesh>
      )}
      {style === "curly" &&
        [[-0.08, 0.13, 0], [0.08, 0.13, 0], [0, 0.16, 0.04], [-0.1, 0.07, 0.08], [0.1, 0.07, 0.08], [0, 0.1, 0.11]].map(([x, y, z], i) => (
          <mesh key={i} position={[x, y, z]}>
            <sphereGeometry args={[0.065, 14, 10]} />
            <meshStandardMaterial color={hair} roughness={0.9} />
          </mesh>
        ))}
    </group>
  );
}

/** An ergonomic office chair (an executive one for the CEO); it stays at the desk. */
function Chair({ ceo }: { ceo: boolean }) {
  const shell = ceo ? "#7a4a2a" : "#2c2f36";
  return (
    <group position={[0, 0, -0.1]} rotation={[0, Math.PI, 0]}>
      <RoundedBox args={[0.56, 0.08, 0.52]} radius={0.035} smoothness={3} position={[0, 0.49, 0]} castShadow>
        <meshStandardMaterial color={shell} roughness={0.6} />
      </RoundedBox>
      <RoundedBox args={[0.52, ceo ? 0.95 : 0.66, 0.06]} radius={0.03} smoothness={3} position={[0, ceo ? 1.03 : 0.9, 0.27]}
        rotation={[-0.08, 0, 0]} castShadow>
        <meshStandardMaterial color={shell} roughness={ceo ? 0.45 : 0.8} />
      </RoundedBox>
      {[-1, 1].map((s) => (
        <RoundedBox key={s} args={[0.05, 0.03, 0.3]} radius={0.012} smoothness={2} position={[s * 0.28, 0.66, 0.02]}>
          <meshStandardMaterial color="#1f2328" roughness={0.5} />
        </RoundedBox>
      ))}
      <mesh position={[0, 0.26, 0]}>
        <cylinderGeometry args={[0.028, 0.028, 0.44, 12]} />
        <meshStandardMaterial color="#9aa0a6" metalness={0.8} roughness={0.3} />
      </mesh>
      {[0, 1, 2, 3, 4].map((i) => {
        const a = (i / 5) * Math.PI * 2;
        return (
          <group key={i} rotation={[0, a, 0]}>
            <mesh position={[0.15, 0.06, 0]}>
              <boxGeometry args={[0.3, 0.035, 0.05]} />
              <meshStandardMaterial color="#1f2328" roughness={0.5} />
            </mesh>
            <mesh position={[0.29, 0.03, 0]}>
              <sphereGeometry args={[0.03, 10, 8]} />
              <meshStandardMaterial color="#1f2328" roughness={0.4} />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}
