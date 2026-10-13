#!/usr/bin/env node
/**
 * Thin adapter around the user's original notation -> hc.parse() -> SVG path.
 * The third-party HC engine is supplied by --engine or HC_ENGINE and is not
 * included in this repository.
 */
const fs = require("fs");
const path = require("path");

global.window = {
  addEventListener() {},
  location: { host: "www.cangqiang.com.cn", href: "" },
  localStorage: { getItem() { return null; }, setItem() {} },
};
global.svgbottomlinenum = 0;
global.svglinenum = 0;
global.initline = 0;
global.allY = [];

function arg(name, fallback = "") {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

const engine = arg("engine", process.env.HC_ENGINE);
if (!engine || !fs.existsSync(engine)) {
  console.error("HC engine not found; pass --engine or set HC_ENGINE.");
  process.exit(2);
}

const notationPath = arg("notation");
const jsonPath = arg("json");
const outPath = arg("out");
if ((!notationPath && !jsonPath) || !outPath) {
  console.error("usage: node tools/render_hc.js --notation notation.txt|--json score.json --out score.svg [--engine hc.js]");
  process.exit(2);
}

let notation;
let title = arg("title");
let lyric = [];
if (jsonPath) {
  const payload = JSON.parse(fs.readFileSync(jsonPath, "utf8"));
  const songdata = payload.songdata || payload;
  notation = songdata.notation;
  title = title || songdata.song_name || path.basename(jsonPath);
  if (typeof songdata.lyric === "string") {
    try {
      lyric = JSON.parse(songdata.lyric);
    } catch {
      lyric = [songdata.lyric];
    }
  } else if (Array.isArray(songdata.lyric)) {
    lyric = songdata.lyric;
  }
  if (lyric.length && !notation.includes("{lyric}")) {
    notation += "\n" + lyric
      .map((line) => `{lyric}\n${String(line)}\n{/lyric}`)
      .join("\n");
  }
} else {
  notation = fs.readFileSync(notationPath, "utf8");
}
if (typeof notation !== "string" || !notation.trim()) {
  console.error("input does not contain notation text.");
  process.exit(2);
}
const hc = require(path.resolve(engine));
const result = hc.parse(notation);
if (!result || typeof result.svg !== "string" || !result.svg.startsWith("<svg")) {
  console.error("hc.parse() did not return an SVG.");
  process.exit(1);
}

fs.mkdirSync(path.dirname(path.resolve(outPath)), { recursive: true });
fs.writeFileSync(outPath, result.svg, "utf8");
console.log(`OK ${title} -> ${outPath} (${result.totalMeasures} measures, ${result.svg.length} bytes)`);
