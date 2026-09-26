import { RoundedBox } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { CanvasTexture, LinearFilter, LinearMipmapLinearFilter, RepeatWrapping, SRGBColorSpace, type Group } from "three";
import { BACK_WALL_Z, ROOM_HALF_WIDTH } from "./layout";

const WALL_HEIGHT = 3.4;
const WALL = "#f4f1ec"; // warm white
const FEATURE = "#6f8f7f"; // sage feature wall behind the team
const OAK = "#c9a27a";
const BLACK = "#1f2328";
export const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif";

function canvasTexture(size: [number, number], draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
  repeat: [number, number]) {
  const canvas = document.createElement("canvas");
  [canvas.width, canvas.height] = size;
  draw(canvas.getContext("2d")!, size[0], size[1]);
  const tex = new CanvasTexture(canvas);
  // Mipmaps stop thin plank lines shimmering (moiré) at a distance or while the camera moves
  tex.minFilter = LinearMipmapLinearFilter;
  tex.magFilter = LinearFilter;
  tex.generateMipmaps = true;
  tex.wrapS = tex.wrapT = RepeatWrapping;
  tex.repeat.set(...repeat);
  tex.colorSpace = SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

/** A bright modern studio: oak floor, a sage feature wall, tall windows and plants. */
export function Room({ frontZ, board }: { frontZ: number; board: { title: string; lines: string[]; footer: string } }) {
  const depth = frontZ - BACK_WALL_Z;
  const width = ROOM_HALF_WIDTH * 2;
  const centerZ = BACK_WALL_Z + depth / 2;

  // Light oak planks with soft grain and staggered joints
  const floor = useMemo(
    () =>
      canvasTexture([512, 512], (ctx, w, h) => {
        const rows = 8;
        const plank = h / rows;
        for (let r = 0; r < rows; r++) {
          const shade = 0.94 + ((r * 37) % 7) / 100;
          ctx.fillStyle = `rgb(${Math.round(214 * shade)}, ${Math.round(182 * shade)}, ${Math.round(146 * shade)})`;
          ctx.fillRect(0, r * plank, w, plank);
          ctx.strokeStyle = "rgba(120, 85, 50, 0.08)";
          for (let g = 0; g < 6; g++) {
            ctx.beginPath();
            const y = r * plank + 6 + g * (plank / 6);
            ctx.moveTo(0, y);
            ctx.bezierCurveTo(w * 0.3, y + 3, w * 0.6, y - 3, w, y + 1);
            ctx.stroke();
          }
          ctx.fillStyle = "rgba(90, 60, 30, 0.18)";
          ctx.fillRect(0, r * plank, w, 1.5);
          const joint = ((r * 197) % w + w) % w;
          ctx.fillRect(joint, r * plank, 1.5, plank);
        }
      }, [width / 4, depth / 4]),
    [width, depth],
  );

  const rugZ = (0.6 + frontZ - 3) / 2 + 0.3;

  return (
    <group>
      {/* floor, with a slim plinth edge */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, centerZ]} receiveShadow>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial map={floor} roughness={0.55} />
      </mesh>
      {/* the slab's top stays just under the floor: two surfaces at one height flicker (z-fighting) */}
      <mesh position={[0, -0.13, centerZ]}>
        <boxGeometry args={[width + 0.4, 0.24, depth + 0.2]} />
        <meshStandardMaterial color="#e2ddd5" roughness={0.9} />
      </mesh>

      {/* back wall: warm white, with a sage feature panel behind the whiteboard screen */}
      <mesh position={[0, WALL_HEIGHT / 2, BACK_WALL_Z - 0.1]} receiveShadow>
        <boxGeometry args={[width + 0.4, WALL_HEIGHT, 0.2]} />
        <meshStandardMaterial color={WALL} roughness={0.95} />
      </mesh>
      <mesh position={[0, WALL_HEIGHT / 2, BACK_WALL_Z + 0.005]} receiveShadow>
        <planeGeometry args={[9, WALL_HEIGHT]} />
        <meshStandardMaterial color={FEATURE} roughness={0.9} />
      </mesh>
      {/* modern flush door */}
      <group position={[6.4, 0, BACK_WALL_Z + 0.02]}>
        <mesh position={[0, 1.1, 0]}>
          <boxGeometry args={[1.05, 2.2, 0.05]} />
          <meshStandardMaterial color={OAK} roughness={0.6} />
        </mesh>
        <mesh position={[-0.36, 1.05, 0.05]}>
          <boxGeometry args={[0.03, 0.45, 0.03]} />
          <meshStandardMaterial color={BLACK} metalness={0.6} roughness={0.35} />
        </mesh>
      </group>

      {/* left wall: floor-to-ceiling windows with slim black frames */}
      <mesh position={[-ROOM_HALF_WIDTH - 0.1, WALL_HEIGHT / 2, centerZ]} receiveShadow>
        <boxGeometry args={[0.2, WALL_HEIGHT, depth]} />
        <meshStandardMaterial color={WALL} roughness={0.95} />
      </mesh>
      {[-3.4, 0.4, 4.2].filter((z) => z < frontZ - 1.5).map((z) => (
        <TallWindow key={z} position={[-ROOM_HALF_WIDTH + 0.01, 0, z]} />
      ))}
      {/* skirting */}
      <mesh position={[0, 0.05, BACK_WALL_Z + 0.01]}>
        <boxGeometry args={[width + 0.4, 0.1, 0.03]} />
        <meshStandardMaterial color="#e6e1d8" />
      </mesh>

      <PipelineScreen {...board} />
      <WallClock position={[-5.2, 2.55, BACK_WALL_Z + 0.03]} />


      <Plant position={[-7.4, 0, BACK_WALL_Z + 0.6]} kind="tall" />
      <Plant position={[7.6, 0, frontZ - 0.8]} scale={0.85} />
      <Plant position={[-7.5, 0, frontZ - 0.8]} scale={1.1} kind="tall" />
      <CoffeeStation position={[7.4, 0, BACK_WALL_Z + 0.55]} />
      <Shelving position={[-7.75, 0, -0.4]} />

      {/* a large, soft neutral rug under the team area */}
      <RoundedBox args={[12.8, 0.02, Math.max(4.4, frontZ - 0.8)]} radius={0.01} smoothness={2}
        position={[0, 0.01, rugZ]} receiveShadow>
        <meshStandardMaterial color="#e3dbcf" roughness={1} />
      </RoundedBox>
      <RoundedBox args={[12.2, 0.02, Math.max(3.8, frontZ - 1.4)]} radius={0.01} smoothness={2}
        position={[0, 0.02, rugZ]} receiveShadow>
        <meshStandardMaterial color="#d6ccbd" roughness={1} />
      </RoundedBox>
    </group>
  );
}

function TallWindow({ position }: { position: [number, number, number] }) {
  const h = 2.9;
  return (
    <group position={position} rotation={[0, Math.PI / 2, 0]}>
      <mesh position={[0, h / 2 + 0.2, 0.02]}>
        <planeGeometry args={[2.4, h]} />
        <meshStandardMaterial color="#cfe6f5" emissive="#e8f4ff" emissiveIntensity={0.65} roughness={0.2} />
      </mesh>
      {/* frame and mullions */}
      {[[0, 0.2, 2.5, 0.06], [0, h + 0.2, 2.5, 0.06], [0, 1.4, 2.5, 0.035]].map(([x, y, w, t], i) => (
        <mesh key={`h${i}`} position={[x, y, 0.05]}>
          <boxGeometry args={[w, t, 0.05]} />
          <meshStandardMaterial color={BLACK} roughness={0.4} />
        </mesh>
      ))}
      {[-1.22, 0, 1.22].map((x) => (
        <mesh key={`v${x}`} position={[x, h / 2 + 0.2, 0.05]}>
          <boxGeometry args={[x === 0 ? 0.035 : 0.06, h, 0.05]} />
          <meshStandardMaterial color={BLACK} roughness={0.4} />
        </mesh>
      ))}
    </group>
  );
}

/** A wall-mounted screen showing the pipeline, drawn on a canvas so the text sits flat in 3D. */
function PipelineScreen({ title, lines, footer }: { title: string; lines: string[]; footer: string }) {
  const canvas = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 1024;
    c.height = 540;
    return c;
  }, []);
  const texture = useMemo(() => {
    const t = new CanvasTexture(canvas);
    t.colorSpace = SRGBColorSpace;
    t.anisotropy = 4;
    return t;
  }, [canvas]);
  const key = `${title}|${lines.join("|")}|${footer}`;

  useEffect(() => {
    const ctx = canvas.getContext("2d")!;
    const grad = ctx.createLinearGradient(0, 0, 1024, 540);
    grad.addColorStop(0, "#161b26");
    grad.addColorStop(1, "#243047");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 1024, 540);
    ctx.fillStyle = "#8fa3c7";
    ctx.font = `600 34px ${FONT}`;
    ctx.fillText(title.charAt(0) + title.slice(1).toLowerCase(), 56, 84);
    lines.slice(0, 4).forEach((line, i) => {
      const [num, ...rest] = line.split(" ");
      const y = 170 + i * 88;
      ctx.fillStyle = line.includes("escalated") ? "#ff8a80" : "#ffffff";
      ctx.font = `700 64px ${FONT}`;
      ctx.fillText(num, 56, y);
      const w = ctx.measureText(num).width;
      ctx.fillStyle = "#c5d0e6";
      ctx.font = `400 40px ${FONT}`;
      ctx.fillText(rest.join(" ").replace("!", ""), 56 + w + 20, y - 4);
    });
    if (footer) {
      ctx.font = `600 26px ${FONT}`;
      const w = ctx.measureText(footer).width + 40;
      ctx.fillStyle = footer === "LIVE" ? "#2ecc71" : "#f5a524";
      ctx.beginPath();
      ctx.roundRect(1024 - w - 48, 48, w, 48, 24);
      ctx.fill();
      ctx.fillStyle = "#111";
      ctx.fillText(footer, 1024 - w - 28, 82);
    }
    texture.needsUpdate = true;
  }, [canvas, texture, key]);

  return (
    <group position={[0, 2.05, BACK_WALL_Z + 0.06]}>
      <RoundedBox args={[3.6, 1.95, 0.08]} radius={0.04} smoothness={3}>
        <meshStandardMaterial color="#111318" roughness={0.3} metalness={0.4} />
      </RoundedBox>
      <mesh position={[0, 0, 0.045]}>
        <planeGeometry args={[3.45, 1.82]} />
        <meshStandardMaterial map={texture} emissiveMap={texture} emissive="#ffffff" emissiveIntensity={0.55} roughness={0.25} />
      </mesh>
    </group>
  );
}

function WallClock({ position }: { position: [number, number, number] }) {
  const hour = useRef<Group>(null);
  const minute = useRef<Group>(null);
  useFrame(() => {
    const now = new Date();
    const m = now.getMinutes() + now.getSeconds() / 60;
    if (minute.current) minute.current.rotation.z = -(m / 60) * Math.PI * 2;
    if (hour.current) hour.current.rotation.z = -(((now.getHours() % 12) + m / 60) / 12) * Math.PI * 2;
  });
  return (
    <group position={position}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.34, 0.34, 0.04, 48]} />
        <meshStandardMaterial color="#ffffff" roughness={0.4} />
      </mesh>
      <mesh position={[0, 0, 0.005]}>
        <torusGeometry args={[0.34, 0.018, 8, 48]} />
        <meshStandardMaterial color={BLACK} roughness={0.3} />
      </mesh>
      <group ref={hour} position={[0, 0, 0.03]}>
        <mesh position={[0, 0.08, 0]}>
          <boxGeometry args={[0.025, 0.17, 0.01]} />
          <meshBasicMaterial color={BLACK} />
        </mesh>
      </group>
      <group ref={minute} position={[0, 0, 0.035]}>
        <mesh position={[0, 0.12, 0]}>
          <boxGeometry args={[0.015, 0.26, 0.01]} />
          <meshBasicMaterial color={BLACK} />
        </mesh>
      </group>
    </group>
  );
}

/** A ceramic pot with broad, glossy leaves (a monstera-ish houseplant), or a tall fiddle-leaf. */
function Plant({ position, scale = 1, kind = "bushy" }: { position: [number, number, number]; scale?: number; kind?: "bushy" | "tall" }) {
  const leaves = useMemo(() => {
    const out: { pos: [number, number, number]; rot: [number, number, number]; size: number }[] = [];
    const count = kind === "tall" ? 11 : 9;
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2 + i * 0.7;
      const height = kind === "tall" ? 0.9 + (i / count) * 1.1 : 0.7 + (i % 3) * 0.12;
      const spread = kind === "tall" ? 0.18 : 0.3;
      out.push({ pos: [Math.cos(a) * spread, height, Math.sin(a) * spread], rot: [0.5, -a, 0.3], size: kind === "tall" ? 0.17 : 0.22 });
    }
    return out;
  }, [kind]);
  return (
    <group position={position} scale={scale}>
      <mesh position={[0, 0.24, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[0.26, 0.2, 0.48, 32]} />
        <meshStandardMaterial color={kind === "tall" ? "#f2efe9" : "#c4876a"} roughness={0.55} />
      </mesh>
      {kind === "tall" && (
        <mesh position={[0, 0.9, 0]}>
          <cylinderGeometry args={[0.025, 0.03, 1.3, 8]} />
          <meshStandardMaterial color="#6b5a3e" />
        </mesh>
      )}
      {leaves.map((l, i) => (
        <mesh key={i} position={l.pos} rotation={l.rot} scale={[1, 0.18, 1.6]} castShadow>
          <sphereGeometry args={[l.size, 16, 12]} />
          <meshStandardMaterial color={i % 3 ? "#3f7d4f" : "#2f6a40"} roughness={0.45} />
        </mesh>
      ))}
    </group>
  );
}

function CoffeeStation({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <RoundedBox args={[1.3, 0.9, 0.6]} radius={0.03} smoothness={3} position={[0, 0.45, 0]} castShadow receiveShadow>
        <meshStandardMaterial color="#f7f5f1" roughness={0.5} />
      </RoundedBox>
      <mesh position={[0, 0.915, 0]}>
        <boxGeometry args={[1.34, 0.04, 0.64]} />
        <meshStandardMaterial color={OAK} roughness={0.5} />
      </mesh>
      {/* espresso machine and cups */}
      <RoundedBox args={[0.42, 0.42, 0.36]} radius={0.05} smoothness={3} position={[-0.25, 1.15, -0.05]} castShadow>
        <meshStandardMaterial color="#c9ccd1" metalness={0.8} roughness={0.25} />
      </RoundedBox>
      {[0.2, 0.36].map((x) => (
        <mesh key={x} position={[x, 0.98, 0.1]} castShadow>
          <cylinderGeometry args={[0.05, 0.042, 0.1, 20]} />
          <meshStandardMaterial color="#ffffff" roughness={0.3} />
        </mesh>
      ))}
    </group>
  );
}

function Shelving({ position }: { position: [number, number, number] }) {
  const books = ["#e8dcc8", "#3d5a80", "#98c1d9", "#ee6c4d", "#293241", "#c9ada7", "#6d6875"];
  return (
    <group position={position} rotation={[0, Math.PI / 2, 0]}>
      {/* slim black frame with oak shelves */}
      {[-1, 1].map((sx) => (
        <mesh key={sx} position={[sx * 1.0, 1.05, 0]} castShadow>
          <boxGeometry args={[0.04, 2.1, 0.38]} />
          <meshStandardMaterial color={BLACK} roughness={0.4} />
        </mesh>
      ))}
      {[0.08, 0.72, 1.36, 2.0].map((y) => (
        <mesh key={y} position={[0, y, 0]} castShadow receiveShadow>
          <boxGeometry args={[2.0, 0.04, 0.36]} />
          <meshStandardMaterial color={OAK} roughness={0.55} />
        </mesh>
      ))}
      {[0.1, 0.74, 1.38].map((y, row) =>
        books.slice(0, 5 + row).map((c, i) => (
          <mesh key={`${row}${i}`} position={[-0.8 + i * 0.13, y + 0.2 - ((i + row) % 3) * 0.02, 0.02]} castShadow>
            <boxGeometry args={[0.1, 0.4 - ((i + row) % 3) * 0.04, 0.26]} />
            <meshStandardMaterial color={books[(i + row * 2) % books.length] ?? c} roughness={0.8} />
          </mesh>
        )),
      )}
      <group position={[0.55, 0.74, 0]} scale={0.45}>
        <Plant position={[0, 0, 0]} />
      </group>
      <mesh position={[0.6, 1.5, 0]} castShadow>
        <sphereGeometry args={[0.1, 24, 16]} />
        <meshStandardMaterial color="#e9c46a" roughness={0.3} />
      </mesh>
    </group>
  );
}
