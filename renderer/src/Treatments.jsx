import React from 'react';
import {Img, interpolate, useCurrentFrame} from 'remotion';
import {FPS, HEIGHT, WIDTH} from './schema.js';
import {
  ADVANCE, FONT, SAFE, SAFE_WIDTH, STAGE_TOP,
  figures, funnelPair, impliedRatio, leadFigure, wrapWords,
} from './layout.js';

// ONE TREATMENT PER BEAT KIND. The old binary — source image, else big word —
// was designed when sources were Hugging Face posts carrying diagrams and
// benchmark charts. Journalism articles carry one decorative hero image, so
// that design starved: a single Getty photo stretched across a whole video.
//
// The richest material is already in report.json. "5,000 applied, 278 funded"
// IS a chart, and a grounded chart beats a stock photo of a server rack.
//
// EVERY TREATMENT WORKS WITH NO IMAGE AT ALL. That is the common case, not the
// fallback: type and shape carry all six, and a photo is only ever a darkened
// backdrop.

const ease = (t) => 1 - Math.pow(1 - t, 3);

const progressOf = (frame, durationInFrames) =>
  interpolate(frame, [0, Math.max(durationInFrames, 1)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

// SUB-BEAT MOTION. Attention resets every 3-4 seconds in this format, and a
// 20-word quote beat held one static card for ten. Every treatment reads this
// to change state partway through, so no beat is ever visually static for
// longer than STEP_SECONDS.
export const STEP_SECONDS = 3;

// A reveal timed in FRAMES with a proportional ceiling. Pure fractions of the
// beat compress to nothing on a 2.5s funnel and stretch to ten seconds on a
// long quote; both were wrong. Start at a fixed beat, finish by `latest`.
const revealAt = (frame, durationInFrames, startFrame, lengthFrames = 16) => {
  const start = Math.min(startFrame, durationInFrames * 0.45);
  const end = Math.min(start + lengthFrames, durationInFrames * 0.92);
  return ease(interpolate(frame, [start, Math.max(end, start + 1)], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
};

const stepAt = (frame, durationInFrames) => {
  const steps = Math.max(1, Math.ceil(durationInFrames / (STEP_SECONDS * FPS)));
  const index = Math.min(steps - 1, Math.floor(frame / (STEP_SECONDS * FPS)));
  return {index, steps};
};

// A photo is never the subject: it sits behind, heavily darkened, so type
// stays legible over any image the article happens to carry.
// The default dim was 0.82 and the photo still competed with the type — a
// face at 18% opacity reads as a face, and the eye goes to it over the number.
// A backdrop is texture, not subject.
const Backdrop = ({image, dim = 0.9, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const scale = interpolate(progress, [0, 1], [1.06, 1.14]);
  return (
    <div style={{position: 'absolute', inset: 0, overflow: 'hidden',
                 background: '#05070d'}}>
      {image ? (
        <Img
          src={image.url}
          style={{
            position: 'absolute', left: '50%', top: '50%',
            transform: `translate(-50%, -50%) scale(${scale})`,
            width: WIDTH, height: HEIGHT, objectFit: 'cover',
            objectPosition: 'center 40%',
          }}
        />
      ) : null}
      <div style={{position: 'absolute', inset: 0,
                   background: `rgba(5,7,13,${image ? dim : 0.9})`}} />
    </div>
  );
};

const Stage = ({children, align = 'center'}) => (
  <div style={{
    position: 'absolute',
    left: SAFE.left, right: SAFE.right, top: STAGE_TOP,
    height: HEIGHT - STAGE_TOP - SAFE.bottom,
    display: 'flex', flexDirection: 'column',
    alignItems: align === 'left' ? 'flex-start' : 'center',
    justifyContent: 'center', textAlign: align,
    fontFamily: FONT, color: '#ffffff',
  }}>
    {children}
  </div>
);

// --- hook ------------------------------------------------------------------
// Caption-dominant: the words ARE the visual. Largest type in the video, any
// photo pushed far back behind it.

const HookTreatment = ({line, image, frame, durationInFrames}) => {
  const {index} = stepAt(frame, durationInFrames);
  const entry = ease(interpolate(frame, [0, 14], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const figure = leadFigure(line.text);
  // Step 0 sets the phrase; later steps punch the figure it contains, so a
  // long hook is never one held frame.
  const scale = 1 + index * 0.04;

  const words = line.text.replace(/[."]+$/, '');
  const lines = wrapWords(words, 18);
  const size = Math.max(96, Math.min(190, SAFE_WIDTH / (18 * ADVANCE)));

  return (
    <>
      <Backdrop image={image} dim={0.86} frame={frame}
                durationInFrames={durationInFrames} />
      <Stage>
        <div style={{
          fontWeight: 900, fontSize: size, lineHeight: 0.98,
          letterSpacing: '-0.035em',
          opacity: entry, transform: `scale(${(0.94 + entry * 0.06) * scale})`,
          textShadow: '0 18px 60px rgba(0,0,0,0.75)',
        }}>
          {lines.map((l, i) => <div key={i}>{l}</div>)}
        </div>
        {figure && index > 0 ? (
          <div style={{marginTop: 40, fontWeight: 800, fontSize: 80,
                       color: '#ffd84d', letterSpacing: '-0.02em'}}>
            {figure.raw}
          </div>
        ) : null}
      </Stage>
    </>
  );
};

// --- proof -----------------------------------------------------------------
// Number card: the figure at display size, counting up, unit beneath. The
// surrounding sentence is subtitle only.

const CountUp = ({figure, progress}) => {
  const shown = Math.round(figure.numeric * ease(Math.min(1, progress * 1.6)));
  const prefix = figure.value.trim().startsWith('$') ? '$' : '';
  const text = figure.numeric >= 1000
    ? shown.toLocaleString('en-US')
    : String(shown);
  return <>{prefix}{text}</>;
};

const ProofTreatment = ({line, image, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const {index} = stepAt(frame, durationInFrames);
  const figure = leadFigure(line.text);

  // No figure in the sentence: fall back to the most concrete phrase, still
  // typographic. A proof beat without a number is still a proof beat.
  if (!figure) {
    const key = line.text.replace(/[^\w\s%$.-]/g, ' ').split(/\s+/)
      .filter(Boolean).reduce((a, b) => (b.length > a.length ? b : a), '');
    const size = Math.max(72, Math.min(200, SAFE_WIDTH / (Math.max(key.length, 1) * ADVANCE)));
    return (
      <>
        <Backdrop image={image} frame={frame} durationInFrames={durationInFrames} />
        <Stage>
          <div style={{fontWeight: 900, fontSize: size, letterSpacing: '-0.03em',
                       transform: `scale(${1 + index * 0.05})`}}>{key}</div>
        </Stage>
      </>
    );
  }

  const size = Math.max(120, Math.min(300,
    SAFE_WIDTH / (Math.max(figure.value.length, 1) * 0.62)));

  return (
    <>
      <Backdrop image={image} frame={frame} durationInFrames={durationInFrames} />
      <Stage>
        <div style={{
          fontWeight: 900, fontSize: size, lineHeight: 0.9,
          letterSpacing: '-0.04em', fontVariantNumeric: 'tabular-nums',
          textShadow: '0 18px 60px rgba(0,0,0,0.7)',
        }}>
          <CountUp figure={figure} progress={progress} />
        </div>
        {figure.unit ? (
          <div style={{
            marginTop: 24, fontWeight: 700, fontSize: 64,
            letterSpacing: '0.04em', textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.72)',
            // The unit resolves in after the count, so the card has a second
            // state rather than holding one frame.
            opacity: revealAt(frame, durationInFrames, 22),
          }}>
            {figure.unit}
          </div>
        ) : null}
      </Stage>
    </>
  );
};

// --- funnel ----------------------------------------------------------------
// The reason `funnel` is its own kind, and the highest-value visual available.
// Two bars, LARGER FIRST, animated so the drop is what the eye follows.

const FunnelTreatment = ({line, image, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const pair = funnelPair(line.text);
  const implied = impliedRatio(line.text);
  const lead = leadFigure(line.text);

  // "The attack halved its key strength" is a funnel with NO number written
  // out at all — the ratio is carried by the verb. Falling through to a
  // number card rendered the word "strength." at display size, which is the
  // failure this treatment exists to prevent. Build the pair from whichever
  // of the three sources the sentence actually provides:
  //   two figures      -> use them
  //   one figure + verb -> derive the second
  //   verb only         -> unitless before/after, labelled from the verb
  let big = pair ? pair.big : lead;
  let small = pair ? pair.small : null;

  if (!small && implied) {
    if (big) {
      small = {value: '', unit: '', numeric: big.numeric * implied.ratio,
               raw: implied.label};
    } else {
      // Purely proportional: the bars carry the ratio, the labels carry the
      // words. "before" -> "half" is still the drop the eye should follow.
      big = {value: '', unit: '', numeric: 1, raw: 'before'};
      small = {value: '', unit: '', numeric: implied.ratio, raw: implied.label};
    }
  }

  if (!big || !small) {
    return <ProofTreatment line={line} image={image} frame={frame}
                           durationInFrames={durationInFrames} />;
  }

  const ratio = Math.max(0.04, Math.min(1, small.numeric / big.numeric));
  // Timed in FRAMES, not in fractions of the beat. A funnel beat is often the
  // shortest in the script ("278 got it." runs 2.5s), and a drop scheduled
  // over 50-90% of the beat had barely started before the scene cut — the
  // attrition, which is the entire point of this treatment, was never seen.
  const growEnd = Math.min(18, durationInFrames * 0.3);
  const dropStart = growEnd + 4;
  const dropEnd = Math.min(dropStart + 20, durationInFrames * 0.9);
  const grow = ease(interpolate(frame, [0, growEnd], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const drop = ease(interpolate(frame, [dropStart, dropEnd], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));

  const barWidth = SAFE_WIDTH;
  const bigW = barWidth * grow;
  const smallW = barWidth * ratio * drop;

  return (
    <>
      <Backdrop image={image} frame={frame} durationInFrames={durationInFrames} />
      <Stage align="left">
        <div style={{width: '100%'}}>
          <div style={{fontWeight: 800, fontSize: 52, opacity: grow,
                       letterSpacing: '-0.01em', marginBottom: 12}}>
            {big.raw || big.value}
          </div>
          <div style={{height: 130, width: bigW, borderRadius: 14,
                       background: 'linear-gradient(90deg,#5b8cff,#8ab4ff)'}} />
          <div style={{height: 56}} />
          <div style={{fontWeight: 800, fontSize: 52, opacity: drop,
                       letterSpacing: '-0.01em', marginBottom: 12,
                       color: '#ffd84d'}}>
            {small.raw || small.value}
          </div>
          <div style={{height: 130, width: smallW, borderRadius: 14,
                       background: 'linear-gradient(90deg,#ffd84d,#ffb020)'}} />
        </div>
      </Stage>
    </>
  );
};

// --- quote -----------------------------------------------------------------
// Pull-quote card. NEVER over a photo: the marks and the attribution are the
// composition, and a backdrop only competes with them.

const QuoteTreatment = ({line, speaker, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const inner = (line.text.match(/"([^"]+)"/) || [null, line.text])[1];
  const words = inner.split(/\s+/);
  // Reveal in two parts so a long quotation is not one static card.
  const half = Math.ceil(words.length / 2);
  const secondIn = revealAt(frame, durationInFrames, 30, 20);
  const size = Math.max(52, Math.min(92, SAFE_WIDTH / (22 * ADVANCE)));

  return (
    <>
      <div style={{position: 'absolute', inset: 0,
                   background: 'linear-gradient(160deg,#141a2e 0%,#05070d 70%)'}} />
      <Stage align="left">
        <div style={{fontWeight: 900, fontSize: 200, lineHeight: 0.7,
                     color: 'rgba(255,255,255,0.22)'}}>&ldquo;</div>
        <div style={{fontWeight: 600, fontSize: size, lineHeight: 1.24,
                     letterSpacing: '-0.01em', marginTop: 8}}>
          <span>{words.slice(0, half).join(' ')}</span>{' '}
          <span style={{opacity: secondIn}}>{words.slice(half).join(' ')}</span>
        </div>
        {speaker ? (
          <div style={{marginTop: 36, opacity: secondIn}}>
            <div style={{fontWeight: 800, fontSize: 44, letterSpacing: '0.01em'}}>
              {speaker.name}
            </div>
            {speaker.role ? (
              <div style={{fontWeight: 600, fontSize: 34, marginTop: 6,
                           color: 'rgba(255,255,255,0.66)',
                           textTransform: 'uppercase', letterSpacing: '0.06em'}}>
                {speaker.role}
              </div>
            ) : null}
          </div>
        ) : null}
      </Stage>
    </>
  );
};

// --- catch -----------------------------------------------------------------
// The tension, as a split: two labelled halves, the second struck through.

const splitCatch = (text) => {
  const clean = text.replace(/^(the catch:|but|however)\s*/i, '');
  const marker = clean.match(/\b(but|while|without|though|although|yet)\b/i);
  if (marker && marker.index > 8) {
    return [clean.slice(0, marker.index).trim(),
            clean.slice(marker.index).trim()];
  }
  // A CLAUSE BOUNDARY, before blind halving. "The systems tested were weakened
  // versions, and the attacks would likely be infeasible" has no contrast word,
  // and splitting at the midpoint cut mid-clause ("...and the / attacks").
  const comma = clean.indexOf(', ');
  if (comma > 8 && comma < clean.length - 12) {
    return [clean.slice(0, comma).trim(),
            clean.slice(comma + 1).replace(/^\s*and\s+/i, '').trim()];
  }
  const words = clean.split(/\s+/);
  const half = Math.ceil(words.length / 2);
  return [words.slice(0, half).join(' '), words.slice(half).join(' ')];
};

const CatchTreatment = ({line, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const [first, second] = splitCatch(line.text);
  const strike = revealAt(frame, durationInFrames, 28, 22);
  const secondIn = revealAt(frame, durationInFrames, 20, 16);
  const size = Math.max(44, Math.min(76, SAFE_WIDTH / (24 * ADVANCE)));

  return (
    <>
      <div style={{position: 'absolute', inset: 0, background: '#05070d'}} />
      <div style={{position: 'absolute', inset: 0,
                   background: 'linear-gradient(180deg,#1b1430 0%,#05070d 55%)'}} />
      <Stage align="left">
        {/* Struck per LINE, using the text decoration rather than a drawn bar.
            An absolutely-positioned bar at 46% landed in the gap between lines
            of a three-line claim; a background-image with repeat-y filled the
            whole block as a highlight. line-through follows the glyphs. */}
        <div style={{
          width: '100%', fontWeight: 800, fontSize: size, lineHeight: 1.25,
          // Revealed left-to-right by clipping the struck copy over a plain one.
          position: 'relative',
        }}>
          <span style={{opacity: 0.55}}>{first}</span>
          <span style={{
            position: 'absolute', left: 0, top: 0, width: '100%',
            clipPath: `inset(0 ${(1 - strike) * 100}% 0 0)`,
            textDecoration: 'line-through',
            textDecorationColor: '#ff5c5c',
            textDecorationThickness: 7,
          }}>
            {first}
          </span>
        </div>
        <div style={{marginTop: 44, width: '100%', opacity: secondIn,
                     fontWeight: 800, fontSize: size, lineHeight: 1.2,
                     color: '#ffd84d'}}>
          {second}
        </div>
      </Stage>
    </>
  );
};

// --- closer ----------------------------------------------------------------
// The hook's composition, resolved. The bookend the closer beat performs
// verbally, performed visually.

const CloserTreatment = ({line, hookLine, image, frame, durationInFrames}) => {
  const progress = progressOf(frame, durationInFrames);
  const settle = ease(interpolate(progress, [0, 0.5], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}));
  const size = Math.max(96, Math.min(200, SAFE_WIDTH / (14 * ADVANCE)));
  const lines = wrapWords(line.text.replace(/[."]+$/, ''), 14);

  return (
    <>
      <Backdrop image={image} dim={0.9} frame={frame}
                durationInFrames={durationInFrames} />
      <Stage>
        {/* The hook, faded back — the same composition in its resolved state. */}
        {hookLine ? (
          <div style={{fontWeight: 800, fontSize: 44, marginBottom: 40,
                       color: 'rgba(255,255,255,0.34)', letterSpacing: '-0.01em',
                       textDecoration: 'line-through'}}>
            {wrapWords(hookLine.text.replace(/[."]+$/, ''), 30)[0]}
          </div>
        ) : null}
        <div style={{fontWeight: 900, fontSize: size, lineHeight: 0.98,
                     letterSpacing: '-0.035em',
                     transform: `scale(${0.9 + settle * 0.1})`, opacity: settle}}>
          {lines.map((l, i) => <div key={i}>{l}</div>)}
        </div>
      </Stage>
    </>
  );
};

// --- selection -------------------------------------------------------------

export const TREATMENTS = {
  hook: HookTreatment,
  proof: ProofTreatment,
  funnel: FunnelTreatment,
  quote: QuoteTreatment,
  catch: CatchTreatment,
  closer: CloserTreatment,
};

export const Treatment = ({kind, ...rest}) => {
  const Component = TREATMENTS[kind] || ProofTreatment;
  return <Component {...rest} />;
};

export {figures, funnelPair, leadFigure};
