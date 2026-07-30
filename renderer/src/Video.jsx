import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile} from 'remotion';
import {FPS} from './schema.js';
import {Subtitles} from './Subtitles.jsx';
import {ImageTreatment, TypographicTreatment} from './Treatments.jsx';

// ONE composition. The narration is mounted ONCE at frame 0 as a single
// continuous track — scenes are timed to match it, never the reverse. Cutting
// audio per scene would reintroduce the stitching artefacts the one-request
// synthesis exists to avoid.

const resolveAudio = (audioSrc) => {
  if (!audioSrc) return null;
  if (/^https?:\/\//.test(audioSrc)) return audioSrc;
  return staticFile(audioSrc);
};

export const NewsVideo = ({durationSeconds, audioSrc, lines, images, title}) => {
  const audio = resolveAudio(audioSrc);

  return (
    <AbsoluteFill style={{backgroundColor: '#05070d'}}>
      {audio ? <Audio src={audio} /> : null}

      {lines.map((line, index) => {
        const from = Math.round(line.start_seconds * FPS);
        // Hold each scene until the next line begins so there is never a gap;
        // the last scene runs to the end of the audio.
        const nextStart =
          index + 1 < lines.length
            ? lines[index + 1].start_seconds
            : durationSeconds;
        const frames = Math.max(1, Math.round((nextStart - line.start_seconds) * FPS));
        const image = images[index] ?? null;

        return (
          <Sequence key={index} from={from} durationInFrames={frames} layout="none">
            {image ? (
              <ImageTreatment
                image={image}
                lineIndex={index}
                durationInFrames={frames}
              />
            ) : (
              <TypographicTreatment
                line={line}
                lineIndex={index}
                durationInFrames={frames}
              />
            )}
          </Sequence>
        );
      })}

      {/* Subtitles ride on the absolute timeline, not inside a scene, so a
          word never gets clipped by a scene boundary. */}
      {lines.map((line, index) => {
        const from = Math.round(line.start_seconds * FPS);
        const last = line.words[line.words.length - 1];
        const end = last ? last.end_seconds : line.start_seconds + line.duration_seconds;
        const frames = Math.max(1, Math.round((end - line.start_seconds) * FPS) + 6);
        return (
          <Sequence key={`sub-${index}`} from={from} durationInFrames={frames} layout="none">
            <SubtitleAtOffset line={line} offsetSeconds={line.start_seconds} />
          </Sequence>
        );
      })}

      {title ? (
        <div
          style={{
            position: 'absolute',
            top: 96,
            left: 72,
            right: 72,
            fontFamily: 'Inter, Helvetica, Arial, sans-serif',
            fontWeight: 700,
            fontSize: 34,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.62)',
            textAlign: 'center',
          }}
        >
          {title}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

// Word timings are absolute (seconds from the start of the audio) but a
// Sequence resets useCurrentFrame to 0. Shift them back so each word lights up
// at its real time.
const SubtitleAtOffset = ({line, offsetSeconds}) => {
  const shifted = {
    ...line,
    words: line.words.map((w) => ({
      ...w,
      start_seconds: w.start_seconds - offsetSeconds,
      end_seconds: w.end_seconds - offsetSeconds,
    })),
  };
  return <Subtitles line={shifted} />;
};
