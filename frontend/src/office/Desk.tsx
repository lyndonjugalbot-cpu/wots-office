import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { MeshStandardMaterial } from "three";
import type { AgentStatus } from "../types";
import { SCREEN_COLORS } from "./layout";

/** A wooden desk with a beige CRT monitor whose screen glows with the worker's status. */
export function Desk({ status, big }: { status: AgentStatus; big: boolean }) {
  const screen = useRef<MeshStandardMaterial>(null);
  const width = big ? 2.3 : 1.7;
  const wood = big ? "#6b3f22" : "#a8733f";

  useFrame(({ clock }) => {
    if (!screen.current) return;
    const t = clock.elapsedTime;
    // Busy screens flicker like an old CRT; idle ones sit on a dim screensaver
    screen.current.emissiveIntensity =
      status === "working" ? 0.9 + Math.sin(t * 40) * 0.08 : status === "idle" ? 0.35 + Math.sin(t) * 0.05 : 0.9;
  });

  return (
    <group position={[0, 0, -0.75]}>
      {/* top and legs */}
      <mesh position={[0, 0.76, 0]} castShadow receiveShadow>
        <boxGeometry args={[width, 0.08, 0.85]} />
        <meshStandardMaterial color={wood} flatShading />
      </mesh>
      {[-1, 1].flatMap((sx) =>
        [-1, 1].map((sz) => (
          <mesh key={`${sx}${sz}`} position={[sx * (width / 2 - 0.08), 0.36, sz * 0.34]} castShadow>
            <boxGeometry args={[0.08, 0.72, 0.08]} />
            <meshStandardMaterial color={wood} flatShading />
          </mesh>
        )),
      )}
      {/* modesty panel */}
      <mesh position={[0, 0.45, -0.38]}>
        <boxGeometry args={[width - 0.2, 0.55, 0.04]} />
        <meshStandardMaterial color={wood} flatShading />
      </mesh>

      {/* CRT monitor */}
      <group position={[0, 0.8, -0.12]}>
        <mesh position={[0, 0.3, -0.08]} castShadow>
          <boxGeometry args={[0.62, 0.5, 0.5]} />
          <meshStandardMaterial color="#d9d2bd" flatShading />
        </mesh>
        <mesh position={[0, 0.3, 0.175]}>
          <planeGeometry args={[0.5, 0.38]} />
          <meshStandardMaterial ref={screen} color="#111" emissive={SCREEN_COLORS[status]} emissiveIntensity={0.9} />
        </mesh>
        <mesh position={[0, 0.03, -0.05]}>
          <boxGeometry args={[0.3, 0.06, 0.3]} />
          <meshStandardMaterial color="#cfc8b2" flatShading />
        </mesh>
      </group>

      {/* keyboard, mug and papers */}
      <mesh position={[0, 0.82, 0.24]} castShadow>
        <boxGeometry args={[0.5, 0.04, 0.16]} />
        <meshStandardMaterial color="#e6e0cc" flatShading />
      </mesh>
      <mesh position={[width / 2 - 0.25, 0.87, 0.15]} castShadow>
        <cylinderGeometry args={[0.06, 0.06, 0.14, 8]} />
        <meshStandardMaterial color={big ? "#ffd23f" : "#e74c3c"} flatShading />
      </mesh>
      <mesh position={[-width / 2 + 0.3, 0.82, 0.05]} rotation={[0, 0.3, 0]}>
        <boxGeometry args={[0.3, 0.04, 0.38]} />
        <meshStandardMaterial color="#fafafa" flatShading />
      </mesh>
      {big && (
        <mesh position={[0.55, 0.86, 0.3]}>
          <boxGeometry args={[0.45, 0.12, 0.06]} />
          <meshStandardMaterial color="#c9a227" metalness={0.5} roughness={0.4} />
        </mesh>
      )}
    </group>
  );
}
