#!/usr/bin/env node
/*
 * Ask the existing MiniUnSongData runtime parser for its playback timeline.
 * The official runtime stays outside this repository and is supplied at run time.
 */
const fs = require("fs");
const path = require("path");

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : "";
}

const runtimeDir = path.resolve(arg("runtime-dir"));
const inputPath = path.resolve(arg("input"));
if (!arg("runtime-dir") || !arg("input")) {
  console.error("usage: node extract_events.js --runtime-dir RUNTIME --input SCORE.json");
  process.exit(2);
}

function findRuntimeFile(prefix) {
  const exact = {
    "hc_": "hc_12ab21fd54-0419-12.js",
    "util_": "util_5b7fbbde18.js",
  }[prefix];
  const candidate = exact && path.join(runtimeDir, exact);
  if (candidate && fs.existsSync(candidate)) return candidate;
  const match = fs.readdirSync(runtimeDir).find((name) => name.startsWith(prefix) && name.endsWith(".js"));
  return match ? path.join(runtimeDir, match) : "";
}

const utilPath = findRuntimeFile("util_");
const parserPath = findRuntimeFile("hc_");
const builderPath = path.join(runtimeDir, "song_builder_new.js");
const codecPath = path.join(runtimeDir, "core_codec.js");
for (const required of [utilPath, parserPath, builderPath, codecPath]) {
  if (!required || !fs.existsSync(required)) {
    console.error(`runtime file not found in ${runtimeDir}`);
    process.exit(2);
  }
}

global.window = global;
global.document = {
  createElement: (tag) => ({ style: {}, setAttribute() {}, appendChild() {}, innerHTML: "", tagName: tag }),
  getElementById: () => null,
  createElementNS: (_ns, tag) => ({ style: {}, setAttribute() {}, appendChild() {}, innerHTML: "", tagName: tag }),
};
global.localStorage = {
  _s: {},
  setItem(key, value) { this._s[key] = value; },
  getItem(key) { return this._s[key] || null; },
  removeItem(key) { delete this._s[key]; },
};
global.location = { href: "http://127.0.0.1:8765/", hostname: "127.0.0.1", host: "127.0.0.1" };
global.svglinenum = 0;
global.svgbottomlinenum = 0;

const oldLog = console.log;
console.log = () => {};
try {
  eval(fs.readFileSync(utilPath, "utf8"));
  var parser = require(parserPath).parser;
  eval(fs.readFileSync(builderPath, "utf8"));
} finally {
  console.log = oldLog;
}

const { coreToSongData, getRenderOptions, toCorePayload } = require(codecPath);

function walk(nodes, result = []) {
  for (const node of nodes || []) {
    if (node && Array.isArray(node.notes)) {
      walk(node.notes, result);
    } else if (node && node.id && /^note\d+$/.test(node.id)) {
      result.push(node);
    }
  }
  return result;
}

try {
  const data = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  const corePayload = toCorePayload(data);
  const songData = coreToSongData(corePayload);
  const renderOptions = getRenderOptions(corePayload);
  songBuilder.params = {};
  const previousLog = console.log;
  console.log = () => {};
  songBuilder.init(parser);
  songBuilder.setParams(songData);
  const header = songBuilder.buildParams(renderOptions);
  const source = songBuilder.buildSource();
  const parsed = parser.parse(header + source);
  console.log = previousLog;

  const notes = walk(parsed.notes);
  let cursor = 0;
  const events = [];
  for (const note of notes) {
    const step = Number(note.noteDuration || 0);
    if (!Number.isFinite(step) || step < 0) {
      throw new Error(`invalid note duration for ${note.id}`);
    }
    const duration = Number(note.duration || 0);
    if (note.scale !== 0 && duration > 0 && Number.isFinite(note.midiNumber)) {
      events.push({ onset: cursor, duration, midi: Number(note.midiNumber) });
    }
    cursor += step;
  }
  process.stdout.write(JSON.stringify({
    events,
    note_count: notes.length,
    track_count: Array.isArray(parsed.notes) ? parsed.notes.length : 0,
    total_beats: cursor,
    bpm: parsed.bpm || 0,
  }) + "\n");
} catch (error) {
  console.error(error && error.message ? error.message : String(error));
  process.exit(1);
}
