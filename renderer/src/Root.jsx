import React from 'react';
import {Composition} from 'remotion';
import {NewsVideo} from './Video.jsx';
import {defaultProps, durationInFrames, FPS, HEIGHT, WIDTH} from './schema.js';
import props from '../props.json';

// ONE composition. `npm start` previews whatever props.json holds, which
// `python run.py --update-props` fills from the most recent narrated run.

export const RemotionRoot = () => {
  return (
    <Composition
      id="NewsVideo"
      component={NewsVideo}
      width={WIDTH}
      height={HEIGHT}
      fps={FPS}
      defaultProps={{...defaultProps, ...props}}
      // Duration is derived from the real audio length carried in props, so a
      // 15s script and a 110s script both render to the right length.
      calculateMetadata={({props: p}) => ({
        durationInFrames: durationInFrames({
          durationSeconds: p.durationSeconds ?? defaultProps.durationSeconds,
        }),
      })}
    />
  );
};
