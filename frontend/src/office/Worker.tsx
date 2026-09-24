import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { useRef, useState, type RefObject } from "react";
import type { Group, Mesh } from "three";
import type { OfficeAgent } from "../types";
import { STATUS_COLORS, looks } from "./layout";

interface Props {
  member: OfficeAgent;
  selected: boolean;
  onSelect: (id: string) => void;
}

/** A seated low-poly office worker with a spinning status gem. Labels live in ui/Labels.tsx. */
export function Worker({ member, selected, onSelect }: Props) {
  const agent = member;
  const body = useRef<Group>(null);
  const head = useRef<Group>(null);
  const leftArm = useRef<Group>(null);
  const rightArm = useRef<Group>(null);
  const gem = useRef<Mesh>(null);
  const [hovered, setHovered] = useState(false);
  const { skin, hair, longHair } = looks(member.id);
  const shirt = member.color;
  const isCeo = member.kind === "ceo";

  useFrame(({ clock }) => {
    const t = clock.elapsedTime + member.id.length;
    if (gem.current) {
      gem.current.rotation.y = t * 1.6;
      gem.current.position.y = 2.25 + Math.sin(t * 2) * 0.05;
    }
    if (!body.current || !head.current || !leftArm.current || !rightArm.current) return;
    const s = agent.status;
    // Typing when working, a little celebration when done, slumped when stuck
    const typing = s === "working" ? Math.sin(t * 18) * 0.18 : 0;
    // Positive x-rotation swings the arms forward (-z) toward the keyboard; done raises them overhead
    leftArm.current.rotation.x = 1.2 + typing + (s === "done" ? 1.6 + Math.sin(t * 6) * 0.2 : 0);
    rightArm.current.rotation.x = 1.2 - typing + (s === "done" ? 1.6 - Math.sin(t * 6) * 0.2 : 0);
    head.current.rotation.y = s === "waiting" ? Math.sin(t * 1.5) * 0.5 : s === "working" ? Math.sin(t * 0.7) * 0.08 : 0;
    head.current.rotation.x = s === "error" ? -0.45 : s === "working" ? -0.05 : 0;
    body.current.position.y = s === "done" ? Math.abs(Math.sin(t * 5)) * 0.08 : Math.sin(t * 1.4) * 0.01;
  });

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect(member.id);
  };

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
      {/* Office chair */}
      <group position={[0, 0, 0.1]}>
        <mesh position={[0, 0.48, 0]} castShadow>
          <boxGeometry args={[0.62, 0.1, 0.58]} />
          <meshStandardMaterial color={isCeo ? "#7a1f1f" : "#3b3f4a"} flatShading />
        </mesh>
        <mesh position={[0, 0.95, 0.3]} castShadow>
          <boxGeometry args={[0.62, isCeo ? 1.05 : 0.8, 0.1]} />
          <meshStandardMaterial color={isCeo ? "#7a1f1f" : "#3b3f4a"} flatShading />
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

      {/* Person, sitting and facing the desk (-z) */}
      <group ref={body} position={[0, 0, 0.05]}>
        {/* legs */}
        {[-0.13, 0.13].map((x) => (
          <group key={x}>
            <mesh position={[x, 0.58, -0.2]} castShadow>
              <boxGeometry args={[0.2, 0.18, 0.5]} />
              <meshStandardMaterial color="#2f3b59" flatShading />
            </mesh>
            <mesh position={[x, 0.3, -0.44]} castShadow>
              <boxGeometry args={[0.18, 0.5, 0.18]} />
              <meshStandardMaterial color="#2f3b59" flatShading />
            </mesh>
            <mesh position={[x, 0.06, -0.5]}>
              <boxGeometry args={[0.2, 0.1, 0.3]} />
              <meshStandardMaterial color="#1b1b1b" flatShading />
            </mesh>
          </group>
        ))}
        {/* torso */}
        <mesh position={[0, 0.93, 0.02]} castShadow>
          <boxGeometry args={[0.5, 0.6, 0.3]} />
          <meshStandardMaterial color={shirt} flatShading />
        </mesh>
        {isCeo && (
          <mesh position={[0, 0.95, -0.14]}>
            <boxGeometry args={[0.08, 0.4, 0.02]} />
            <meshStandardMaterial color="#b3001b" />
          </mesh>
        )}
        {/* arms reach forward to the keyboard */}
        {[
          [leftArm, -0.31],
          [rightArm, 0.31],
        ].map(([ref, x]) => (
          <group key={x as number} ref={ref as RefObject<Group>} position={[x as number, 1.15, 0.02]}>
            <mesh position={[0, -0.24, 0]} castShadow>
              <boxGeometry args={[0.13, 0.48, 0.14]} />
              <meshStandardMaterial color={shirt} flatShading />
            </mesh>
            <mesh position={[0, -0.52, 0]}>
              <boxGeometry args={[0.11, 0.1, 0.11]} />
              <meshStandardMaterial color={skin} flatShading />
            </mesh>
          </group>
        ))}
        {/* head */}
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

      {/* Status gem */}
      <mesh ref={gem} position={[0, 2.25, 0]} scale={[1, 1.7, 1]}>
        <octahedronGeometry args={[0.14, 0]} />
        <meshStandardMaterial
          color={STATUS_COLORS[agent.status]}
          emissive={STATUS_COLORS[agent.status]}
          emissiveIntensity={0.55}
          flatShading
        />
      </mesh>

      {/* Selection diamond on the floor */}
      {(selected || hovered) && (
        <mesh position={[0, 0.06, -0.1]} rotation={[-Math.PI / 2, 0, Math.PI / 4]}>
          <ringGeometry args={[0.85, 1.0, 4]} />
          <meshBasicMaterial color={selected ? "#39d353" : "#ffffff"} transparent opacity={selected ? 0.9 : 0.45} />
        </mesh>
      )}

    </group>
  );
}
