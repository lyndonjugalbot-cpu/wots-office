import { OrbitControls, OrthographicCamera } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect } from "react";
import type { OfficeAgent } from "../types";
import { Desk } from "./Desk";
import { LabelProjector } from "./labels";
import { deskLayout, walkMap } from "./layout";
import { Room } from "./Room";
import { Worker } from "./Worker";

interface Props {
  agents: OfficeAgent[];
  board: { title: string; lines: string[]; footer: string };
  selected: string | null;
  onSelect: (id: string | null) => void;
  retro: boolean;
  autoRotate: boolean;
}

export function Scene({ agents, board, selected, onSelect, retro, autoRotate }: Props) {
  const { spots, frontZ } = deskLayout(agents);
  const map = walkMap(spots, frontZ);
  const centerZ = (frontZ - 6) / 2;

  return (
    <Canvas
      shadows="percentage"
      className={retro ? "office-canvas office-canvas--retro" : "office-canvas"}
      // Rendering at a low resolution and scaling up with pixelated CSS gives the retro look
      dpr={retro ? 0.5 : [1, 2]}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={["#2a2140"]} />
      <OrthographicCamera makeDefault position={[16, 15, 16 + centerZ]} zoom={36} near={-100} far={200} />
      <OrbitControls
        target={[0, 0.8, centerZ]}
        enableDamping
        minZoom={18}
        maxZoom={110}
        maxPolarAngle={Math.PI / 2.25}
        autoRotate={autoRotate}
        autoRotateSpeed={0.6}
      />

      <ambientLight intensity={0.55} color="#fff4e0" />
      <hemisphereLight args={["#fff8e7", "#6b4f3a", 0.5]} />
      <directionalLight
        position={[9, 16, 10]}
        intensity={1.6}
        color="#fff1d6"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={14}
        shadow-camera-bottom={-14}
        shadow-bias={-0.0005}
      />

      <FitZoom depth={frontZ + 6} />
      <Room frontZ={frontZ} board={board} />

      {agents.map((member) => {
        const spot = spots[member.id];
        if (!spot) return null;
        return (
          <group key={member.id} position={spot.position}>
            <Desk status={member.status} big={spot.big} />
            <Worker member={member} home={spot.position} map={map} selected={selected === member.id} onSelect={onSelect} />
          </group>
        );
      })}
      {/* After the workers, so labels use the positions they set this frame */}
      <LabelProjector />
    </Canvas>
  );
}

/** Pick a starting zoom that fits the whole room on screen, from phones to wide monitors. */
function FitZoom({ depth }: { depth: number }) {
  const camera = useThree((s) => s.camera);
  const { width, height } = useThree((s) => s.size);
  useEffect(() => {
    // The isometric room spans roughly (room width + depth) world units across the screen
    camera.zoom = Math.max(12, Math.min(40, width / (17 + depth * 0.55), height / (8 + depth * 0.45)));
    camera.updateProjectionMatrix();
  }, [camera, width, height, depth]);
  return null;
}
