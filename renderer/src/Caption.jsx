import React from 'react';
import {
  ADVANCE, CAPTION_MAX_LINES, CAPTION_TOP, FONT, SAFE, SAFE_WIDTH, wrapWords,
} from './layout.js';

// The persistent overlay: on screen for the FULL duration, below the source
// badge. It is the part a viewer screenshots, so it is plated for contrast
// rather than relying on a text shadow over whatever photo is behind it.

const MAX_SIZE = 86;
const MIN_SIZE = 54;

// Largest size at which the caption fits CAPTION_MAX_LINES inside the safe
// width. Returns null when even MIN_SIZE will not fit — the caller fails the
// render rather than shipping a clipped or hyphenated headline.
export const fitCaption = (caption, {maxWidth = SAFE_WIDTH,
                                     maxLines = CAPTION_MAX_LINES} = {}) => {
  const text = String(caption || '').trim();
  if (!text) return null;
  for (let size = MAX_SIZE; size >= MIN_SIZE; size -= 2) {
    const charsPerLine = Math.floor(maxWidth / (size * ADVANCE));
    if (charsPerLine < 4) continue;
    const lines = wrapWords(text, charsPerLine);
    // A single word longer than a whole line can never be wrapped, and
    // hyphenating it is not an option we allow.
    const longest = Math.max(...lines.map((l) => l.length));
    if (lines.length <= maxLines && longest <= charsPerLine) {
      return {size, lines};
    }
  }
  return null;
};

export const Caption = ({caption}) => {
  const fitted = fitCaption(caption);
  if (!fitted) return null;   // build_props refuses before we get here

  return (
    <div style={{
      position: 'absolute',
      left: SAFE.left,
      right: SAFE.right,
      top: CAPTION_TOP,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 8,
    }}>
      {fitted.lines.map((line, i) => (
        <div key={i} style={{
          // A near-solid plate, so the caption survives any backdrop.
          background: 'rgba(5,7,13,0.86)',
          padding: '10px 26px',
          borderRadius: 10,
          fontFamily: FONT,
          fontWeight: 900,
          fontSize: fitted.size,
          lineHeight: 1.12,
          letterSpacing: '-0.02em',
          color: '#ffffff',
          textAlign: 'center',
          whiteSpace: 'nowrap',
        }}>
          {line}
        </div>
      ))}
    </div>
  );
};
