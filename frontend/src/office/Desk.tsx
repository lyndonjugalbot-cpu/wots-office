import { RoundedBox } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import type { MeshStandardMaterial } from "three";
import type { AgentStatus } from "../types";
import { SCREEN_COLORS } from "./layout";

const BLACK = "#1f2328";

/** A modern desk: white top with an oak edge, slim black legs, a flat monitor whose screen shows the status. */
export function Desk({ status, big }: { status: AgentStatus; big: boolean }) {
  const screen = useRef<MeshStandardMaterial>(null);
  const width = big ? 2.3 : 1.7;

  useFrame(({ clock }) => {
    if (!screen.current) return;
    const t = clock.elapsedTime;
    // Busy screens shimmer gently; idle ones sit on a dim screensaver
    screen.current.emissiveIntensity =
      status === "working" ? 0.75 + Math.sin(t * 6) * 0.05 : status === "idle" ? 0.25 + Math.sin(t * 0.8) * 0.05 : 0.8;
  });

  return (
    <group position={[0, 0, -0.75]}>
      {/* top: oak slab under a white surface */}
      <RoundedBox args={[width, 0.05, 0.85]} radius={0.02} smoothness={3} position={[0, 0.735, 0]} castShadow receiveShadow>
        <meshStandardMaterial color="#c9a27a" roughness={0.5} />
      </RoundedBox>
      <RoundedBox args={[width - 0.02, 0.02, 0.83]} radius={0.01} smoothness={2} position={[0, 0.765, 0]} receiveShadow>
        <meshStandardMaterial color={big ? "#2b2f36" : "#fbfaf8"} roughness={0.35} />
      </RoundedBox>
      {/* slim steel legs */}
      {[-1, 1].flatMap((sx) =>
        [-1, 1].map((sz) => (
          <mesh key={`${sx}${sz}`} position={[sx * (width / 2 - 0.1), 0.36, sz * 0.33]} castShadow>
            <cylinderGeometry args={[0.022, 0.022, 0.72, 12]} />
            <meshStandardMaterial color={BLACK} metalness={0.5} roughness={0.35} />
          </mesh>
        )),
      )}

      {/* flat monitor on a stand */}
      <group position={[0, 0.775, -0.18]}>
        <RoundedBox args={[0.22, 0.015, 0.16]} radius={0.006} smoothness={2} position={[0, 0.01, 0]}>
          <meshStandardMaterial color="#c9ccd1" metalness={0.8} roughness={0.25} />
        </RoundedBox>
        <mesh position={[0, 0.17, -0.03]}>
          <boxGeometry args={[0.04, 0.3, 0.02]} />
          <meshStandardMaterial color="#c9ccd1" metalness={0.8} roughness={0.25} />
        </mesh>
        <RoundedBox args={[0.78, 0.46, 0.035]} radius={0.015} smoothness={3} position={[0, 0.42, 0]} castShadow>
          <meshStandardMaterial color="#15171b" roughness={0.3} />
        </RoundedBox>
        <mesh position={[0, 0.425, 0.019]}>
          <planeGeometry args={[0.73, 0.41]} />
          <meshStandardMaterial ref={screen} color="#0c0f14" emissive={SCREEN_COLORS[status]} emissiveIntensity={0.8}
            roughness={0.2} />
        </mesh>
      </group>

      {/* keyboard, mouse, mug and a notebook */}
      <RoundedBox args={[0.46, 0.02, 0.14]} radius={0.008} smoothness={2} position={[0, 0.785, 0.2]} castShadow>
        <meshStandardMaterial color="#e9e9ec" roughness={0.4} />
      </RoundedBox>
      <mesh position={[0.36, 0.785, 0.22]} scale={[1, 0.45, 1.4]} castShadow>
        <sphereGeometry args={[0.04, 16, 12]} />
        <meshStandardMaterial color="#e9e9ec" roughness={0.4} />
      </mesh>
      <mesh position={[width / 2 - 0.22, 0.825, 0.12]} castShadow>
        <cylinderGeometry args={[0.05, 0.045, 0.11, 20]} />
        <meshStandardMaterial color={big ? "#c9a227" : "#ffffff"} roughness={0.3} metalness={big ? 0.5 : 0} />
      </mesh>
      <RoundedBox args={[0.24, 0.02, 0.32]} radius={0.008} smoothness={2} position={[-width / 2 + 0.28, 0.785, 0.05]}
        rotation={[0, 0.25, 0]} castShadow>
        <meshStandardMaterial color={big ? "#8b2e2e" : "#3d5a80"} roughness={0.7} />
      </RoundedBox>
    </group>
  );
}
