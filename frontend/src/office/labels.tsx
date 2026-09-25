import { useFrame } from "@react-three/fiber";
import { Vector3 } from "three";

/**
 * Name tags and speech bubbles are ordinary DOM elements in one overlay (see ui/Labels.tsx).
 * Each has a world-space anchor here, and <LabelProjector> moves it to the matching screen
 * position every frame by writing its transform directly, with no React re-render.
 *
 * Anchors outlive React's ref callbacks: a worker walking around the office keeps moving its
 * anchor with moveLabel(), and a re-render mustn't snap the label back to the desk.
 */
const worlds = new Map<string, Vector3>();
const elements = new Map<string, HTMLElement>();
const scratch = new Vector3();

export function registerLabel(key: string, world: [number, number, number], el: HTMLElement | null) {
  if (!worlds.has(key)) worlds.set(key, new Vector3(...world));
  if (el) elements.set(key, el);
  else elements.delete(key);
}

export function moveLabel(key: string, x: number, y: number, z: number) {
  const world = worlds.get(key);
  if (world) world.set(x, y, z);
  else worlds.set(key, new Vector3(x, y, z));
}

/** Mount after the workers so it projects the positions they set this frame. */
export function LabelProjector() {
  useFrame(({ camera, size }) => {
    for (const [key, el] of elements) {
      const world = worlds.get(key);
      if (!world) continue;
      scratch.copy(world).project(camera);
      const x = ((scratch.x + 1) / 2) * size.width;
      const y = ((1 - scratch.y) / 2) * size.height;
      el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
      el.style.visibility = scratch.z > 1 ? "hidden" : "visible";
    }
  });
  return null;
}
