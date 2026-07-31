import React from 'react';
import {useCurrentFrame} from 'remotion';
import {FPS} from './schema.js';
import {SAFE} from './layout.js';

// Word-by-word, driven entirely by the words array from the ElevenLabs
// alignment. NEVER estimated from character counts — real per-word timings
// are in the payload and are the only ones that stay in sync.

// Long lines — a full quotation, say — must shrink rather than grow up the
// frame and collide with the treatment above them.
const subtitleFontSize = (wordCount) => {
  if (wordCount <= 8) return 68;
  if (wordCount <= 14) return 58;
  if (wordCount <= 22) return 48;
  return 42;
};

export const Subtitles = ({line}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const fontSize = subtitleFontSize(line.words.length);

  return (
    <div
      style={{
        position: 'absolute',
        left: SAFE.left,
        right: SAFE.right,
        // Inside the bottom safe margin: platform UI covers the lower edge,
        // and the treatments above stop at HEIGHT - SAFE.bottom.
        bottom: SAFE.bottom - 200,
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        gap: '0 18px',
        fontFamily: 'Inter, Helvetica, Arial, sans-serif',
        fontWeight: 800,
        fontSize,
        lineHeight: 1.18,
        textAlign: 'center',
      }}
    >
      {line.words.map((word, i) => {
        const spoken = t >= word.start_seconds;
        const active = spoken && t < word.end_seconds;
        return (
          <span
            key={`${i}-${word.text}`}
            style={{
              color: spoken ? '#ffffff' : 'rgba(255,255,255,0.35)',
              transform: `scale(${active ? 1.06 : 1})`,
              transition: 'none',
              textShadow: '0 4px 24px rgba(0,0,0,0.85), 0 2px 6px rgba(0,0,0,0.9)',
              display: 'inline-block',
            }}
          >
            {word.text}
          </span>
        );
      })}
    </div>
  );
};
