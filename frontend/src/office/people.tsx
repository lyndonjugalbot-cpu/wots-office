import { useFrame, useLoader } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  AnimationClip, AnimationMixer, LoopOnce, LoopRepeat, QuaternionKeyframeTrack, VectorKeyframeTrack,
  type AnimationAction, type Bone, type Group, type Object3D,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";

/**
 * Realistic office workers: Mixamo characters and animations, converted by
 * scripts/mixamo/convert_mixamo.py into frontend/public/characters/. Those files are licensed for
 * use inside this app only and aren't in git; without them the office falls back to drawn people.
 */

export type Clip = "typing" | "sit_idle" | "sit_waiting" | "sit_done" | "sit_error" | "sit_down" | "stand_up" |
  "walk" | "idle" | "drink";

export interface CastMember { id: string; file: string; height: number }
export interface Cast { characters: CastMember[]; clips: AnimationClip[] }

export const PERSON_HEIGHT = 1.75; // metres; every character is scaled to this
const BASE = "characters/";
// Clips that play once and hold their last pose (getting up, sitting down)
const ONCE = new Set<Clip>(["stand_up", "sit_down"]);

let castPromise: Promise<Cast | null> | null = null;

/** The converted cast, or null when the Mixamo files aren't installed. */
export function loadCast(): Promise<Cast | null> {
  castPromise ??= (async () => {
    try {
      const [manifest, clips] = await Promise.all([
        fetch(`${BASE}manifest.json`).then((r) => (r.ok ? r.json() : null)),
        fetch(`${BASE}animations.json`).then((r) => (r.ok ? r.json() : null)),
      ]);
      if (!manifest?.characters?.length || !Array.isArray(clips)) return null;
      return { characters: manifest.characters, clips: clips.map((c: Parameters<typeof AnimationClip.parse>[0]) => AnimationClip.parse(c)) };
    } catch {
      return null;
    }
  })();
  return castPromise;
}

// The CEO's desk is the man in the dark suit and tie (Mixamo ch33)
const CEO_CHARACTER = "ch33";

/** A stable character for everyone; the CEO is always the man in the suit and tie. */
export function castFor(ids: string[], cast: Cast | null): Record<string, CastMember> {
  if (!cast) return {};
  const ceo = cast.characters.find((c) => c.id === CEO_CHARACTER);
  const rest = cast.characters.filter((c) => c !== ceo);
  const out: Record<string, CastMember> = {};
  let i = 0;
  for (const id of ids) {
    if (id === "ceo" && ceo) out[id] = ceo;
    else out[id] = rest[i++ % rest.length];
  }
  return out;
}

const withMeshopt = (loader: GLTFLoader) => {
  loader.setMeshoptDecoder(MeshoptDecoder);
};

/**
 * Mixamo animations are made on one skeleton ("mixamorig11Hips"...); each character has its own
 * prefix and size. Rename the tracks, keep the hips' height (scaled to this body) but not their
 * forward/back travel: where people go is decided by the office, not by the animation.
 */
function retarget(clips: AnimationClip[], prefix: string, restHips: { x: number; y: number; z: number },
  sourceHipsHeight: number): Map<string, AnimationClip> {
  const ratio = restHips.y / sourceHipsHeight;
  const out = new Map<string, AnimationClip>();
  for (const clip of clips) {
    const tracks = [];
    for (const track of clip.tracks) {
      const dot = track.name.lastIndexOf(".");
      const node = track.name.slice(0, dot).replace(/^mixamorig\d*/, prefix);
      const prop = track.name.slice(dot + 1);
      if (prop === "quaternion") {
        tracks.push(new QuaternionKeyframeTrack(`${node}.quaternion`, track.times, track.values));
      } else if (prop === "position" && node.endsWith("Hips")) {
        const v = Float32Array.from(track.values);
        for (let i = 0; i < v.length; i += 3) {
          v[i] = restHips.x;
          v[i + 1] *= ratio;
          v[i + 2] = restHips.z;
        }
        tracks.push(new VectorKeyframeTrack(`${node}.position`, track.times, v));
      }
    }
    out.set(clip.name, new AnimationClip(clip.name, clip.duration, tracks));
  }
  return out;
}

export interface AnimState {
  clip: Clip;
  timeScale: number;
}

/**
 * One person. `state.current` is set by the Worker every frame; the body cross-fades to the
 * matching animation. The model faces -z, like the rest of the office's people.
 */
export function CharacterBody({ member, clips, state, shadows = true }: {
  member: CastMember; clips: AnimationClip[]; state: React.RefObject<AnimState>; shadows?: boolean;
}) {
  const gltf = useLoader(GLTFLoader, `${BASE}${member.file}`, withMeshopt);
  const root = useRef<Group>(null);
  const { model, actions, mixer } = useMemo(() => {
    const model = SkeletonUtils.clone(gltf.scene) as Object3D;
    let hips: Bone | null = null;
    model.traverse((o) => {
      if ((o as Bone).isBone && o.name.endsWith("Hips") && !hips) hips = o as Bone;
      const mesh = o as unknown as { isMesh?: boolean; isSkinnedMesh?: boolean; castShadow: boolean; frustumCulled: boolean };
      if (mesh.isMesh) {
        mesh.castShadow = shadows;
        if (mesh.isSkinnedMesh) mesh.frustumCulled = false; // animated bounds move outside the bind pose
      }
    });
    const hipBone = hips as Bone | null;
    const prefix = hipBone ? hipBone.name.slice(0, -"Hips".length) : "mixamorig";
    const rest = hipBone ? { x: hipBone.position.x, y: hipBone.position.y, z: hipBone.position.z } : { x: 0, y: 1, z: 0 };
    // The standing idle's first frame is the animations' own standing hip height
    const idle = clips.find((c) => c.name === "idle");
    const hipsTrack = idle?.tracks.find((t) => t.name.endsWith("Hips.position"));
    const sourceHeight = hipsTrack ? hipsTrack.values[1] : rest.y;
    const mixer = new AnimationMixer(model);
    const actions = new Map<string, AnimationAction>();
    for (const [name, clip] of retarget(clips, prefix, rest, sourceHeight)) {
      const action = mixer.clipAction(clip);
      if (ONCE.has(name as Clip)) {
        action.setLoop(LoopOnce, 1);
        action.clampWhenFinished = true;
      } else action.setLoop(LoopRepeat, Infinity);
      actions.set(name, action);
    }
    return { model, actions, mixer };
  }, [gltf, clips, shadows]);

  const current = useRef<string | null>(null);
  useEffect(() => () => void mixer.stopAllAction(), [mixer]);

  useFrame((_, dt) => {
    const want = state.current;
    if (want && want.clip !== current.current) {
      const next = actions.get(want.clip);
      if (next) {
        const prev = current.current ? actions.get(current.current) : null;
        next.reset().setEffectiveWeight(1).play();
        if (prev) next.crossFadeFrom(prev, 0.35, false);
        current.current = want.clip;
      }
    }
    const playing = current.current ? actions.get(current.current) : null;
    if (playing && want) playing.timeScale = want.timeScale;
    mixer.update(Math.min(dt, 0.1));
  });

  const scale = PERSON_HEIGHT / member.height;
  return (
    <group ref={root} rotation={[0, Math.PI, 0]} scale={scale}>
      <primitive object={model} />
    </group>
  );
}

/** How long a play-once clip takes, for timing the office's stand/sit steps. */
export function clipDuration(clips: AnimationClip[] | undefined, name: Clip, fallback: number): number {
  return clips?.find((c) => c.name === name)?.duration ?? fallback;
}
