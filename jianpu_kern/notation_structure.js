#!/usr/bin/env node
/* Read connection and playback data from the user's official notation parser. */
const fs = require("fs");
const path = require("path");
const runtime = path.resolve(process.argv[2]);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
global.window = global;
global.document = {
  createElement: (tag) => ({ style: {}, setAttribute() {}, appendChild() {}, innerHTML: "", tagName: tag }),
  getElementById: () => null,
  createElementNS: (_ns, tag) => ({ style: {}, setAttribute() {}, appendChild() {}, innerHTML: "", tagName: tag }),
};
global.localStorage = { setItem() {}, getItem() { return null; }, removeItem() {} };
global.location = { href: "http://127.0.0.1/", hostname: "127.0.0.1", host: "127.0.0.1" };
global.svglinenum = 0;
global.svgbottomlinenum = 0;

function collect(obj, notes = new Map(), seen = new Set()) {
  if (!obj || typeof obj !== "object" || seen.has(obj)) return notes;
  seen.add(obj);
  if (obj.type === "note") notes.set(String(obj.id), obj);
  for (const key of ["cells", "extendedNotes", "slaveNotes", "lyricNotes", "notes"]) {
    for (const child of obj[key] || []) collect(child, notes, seen);
  }
  return notes;
}

try {
  const log = console.log;
  console.log = () => {};
  eval(fs.readFileSync(path.join(runtime, "util_5b7fbbde18.js"), "utf8"));
  const parser = require(path.join(runtime, "hc_12ab21fd54-0419-12.js")).parser;
  const builder = require(path.join(runtime, "song_builder_new.js")).builder;
  const codec = require(path.join(runtime, "core_codec.js"));
  const core = codec.toCorePayload(payload);
  builder.params = {};
  builder.init(parser);
  builder.setParams(codec.coreToSongData(core));
  const parsed = parser.parse(builder.buildParams(codec.getRenderOptions(core)) + builder.buildSource());
  console.log = log;
  const sourceNotes = collect({ cells: [...parsed.cells, ...(parsed.repeatParts || [])] });
  const linear = parsed.notes[0].notes.filter((note) => /^note\d+$/.test(String(note.id)));
  const positions = new Map();
  let cursor = 0;
  const notes = linear.map((note, index) => {
    const id = String(note.id).replace(/^note/, "");
    if (positions.has(id)) throw new Error("Repeated source IDs require unfolded phrase input.");
    positions.set(id, index);
    const source = sourceNotes.get(id);
    if (!source) throw new Error(`Missing source note ${id}`);
    const rawLyric = (source.lyrics || [])[0];
    const lyric = rawLyric && typeof rawLyric === "object"
      ? String(rawLyric.value ?? "")
      : String(rawLyric ?? "");
    const result = {
      content: source.content,
      octave: source.octave || 0,
      onset: cursor,
      head: Number(note.noteDuration),
      duration: Number(note.duration),
      midi: Number(note.midiNumber),
      lyric: lyric.replace(/^\s*\d+\s*[.．、:：]\s*/, ""),
    };
    cursor += result.head;
    return result;
  });
  // Bar cells are source syntax, not a meter-derived grid. In particular a
  // phrase may begin with a pickup or contain a deliberately short measure.
  const bars = new Set();
  const visited = new Set();
  function collectBars(obj) {
    if (!obj || typeof obj !== "object" || visited.has(obj)) return;
    visited.add(obj);
    if (/_bar$/.test(obj.type || "")) bars.add(obj);
    for (const key of ["cells", "extendedNotes", "slaveNotes", "lyricNotes", "notes"]) {
      for (const child of obj[key] || []) collectBars(child);
    }
  }
  collectBars({ cells: parsed.cells });
  const precedes = (note, bar) => note.firstLine < bar.firstLine
    || (note.firstLine === bar.firstLine && note.firstColumn < bar.firstColumn);
  const barlines = [...new Set([...bars].map((bar) => {
    let end = 0;
    linear.forEach((note, index) => {
      const source = sourceNotes.get(String(note.id).replace(/^note/, ""));
      if (precedes(source, bar)) end = notes[index].onset + notes[index].head;
    });
    return end;
  }))].filter((beat) => beat > 0).sort((a, b) => a - b);
  const connections = [];
  for (const slur of parsed.slurs || []) {
    if (Number(slur.n)) throw new Error("Tuplet connection reconstruction is not supported.");
    const indexes = [...collect(slur).keys()].map((id) => positions.get(id));
    if (indexes.some((index) => index === undefined)) continue;
    const unique = [...new Set(indexes)].sort((a, b) => a - b);
    if (unique.length >= 2) connections.push({ start: unique[0], end: unique.at(-1) });
  }
  process.stdout.write(JSON.stringify({ notes, connections, barlines, total_beats: cursor }) + "\n");
} catch (error) {
  console.error(error.message || String(error));
  process.exit(1);
}
