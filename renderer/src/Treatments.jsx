import React from 'react';
import {Img, interpolate, useCurrentFrame} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './schema.js';

// Two treatments, chosen by whether the line has a source image. There is no
// stock library and no fallback selection logic: a line either has a real
// image from the article or it is typographic.

const PAN_TO = 1.08;

// Anything wider than 4:3 is a landscape crop: fill the frame width and
// anchor on the upper third, where faces and product shots sit. Squarer
// images fill the height instead.
const isWide = (image) => image.width / image.height > 4 / 3;

export const ImageTreatment = ({image, lineIndex, durationInFrames}) => {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, Math.max(durationInFrames, 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Alternate pan direction between consecutive lines so a multi-image
  // sequence does not drift the same way each time.
  const forward = lineIndex % 2 === 0;
  const scale = forward
    ? interpolate(progress, [0, 1], [1, PAN_TO])
    : interpolate(progress, [0, 1], [PAN_TO, 1]);
  const drift = interpolate(progress, [0, 1], forward ? [0, -28] : [-28, 0]);

  const wide = isWide(image);

  return (
    <div style={{position: 'absolute', inset: 0, overflow: 'hidden', background: '#05070d'}}>
      <Img
        src={image.url}
        style={{
          position: 'absolute',
          left: '50%',
          // Upper-third anchor for landscape crops.
          top: wide ? '33%' : '50%',
          transform:
            `translate(-50%, -50%) translateY(${drift}px) scale(${scale})`,
          width: wide ? WIDTH : 'auto',
          height: wide ? 'auto' : HEIGHT,
          minWidth: wide ? WIDTH : undefined,
          minHeight: wide ? undefined : HEIGHT,
          objectFit: 'cover',
        }}
      />
      {/* Legibility scrim behind the subtitles. */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'linear-gradient(to bottom, rgba(5,7,13,0.55) 0%, rgba(5,7,13,0.1) 35%, rgba(5,7,13,0.75) 78%, rgba(5,7,13,0.95) 100%)',
        }}
      />
    </div>
  );
};

// The key number or phrase, set large. Picks the most concrete token in the
// line: a figure if there is one, otherwise the longest word.
export const keyPhrase = (text) => {
  const numeric = text.match(/\$?\d[\d,.]*%?(?:\s?(?:billion|million|thousand|percent))?/i);
  if (numeric) return numeric[0].trim();
  const words = text.replace(/[^\w\s$%.-]/g, ' ').split(/\s+/).filter(Boolean);
  if (!words.length) return text.slice(0, 18);
  return words.reduce((a, b) => (b.length > a.length ? b : a), '');
};

// Size the phrase to the frame rather than to a fixed constant: "278" and
// "breakthroughs" are the same slot, and a fixed size overflowed the frame
// edges on the long one.
const PHRASE_PADDING = 120;
export const phraseFontSize = (phrase) => {
  const usable = WIDTH - PHRASE_PADDING;
  // ~0.58em average advance width for this weight, measured against renders.
  const fitted = usable / (Math.max(phrase.length, 1) * 0.58);
  return Math.max(64, Math.min(230, fitted));
};

export const TypographicTreatment = ({line, lineIndex, durationInFrames}) => {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, Math.max(durationInFrames, 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const phrase = keyPhrase(line.text);

  const entry = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const scale = interpolate(progress, [0, 1], [1, 1.06]);
  const tilt = lineIndex % 2 === 0 ? -2 : 2;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        background:
          lineIndex % 2 === 0
            ? 'radial-gradient(circle at 30% 28%, #17233d 0%, #05070d 68%)'
            : 'radial-gradient(circle at 70% 30%, #1d1830 0%, #05070d 68%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          transform: `translateY(-300px) scale(${scale * (0.94 + entry * 0.06)}) rotate(${tilt}deg)`,
          opacity: entry,
          fontFamily: 'Inter, Helvetica, Arial, sans-serif',
          fontWeight: 900,
          fontSize: phraseFontSize(phrase),
          letterSpacing: '-0.03em',
          color: '#ffffff',
          textAlign: 'center',
          padding: `0 ${PHRASE_PADDING / 2}px`,
          lineHeight: 0.95,
          maxWidth: WIDTH,
          textShadow: '0 20px 70px rgba(0,0,0,0.6)',
        }}
      >
        {phrase}
      </div>
    </div>
  );
};
