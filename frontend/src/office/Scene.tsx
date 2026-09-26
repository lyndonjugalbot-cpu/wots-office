import { OrbitControls, OrthographicCamera } from "@react-three/drei";
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import type { OfficeAgent } from "../types";
import { Desk } from "./Desk";
import { LabelProjector } from "./labels";
import { deskLayout, walkMap } from "./layout";
import { Room } from "./Room";
import { castFor, loadCast, type Cast } from "./people";
import { Worker } from "./Worker";

interface Props {
  agents: OfficeAgent[];
  board: { title: string; lines: string[]; footer: string };
  selected: string | null;
  onSelect: (id: string | null) => void;
  autoRotate: boolean;
}

export function Scene({ agents, board, selected, onSelect, autoRotate }: Props) {
  const { spots, frontZ } = deskLayout(agents);
  // Realistic people if the Mixamo cast is installed (undefined while loading, null if not)
  const [cast, setCast] = useState<Cast | null | undefined>(undefined);
  useEffect(() => {
    loadCast().then(setCast);
  }, []);
  const ids = agents.map((a) => a.id).join(",");
  const roles = useMemo(() => castFor(ids ? ids.split(",") : [], cast ?? null), [ids, cast]);
  const map = walkMap(spots, frontZ);
  const centerZ = (frontZ - 6) / 2;

  return (
    <Canvas
      shadows="soft"
      className="office-canvas"
      dpr={[1, 2]}
      gl={{ antialias: true, toneMappingExposure: 0.92 }}
      onPointerMissed={() => onSelect(null)}
    >
      <color attach="background" args={["#e9edf2"]} />
      <fog attach="fog" args={["#e9edf2", 40, 90]} />
      <OrthographicCamera makeDefault position={[16, 15, 16 + centerZ]} zoom={36} near={1} far={90} />
      <OrbitControls
        target={[0, 0.8, centerZ]}
        enableDamping
        minZoom={18}
        maxZoom={110}
        maxPolarAngle={Math.PI / 2.25}
        autoRotate={autoRotate}
        autoRotateSpeed={0.6}
      />

      {/* bright, soft daylight: sky fill, a warm sun through the windows, a cool bounce */}
      <ambientLight intensity={0.3} color="#ffffff" />
      <hemisphereLight args={["#f4f8ff", "#cdbca5", 0.65]} />
      <directionalLight
        position={[-10, 16, 8]}
        intensity={1.65}
        color="#fff4e2"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-radius={6}
        shadow-camera-left={-14}
        shadow-camera-right={14}
        shadow-camera-top={14}
        shadow-camera-bottom={-14}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
      />
      <directionalLight position={[12, 8, -6]} intensity={0.35} color="#dfe8ff" />

      <FitZoom depth={frontZ + 6} />
      <Room frontZ={frontZ} board={board} />

      {agents.map((member) => {
        const spot = spots[member.id];
        if (!spot) return null;
        return (
          <group key={member.id} position={spot.position}>
            <group rotation={[0, Math.PI, 0]}>
              <Desk status={member.status} big={spot.big} />
            </group>
            {cast !== undefined && (
              <Worker member={member} character={roles[member.id]} clips={cast?.clips} home={spot.position} map={map}
                selected={selected === member.id} onSelect={onSelect} />
            )}
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
