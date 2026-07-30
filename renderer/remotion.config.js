import {Config} from '@remotion/cli/config';

Config.setVideoImageFormat('jpeg');
Config.setOverwriteOutput(true);
// The composition reads props.json and audio from public/ via staticFile().
Config.setPublicDir('public');
