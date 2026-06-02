const textInput = document.querySelector("#textInput");
const charCount = document.querySelector("#charCount");
const clearButton = document.querySelector("#clearButton");
const langSelect = document.querySelector("#langSelect");
const voiceSelect = document.querySelector("#voiceSelect");
const deliverySelect = document.querySelector("#deliverySelect");
const previewButton = document.querySelector("#previewButton");
const voiceList = document.querySelector("#voiceList");
const voiceCount = document.querySelector("#voiceCount");
const speedRange = document.querySelector("#speedRange");
const speedValue = document.querySelector("#speedValue");
const generateButton = document.querySelector("#generateButton");
const statusEl = document.querySelector("#status");
const audioPlayer = document.querySelector("#audioPlayer");
const downloadLink = document.querySelector("#downloadLink");
const resultMeta = document.querySelector("#resultMeta");
const historyList = document.querySelector("#historyList");
const refreshButton = document.querySelector("#refreshButton");

let config = { languages: {}, voices: {}, deliveryStyles: {}, outputs: [] };

const previewText = {
  a: "Hello, this is a quick voice preview.",
  b: "Hello, this is a quick British voice preview.",
  e: "Hola, esta es una prueba rapida de voz.",
  f: "Bonjour, ceci est un apercu rapide de la voix.",
  h: "Namaste, yeh awaaz ka chhota sa preview hai.",
  i: "Ciao, questa e una prova rapida della voce.",
  j: "Konnichiwa, kore wa koe no preview desu.",
  p: "Ola, este e um teste rapido de voz.",
  z: "Ni hao, zhe shi yige yuyin shiting.",
};

function setStatus(text, state = "") {
  statusEl.textContent = text;
  statusEl.className = `status ${state}`.trim();
}

function formatBytes(bytes) {
  if (!bytes) return "0 KB";
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatTime(seconds) {
  if (!seconds) return "";
  return `${seconds.toFixed(2)}s`;
}

function updateCharCount() {
  charCount.textContent = `${textInput.value.length.toLocaleString("vi-VN")} ký tự`;
}

function fillLanguages() {
  langSelect.innerHTML = "";
  Object.entries(config.languages).forEach(([code, name]) => {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = `${name} (${code})`;
    langSelect.append(option);
  });
  langSelect.value = "a";
  fillVoices();
}

function fillVoices() {
  const voices = config.voices[langSelect.value] || [];
  voiceSelect.innerHTML = "";
  voices.forEach((voice) => {
    const option = document.createElement("option");
    option.value = voice;
    option.textContent = voice;
    voiceSelect.append(option);
  });
  renderVoiceList();
}

function fillDeliveryStyles() {
  deliverySelect.innerHTML = "";
  Object.entries(config.deliveryStyles).forEach(([key, style]) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = style.label;
    option.title = style.description;
    deliverySelect.append(option);
  });
  if (config.deliveryStyles.dramatic) {
    deliverySelect.value = "dramatic";
  }
}

function renderVoiceList() {
  const voices = config.voices[langSelect.value] || [];
  voiceList.innerHTML = "";
  voiceCount.textContent = `${voices.length} giọng`;

  voices.forEach((voice) => {
    const chip = document.createElement("button");
    chip.className = `voice-chip ${voice === voiceSelect.value ? "active" : ""}`.trim();
    chip.type = "button";
    chip.title = `Chọn ${voice}`;
    chip.textContent = voice;
    chip.addEventListener("click", () => {
      voiceSelect.value = voice;
      renderVoiceList();
    });
    voiceList.append(chip);
  });
}

function renderHistory(outputs = []) {
  historyList.innerHTML = "";
  if (!outputs.length) {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "Chưa có file nào trong outputs.";
    historyList.append(empty);
    return;
  }

  outputs.forEach((item) => {
    const row = document.createElement("div");
    row.className = "history-item";

    const info = document.createElement("div");
    const name = document.createElement("strong");
    const meta = document.createElement("small");
    name.textContent = item.name;
    meta.textContent = `Audio đầy đủ · ${formatBytes(item.size)}`;
    info.append(name, meta);

    const play = document.createElement("button");
    play.className = "mini-button";
    play.type = "button";
    play.title = "Phát audio";
    play.textContent = "▶";
    play.addEventListener("click", () => {
      audioPlayer.src = `${item.url}?t=${Date.now()}`;
      downloadLink.href = item.url;
      downloadLink.download = item.name;
      downloadLink.classList.remove("disabled");
      resultMeta.textContent = item.name;
      audioPlayer.play();
    });

    row.append(info, play);
    historyList.append(row);
  });
}

function deliveryLabel(key) {
  return config.deliveryStyles[key]?.label || key;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) throw new Error("Không tải được cấu hình UI.");
  config = await response.json();
  fillLanguages();
  fillDeliveryStyles();
  renderHistory(config.outputs);
}

async function generateAudio() {
  const text = textInput.value.trim();
  if (!text) {
    setStatus("Thiếu text", "error");
    textInput.focus();
    return;
  }

  generateButton.disabled = true;
  previewButton.disabled = true;
  setStatus("Đang tạo", "busy");
  resultMeta.textContent = `Đang tạo audio đầy đủ từ ${text.length.toLocaleString("vi-VN")} ký tự...`;

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        lang: langSelect.value,
        voice: voiceSelect.value,
        speed: Number(speedRange.value),
        delivery: deliverySelect.value,
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Tạo audio thất bại.");

    const cacheBustedUrl = `${data.url}?t=${Date.now()}`;
    audioPlayer.src = cacheBustedUrl;
    downloadLink.href = data.url;
    downloadLink.download = data.name;
    downloadLink.classList.remove("disabled");
    resultMeta.textContent = `Audio đầy đủ · ${deliveryLabel(data.delivery)} · ${data.name} · ${formatTime(data.duration)} · ${formatBytes(data.size)}`;
    renderHistory(data.outputs);
    setStatus("Xong");
    audioPlayer.play();
  } catch (error) {
    setStatus("Lỗi", "error");
    resultMeta.textContent = error.message;
  } finally {
    generateButton.disabled = false;
    previewButton.disabled = false;
  }
}

async function previewVoice() {
  previewButton.disabled = true;
  generateButton.disabled = true;
  setStatus("Nghe thử", "busy");
  resultMeta.textContent = `Đang nghe thử ${voiceSelect.value}...`;

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: previewText[langSelect.value] || previewText.a,
        lang: langSelect.value,
        voice: voiceSelect.value,
        speed: Number(speedRange.value),
        prefix: "preview",
        delivery: deliverySelect.value,
      }),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Nghe thử thất bại.");

    audioPlayer.src = `${data.url}?t=${Date.now()}`;
    downloadLink.href = data.url;
    downloadLink.download = data.name;
    downloadLink.classList.remove("disabled");
    resultMeta.textContent = `Nghe thử ${voiceSelect.value} · ${deliveryLabel(deliverySelect.value)} · ${formatTime(data.duration)} · ${formatBytes(data.size)}`;
    renderHistory(data.outputs);
    setStatus("Xong");
    audioPlayer.play();
  } catch (error) {
    setStatus("Lỗi", "error");
    resultMeta.textContent = error.message;
  } finally {
    previewButton.disabled = false;
    generateButton.disabled = false;
  }
}

textInput.addEventListener("input", updateCharCount);
clearButton.addEventListener("click", () => {
  textInput.value = "";
  updateCharCount();
  textInput.focus();
});
langSelect.addEventListener("change", fillVoices);
voiceSelect.addEventListener("change", renderVoiceList);
speedRange.addEventListener("input", () => {
  speedValue.textContent = `${Number(speedRange.value).toFixed(2)}x`;
});
generateButton.addEventListener("click", generateAudio);
previewButton.addEventListener("click", previewVoice);
refreshButton.addEventListener("click", async () => {
  setStatus("Đang tải", "busy");
  await loadConfig();
  setStatus("Sẵn sàng");
});

updateCharCount();
loadConfig().then(() => setStatus("Sẵn sàng")).catch((error) => {
  setStatus("Lỗi", "error");
  resultMeta.textContent = error.message;
});
