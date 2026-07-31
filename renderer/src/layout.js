// Safe areas and the figure extraction the treatments share.
//
// SAFE AREAS ARE NOT DECORATION. Platform UI covers the frame edges: the
// caption, the share sheet and the progress bar all sit on top of the video.
// Text outside this box is text the viewer never reads, so every treatment
// positions against these numbers and tests/test_render.py asserts it.

import {HEIGHT, WIDTH} from './schema.js';

export const SAFE = {
  left: Math.round(WIDTH * 0.1),      // 108
  right: Math.round(WIDTH * 0.1),     // 108
  top: Math.round(HEIGHT * 0.08),     // 154
  bottom: Math.round(HEIGHT * 0.2),   // 384 — subtitles own the lower band
};

export const SAFE_WIDTH = WIDTH - SAFE.left - SAFE.right;
export const SAFE_TOP = SAFE.top;
export const SAFE_BOTTOM = HEIGHT - SAFE.bottom;
export const SAFE_HEIGHT = SAFE_BOTTOM - SAFE_TOP;

// The band the caption occupies, below the source badge. Treatments start
// below this so nothing collides with a two-line caption.
export const CAPTION_TOP = SAFE_TOP + 76;
export const CAPTION_MAX_LINES = 2;
export const STAGE_TOP = CAPTION_TOP + 250;

export const FONT = 'Inter, Helvetica, Arial, sans-serif';

// Roughly the advance width of one character as a fraction of font size, for
// this family at heavy weights. Measured against rendered frames rather than
// guessed — it is what makes wrapping predictable without measuring text.
export const ADVANCE = 0.56;

export const fitFontSize = (text, {maxWidth, max, min, lines = 1}) => {
  const perLine = Math.max(1, Math.ceil(text.length / lines));
  return Math.max(min, Math.min(max, (maxWidth / (perLine * ADVANCE))));
};

// Greedy wrap on WORD BOUNDARIES. Never hyphenates, never truncates: a
// caption that cannot fit is a failed render, not a clipped headline.
export const wrapWords = (text, charsPerLine) => {
  const words = String(text).split(/\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > charsPerLine && current) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  return lines;
};

// --- figures ---------------------------------------------------------------

// Numbers as the viewer would read them aloud, with the unit kept attached:
// "$100,000", "60 hours", "2^89", "5,000". The card shows the figure at
// display size and its trailing unit beneath, so both parts are captured.
const FIGURE_RE =
  /(\$?\d[\d,]*(?:\.\d+)?)\s*(%|percent|billion|million|thousand|hours?|days?|years?|minutes?|seconds?|x)?/gi;

export const figures = (text) => {
  const out = [];
  for (const match of String(text).matchAll(FIGURE_RE)) {
    const [, value, unit] = match;
    if (!value) continue;
    const numeric = Number(value.replace(/[$,]/g, ''));
    if (!Number.isFinite(numeric)) continue;
    out.push({value, unit: unit || '', numeric, raw: match[0].trim()});
  }
  return out;
};

// The figure a proof card should lead with: the largest, since that is the
// one carrying the weight of the sentence.
export const leadFigure = (text) => {
  const all = figures(text);
  if (!all.length) return null;
  return all.reduce((a, b) => (b.numeric > a.numeric ? b : a));
};

// Two figures where one is a subset of the other — the funnel. Returns them
// LARGER FIRST, which is the ordering draft.py enforces in the writing and
// the reason the drop reads as attrition.
//
// THE UNITS MUST MATCH. Taking the two largest numbers regardless of unit drew
// "$100,000" against "60 hours" as proportional bars — a chart comparing money
// to time, which is not wrong-looking so much as meaningless. draft.py's own
// definition of a funnel is two numbers SHARING A UNIT where one counts a
// subset of the other; the renderer must apply the same test or it invents a
// relationship the script never claimed.
const UNIT_CLASS = (unit) => {
  const u = (unit || '').toLowerCase().replace(/s$/, '');
  if (['hour', 'day', 'year', 'minute', 'second'].includes(u)) return 'time';
  if (['%', 'percent'].includes(u)) return 'percent';
  if (['billion', 'million', 'thousand'].includes(u)) return 'scale';
  return u || 'count';
};

export const funnelPair = (text) => {
  const all = figures(text);
  if (all.length < 2) return null;
  const sorted = [...all].sort((a, b) => b.numeric - a.numeric);
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length; j++) {
      const big = sorted[i];
      const small = sorted[j];
      if (big.numeric <= small.numeric) continue;
      const bigMoney = big.value.includes('$');
      const smallMoney = small.value.includes('$');
      if (bigMoney !== smallMoney) continue;
      if (UNIT_CLASS(big.unit) !== UNIT_CLASS(small.unit)) continue;
      return {big, small, ratio: small.numeric / big.numeric};
    }
  }
  return null;
};

// A beat that says "halved" or "doubled" is a funnel with no second number
// written out. Deriving the implied pair keeps the treatment meaningful
// instead of degrading to a plain number card.
const IMPLIED = [
  {re: /\bhalv(?:ed|es|ing)\b/i, ratio: 0.5, label: 'half'},
  {re: /\bdoubl(?:ed|es|ing)\b/i, ratio: 2, label: 'double'},
  {re: /\bquarter(?:ed)?\b/i, ratio: 0.25, label: 'a quarter'},
  {re: /\btripl(?:ed|es)\b/i, ratio: 3, label: 'triple'},
];

export const impliedRatio = (text) => {
  for (const {re, ratio, label} of IMPLIED) {
    if (re.test(text)) return {ratio, label};
  }
  return null;
};
