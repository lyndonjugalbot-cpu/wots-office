import { useFrame } from "@react-three/fiber";
import { Vector3 } from "three";

/**
 * Name tags and speech bubbles are ordinary DOM elements in one overlay (see ui/Labels.tsx).
 * Each registers a world-space anchor here, and <LabelProjector> moves it to the matching
 * screen position every frame by writing its transform directly, with no React re-render.
 */
interface Anchor {
  world: Vector3;
  el: HTMLElement;
}

const anchors = new Map<string, Anchor>();
const scratch = new Vector3();

export function registerLabel(key: string, world: [number, number, number], el: HTMLElement | null) {
  if (el) anchors.set(key, { world: new Vector3(...world), el });
  else anchors.delete(key);
}

export function LabelProjector() {
  useFrame(({ camera, size }) => {
    for (const { world, el } of anchors.values()) {
      scratch.copy(world).project(camera);
      const x = ((scratch.x + 1) / 2) * size.width;
      const y = ((1 - scratch.y) / 2) * size.height;
      el.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
      el.style.visibility = scratch.z > 1 ? "hidden" : "visible";
    }
  });
  return null;
}
