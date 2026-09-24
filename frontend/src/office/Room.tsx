import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { CanvasTexture, NearestFilter, RepeatWrapping, SRGBColorSpace, type Group } from "three";
import { BACK_WALL_Z, ROOM_HALF_WIDTH } from "./layout";

const WALL_HEIGHT = 3.4;

function pixelTexture(size: number, draw: (ctx: CanvasRenderingContext2D) => void, repeat: [number, number]) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  draw(canvas.getContext("2d")!);
  const tex = new CanvasTexture(canvas);
  tex.magFilter = NearestFilter; // crisp, pixelly retro look
  tex.wrapS = tex.wrapT = RepeatWrapping;
  tex.repeat.set(...repeat);
  tex.colorSpace = SRGBColorSpace;
  return tex;
}

export function Room({ frontZ, board }: { frontZ: number; board: { title: string; lines: string[]; footer: string } }) {
  const depth = frontZ - BACK_WALL_Z;
  const width = ROOM_HALF_WIDTH * 2;

  const floor = useMemo(
    () =>
      pixelTexture(32, (ctx) => {
        ctx.fillStyle = "#e8d9b8";
        ctx.fillRect(0, 0, 32, 32);
        ctx.fillStyle = "#d3bf95";
        ctx.fillRect(0, 0, 16, 16);
        ctx.fillRect(16, 16, 16, 16);
        ctx.fillStyle = "rgba(0,0,0,0.08)";
        ctx.fillRect(0, 15, 32, 1);
        ctx.fillRect(15, 0, 1, 32);
      }, [width / 2, depth / 2]),
    [width, depth],
  );
  const wallpaper = useMemo(
    () =>
      pixelTexture(16, (ctx) => {
        ctx.fillStyle = "#5fa8a0";
        ctx.fillRect(0, 0, 16, 16);
        ctx.fillStyle = "#6fb8ae";
        ctx.fillRect(0, 0, 8, 16);
        ctx.fillStyle = "#f4e3b5";
        ctx.fillRect(3, 6, 2, 2);
      }, [width, 3]),
    [width],
  );

  return (
    <group>
      {/* floor */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, BACK_WALL_Z + depth / 2]} receiveShadow>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial map={floor} />
      </mesh>
      {/* floor slab edge */}
      <mesh position={[0, -0.15, BACK_WALL_Z + depth / 2]}>
        <boxGeometry args={[width + 0.4, 0.3, depth + 0.2]} />
        <meshStandardMaterial color="#7b5a3a" flatShading />
      </mesh>

      {/* back wall with a door */}
      <Wall position={[0, WALL_HEIGHT / 2, BACK_WALL_Z - 0.1]} size={[width + 0.4, WALL_HEIGHT, 0.2]} map={wallpaper} />
      <mesh position={[6.2, 1.1, BACK_WALL_Z + 0.01]}>
        <boxGeometry args={[1.1, 2.2, 0.06]} />
        <meshStandardMaterial color="#6b3f22" flatShading />
      </mesh>
      <mesh position={[5.8, 1.1, BACK_WALL_Z + 0.06]}>
        <sphereGeometry args={[0.05, 6, 6]} />
        <meshStandardMaterial color="#ffd23f" metalness={0.6} />
      </mesh>

      {/* left wall with windows */}
      <Wall position={[-ROOM_HALF_WIDTH - 0.1, WALL_HEIGHT / 2, BACK_WALL_Z + depth / 2]} size={[0.2, WALL_HEIGHT, depth]} map={wallpaper} />
      {[-2.5, 1.5].map((z) => (
        <Window key={z} position={[-ROOM_HALF_WIDTH + 0.01, 1.9, z]} />
      ))}

      <Whiteboard {...board} />
      <WallClock position={[-3, 2.75, BACK_WALL_Z + 0.02]} />

      <Plant position={[-7.4, 0, BACK_WALL_Z + 0.6]} />
      <Plant position={[7.6, 0, frontZ - 0.8]} scale={0.8} />
      <Plant position={[-7.5, 0, frontZ - 0.8]} scale={1.1} />
      <WaterCooler position={[7.4, 0, BACK_WALL_Z + 0.7]} />
      <Bookshelf position={[-7.75, 0, -0.4]} />
      {/* rug under the team area */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.03, (0.6 + frontZ - 3) / 2 + 0.3]} receiveShadow>
        <planeGeometry args={[12.5, Math.max(4, frontZ - 1.2)]} />
        <meshStandardMaterial color="#b5523b" />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.045, (0.6 + frontZ - 3) / 2 + 0.3]}>
        <planeGeometry args={[12.0, Math.max(3.5, frontZ - 1.7)]} />
        <meshStandardMaterial color="#c9694f" />
      </mesh>
    </group>
  );
}

function Wall({ position, size, map }: { position: [number, number, number]; size: [number, number, number]; map: CanvasTexture }) {
  return (
    <group>
      <mesh position={position} receiveShadow>
        <boxGeometry args={size} />
        <meshStandardMaterial map={map} />
      </mesh>
      {/* baseboard and top trim */}
      {[0.08, WALL_HEIGHT - 0.06].map((y) => (
        <mesh key={y} position={[position[0], y, position[2]]}>
          <boxGeometry args={[size[0] + 0.02, y < 1 ? 0.16 : 0.12, size[2] + 0.04]} />
          <meshStandardMaterial color={y < 1 ? "#6b3f22" : "#f4ecd8"} flatShading />
        </mesh>
      ))}
    </group>
  );
}

function Window({ position }: { position: [number, number, number] }) {
  return (
    <group position={position} rotation={[0, Math.PI / 2, 0]}>
      <mesh>
        <boxGeometry args={[1.9, 1.4, 0.08]} />
        <meshStandardMaterial color="#f4ecd8" flatShading />
      </mesh>
      <mesh position={[0, 0, 0.045]}>
        <planeGeometry args={[1.7, 1.2]} />
        <meshStandardMaterial color="#9fd9f5" emissive="#bfe8ff" emissiveIntensity={0.5} />
      </mesh>
      <mesh position={[0, 0, 0.06]}>
        <boxGeometry args={[0.06, 1.2, 0.03]} />
        <meshStandardMaterial color="#f4ecd8" />
      </mesh>
      <mesh position={[0, 0, 0.06]}>
        <boxGeometry args={[1.7, 0.06, 0.03]} />
        <meshStandardMaterial color="#f4ecd8" />
      </mesh>
    </group>
  );
}

/** Drawn onto a canvas texture so the text sits flat on the whiteboard in 3D. */
function Whiteboard({ title, lines, footer }: { title: string; lines: string[]; footer: string }) {
  const canvas = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = 512;
    c.height = 256;
    return c;
  }, []);
  const texture = useMemo(() => {
    const t = new CanvasTexture(canvas);
    t.colorSpace = SRGBColorSpace;
    return t;
  }, [canvas]);
  const key = `${title}|${lines.join("|")}|${footer}`;

  useEffect(() => {
    const draw = () => {
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#fbfbf6";
      ctx.fillRect(0, 0, 512, 256);
      ctx.fillStyle = "#2b59c3";
      ctx.font = "20px 'Press Start 2P', monospace";
      ctx.fillText(title, 24, 44);
      ctx.fillStyle = "#222";
      ctx.font = "30px VT323, monospace";
      lines.slice(0, 4).forEach((line, i) => ctx.fillText(line, 24, 90 + i * 32));
      if (footer) {
        ctx.fillStyle = "#b03a2e";
        ctx.font = "14px 'Press Start 2P', monospace";
        ctx.fillText(footer, 24, 240);
      }
      texture.needsUpdate = true;
    };
    draw();
    document.fonts?.ready.then(draw); // redraw once the pixel fonts have loaded
  }, [canvas, texture, key]);

  return (
    <group position={[0, 2.1, BACK_WALL_Z + 0.04]}>
      <mesh>
        <boxGeometry args={[3.5, 1.85, 0.06]} />
        <meshStandardMaterial color="#9aa3ad" flatShading />
      </mesh>
      <mesh position={[0, 0, 0.035]}>
        <planeGeometry args={[3.3, 1.65]} />
        <meshStandardMaterial map={texture} />
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
        <cylinderGeometry args={[0.32, 0.32, 0.05, 16]} />
        <meshStandardMaterial color="#fbfbf6" flatShading />
      </mesh>
      <group ref={hour} position={[0, 0, 0.04]}>
        <mesh position={[0, 0.08, 0]}>
          <boxGeometry args={[0.04, 0.17, 0.01]} />
          <meshBasicMaterial color="#222" />
        </mesh>
      </group>
      <group ref={minute} position={[0, 0, 0.045]}>
        <mesh position={[0, 0.12, 0]}>
          <boxGeometry args={[0.025, 0.25, 0.01]} />
          <meshBasicMaterial color="#222" />
        </mesh>
      </group>
    </group>
  );
}

function Plant({ position, scale = 1 }: { position: [number, number, number]; scale?: number }) {
  return (
    <group position={position} scale={scale}>
      <mesh position={[0, 0.25, 0]} castShadow>
        <cylinderGeometry args={[0.28, 0.2, 0.5, 6]} />
        <meshStandardMaterial color="#c0643a" flatShading />
      </mesh>
      {[
        [0, 0.85, 0, 0.42],
        [0.18, 1.15, 0.05, 0.3],
        [-0.15, 1.1, -0.1, 0.32],
      ].map(([x, y, z, r], i) => (
        <mesh key={i} position={[x, y, z]} castShadow>
          <icosahedronGeometry args={[r, 0]} />
          <meshStandardMaterial color={i ? "#3f9b4b" : "#2f7d3a"} flatShading />
        </mesh>
      ))}
    </group>
  );
}

function WaterCooler({ position }: { position: [number, number, number] }) {
  return (
    <group position={position}>
      <mesh position={[0, 0.5, 0]} castShadow>
        <boxGeometry args={[0.5, 1.0, 0.5]} />
        <meshStandardMaterial color="#e8e8e0" flatShading />
      </mesh>
      <mesh position={[0, 1.3, 0]} castShadow>
        <cylinderGeometry args={[0.2, 0.2, 0.6, 8]} />
        <meshStandardMaterial color="#6ec6ff" transparent opacity={0.8} flatShading />
      </mesh>
    </group>
  );
}

function Bookshelf({ position }: { position: [number, number, number] }) {
  const books = ["#c0392b", "#2980b9", "#27ae60", "#f39c12", "#8e44ad", "#16a085", "#d35400"];
  return (
    <group position={position} rotation={[0, Math.PI / 2, 0]}>
      <mesh position={[0, 1.0, 0]} castShadow>
        <boxGeometry args={[2.0, 2.0, 0.4]} />
        <meshStandardMaterial color="#7b4a26" flatShading />
      </mesh>
      {[0.45, 1.05, 1.65].map((y, row) =>
        books.map((_, i) => (
          <mesh key={`${row}${i}`} position={[-0.8 + i * 0.25, y, 0.1]}>
            <boxGeometry args={[0.18, 0.4 - ((i + row) % 3) * 0.05, 0.28]} />
            <meshStandardMaterial color={books[(i + row * 2) % books.length]} flatShading />
          </mesh>
        )),
      )}
    </group>
  );
}
