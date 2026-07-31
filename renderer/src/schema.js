// Shape of the props the composition accepts, and the one derived value that
// everything else depends on.
//
// DURATION COMES FROM REAL AUDIO LENGTH, never from estimated_seconds. A 20.9s
// video was once rendered over 24.5s of narration and silently truncated the
// final sentence. Scripts run from ~15 to 110 seconds, so this is derived per
// render and never a constant.

export const FPS = 30;
export const WIDTH = 1080;
export const HEIGHT = 1920;

// A short tail so the last word does not end flush with the final frame.
export const TAIL_SECONDS = 0.6;

export const durationInFrames = ({durationSeconds}) =>
  Math.ceil((durationSeconds + TAIL_SECONDS) * FPS);

export const defaultProps = {
  durationSeconds: 10,
  audioSrc: null,
  title: '',
  // Persistent on-screen overlay. Written by draft, verified by check, never
  // spoken — distinct from `title`, which is the source badge.
  caption: '',
  lines: [],
  images: [],
  // One treatment per beat kind — the visual layer is selected by what the
  // beat DOES, not by whether a photo happens to exist.
  beatKinds: [],
  // Named speaker for the pull-quote card, from the report's quote slot.
  speaker: null,
};
