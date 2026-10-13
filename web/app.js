const SAMPLES = {
  complex: {
    kind: "json",
    value: JSON.stringify({
      songdata: {
        song_name: "手动样例 · 复杂时值",
        keynote: "1=C",
        rhythm: "4/4",
        bpm: 90,
        notation: "{bpm:90}\n1. 2_ 3= 4=|5 6 7 1g|1g= 7_ 6_ 5_|4= 3= 2= 1=|||",
        lyric: ["春风吹过；山河", "万里入歌"],
      },
    }, null, 2),
  },
  json: {
    kind: "json",
    value: JSON.stringify({
      songdata: {
        song_name: "MiniUnSongData sample",
        keynote: "1=C",
        rhythm: "4/4",
        notation: "{bpm:90}\n1= 2= 3= 4=|5= 6= 7= 1g=|||",
        lyric: ["一二三四，五六七"],
      },
    }, null, 2),
  },
  notation: {
    kind: "notation",
    value: "{bpm:90}\n1= 2= 3= 4=|5_ 6_ 7_ 1g=|||",
  },
};

let realSample = null;
let guofengSamples = [];
const inputText = document.querySelector("#inputText");
const sampleSelect = document.querySelector("#sampleSelect");
const guofengOptions = document.querySelector("#guofengOptions");
const errorBox = document.querySelector("#errorBox");
const renderButton = document.querySelector("#renderButton");
const roundtripButton = document.querySelector("#roundtripButton");
const roundtripPanel = document.querySelector("#roundtripPanel");
const sourceSvg = document.querySelector("#sourceSvg");
const roundtripSourceSvg = document.querySelector("#roundtripSourceSvg");
const recoveredSvg = document.querySelector("#recoveredSvg");
const staffSvg = document.querySelector("#staffSvg");
const renderTitle = document.querySelector("#renderTitle");
const runtimeStatus = document.querySelector("#runtimeStatus");
const kernOutput = document.querySelector("#kernOutput");
const jsonOutput = document.querySelector("#jsonOutput");
const roundtripSummary = document.querySelector("#roundtripSummary");

function setError(message = "") {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function putSvg(target, svg) {
  target.classList.remove("empty-stage");
  target.innerHTML = svg;
}

function setBusy(busy) {
  renderButton.disabled = busy;
  roundtripButton.disabled = busy;
  renderButton.querySelector(".button-icon").textContent = busy ? "…" : "▶";
}

async function post(path) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input: inputText.value }),
  });
  const data = await response.json().catch(() => ({ ok: false, error: `HTTP ${response.status}` }));
  if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function loadSample() {
  if (sampleSelect.value === "real") {
    if (realSample) {
      inputText.value = JSON.stringify(realSample, null, 2);
      setError("");
    } else {
      fetch("/api/sample")
        .then((response) => response.json())
        .then((data) => {
          if (!data.ok) throw new Error(data.error || "样例加载失败");
          realSample = data.payload;
          inputText.value = JSON.stringify(realSample, null, 2);
          setError("");
        })
        .catch((error) => setError(error.message));
    }
    return;
  }
  if (sampleSelect.value.startsWith("10074new:")) {
    fetch(`/api/sample?case=${encodeURIComponent(sampleSelect.value)}`)
      .then((response) => response.json())
      .then((data) => {
        if (!data.ok) throw new Error(data.error || "古风案例加载失败");
        inputText.value = JSON.stringify(data.payload, null, 2);
        setError("");
        inputText.focus();
      })
      .catch((error) => setError(error.message));
    return;
  }
  const sample = SAMPLES[sampleSelect.value];
  inputText.value = sample.value;
  setError("");
  inputText.focus();
}

function showSummary(data) {
  roundtripSummary.innerHTML = "";
  const values = [
    `${data.event_count} events`,
    data.meter || "4/4",
    data.exact ? "EXACT" : "DIFF",
  ];
  for (const value of values) {
    const badge = document.createElement("span");
    badge.className = "summary-badge";
    badge.textContent = value;
    roundtripSummary.appendChild(badge);
  }
}

async function render() {
  setError("");
  setBusy(true);
  try {
    const data = await post("/api/render");
    putSvg(sourceSvg, data.svg);
    renderTitle.textContent = data.title || "已完成";
    roundtripPanel.hidden = true;
  } catch (error) {
    setError(error.message);
  } finally {
    setBusy(false);
  }
}

async function doRoundtrip() {
  setError("");
  setBusy(true);
  try {
    const data = await post("/api/roundtrip");
    putSvg(sourceSvg, data.source_svg);
    putSvg(roundtripSourceSvg, data.source_svg);
    putSvg(recoveredSvg, data.recovered_svg);
    putSvg(staffSvg, data.staff_svg);
    kernOutput.textContent = data.kern;
    jsonOutput.textContent = JSON.stringify(data.recovered_json, null, 2);
    showSummary(data);
    renderTitle.textContent = "source / recovered 已生成";
    roundtripPanel.hidden = false;
    roundtripPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setError(error.message);
  } finally {
    setBusy(false);
  }
}

async function copyText(element) {
  try {
    await navigator.clipboard.writeText(element.textContent);
  } catch {
    setError("复制失败，请直接选择文本复制。");
  }
}

document.querySelector("#loadSample").addEventListener("click", loadSample);
sampleSelect.addEventListener("change", loadSample);
document.querySelector("#clearInput").addEventListener("click", () => {
  inputText.value = "";
  setError("");
  roundtripPanel.hidden = true;
});
renderButton.addEventListener("click", render);
roundtripButton.addEventListener("click", doRoundtrip);
document.querySelector("#copyKern").addEventListener("click", () => copyText(kernOutput));
document.querySelector("#copyJson").addEventListener("click", () => copyText(jsonOutput));

fetch("/api/samples")
  .then((response) => response.json())
  .then((data) => {
    if (!data.ok) throw new Error(data.error || "案例列表加载失败");
    guofengSamples = data.samples || [];
    for (const sample of guofengSamples) {
      const option = document.createElement("option");
      option.value = sample.id;
      option.textContent = `${sample.directory} · ${sample.song_name} (${sample.rhythm})`;
      guofengOptions.appendChild(option);
    }
  })
  .catch((error) => setError(error.message));

fetch("/api/config")
  .then((response) => response.json())
  .then((data) => {
    if (data.runtime_configured && data.render_script) {
      runtimeStatus.textContent = "official runtime ready";
      runtimeStatus.className = "status-chip status-ready";
    } else {
      runtimeStatus.textContent = "runtime 未配置";
      runtimeStatus.className = "status-chip status-error";
    }
  })
  .catch(() => {
    runtimeStatus.textContent = "server unavailable";
    runtimeStatus.className = "status-chip status-error";
  });

sampleSelect.value = "real";
loadSample();
