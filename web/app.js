"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  config: null,
  preset: "common3500",
  projects: [],
  projectId: null,
  files: [],
  fileUrls: [],
  jobId: null,
  pollTimer: null,
  logs: [],
  lastMessage: "",
  building: false,
  buildProjectId: null,
  pollFails: 0,
  fontFace: null,
  fontFamily: "HandFontPreview",
};

/* ------------------------------------------------------------ utils */

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function naturalCompare(a, b) {
  return a.localeCompare(b, "zh-CN", { numeric: true, sensitivity: "base" });
}

function formatBytes(num) {
  if (!num) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (num >= 1024 && i < units.length - 1) { num /= 1024; i++; }
  return `${num.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function projectFileUrl(projectId, rel) {
  return "/projects/" + encodeURIComponent(projectId) + "/" +
    String(rel).split("/").map(encodeURIComponent).join("/");
}

function toast(message, kind = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast show" + (kind === "error" ? " error" : "");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.className = "toast"; }, 3600);
}

async function api(url, options = {}) {
  const res = await fetch(url, options);
  let data;
  try { data = await res.json(); } catch { data = { ok: false, error: `服务器返回异常 (${res.status})` }; }
  if (!res.ok || data.ok === false) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

function setBusy(button, busy, text) {
  if (!button) return;
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span>${escapeHtml(text || "处理中…")}</span>`;
  } else {
    button.disabled = false;
    if (button.dataset.original) button.innerHTML = button.dataset.original;
  }
}

/* ------------------------------------------------------------ init */

async function init() {
  bindEvents();
  try {
    state.config = await api("/api/config");
    renderPresets();
    const saved = localStorage.getItem("handfont.family");
    if (saved) $("#family").value = saved;
    updateEstimate();
    await loadProjects();
    if (!state.config.potrace) {
      toast("未检测到 potrace，将使用简化轮廓，效果会略粗糙", "error");
    }
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderPresets() {
  const box = $("#preset-chips");
  box.innerHTML = "";
  for (const preset of state.config.presets) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.id = preset.id;
    btn.className = preset.id === state.preset ? "active" : "";
    btn.innerHTML = `${escapeHtml(preset.label)}${preset.count ? `<small>${preset.count}</small>` : ""}`;
    btn.title = preset.desc || "";
    btn.addEventListener("click", () => {
      state.preset = preset.id;
      $$("#preset-chips button").forEach((b) => b.classList.toggle("active", b.dataset.id === preset.id));
      $("#custom-wrap").classList.toggle("hidden", preset.id !== "custom");
      updateEstimate();
    });
    box.appendChild(btn);
  }
}

function countCustomChars(text) {
  const seen = new Set();
  for (const ch of text) {
    if (/\s/.test(ch)) continue;
    seen.add(ch);
  }
  return seen.size;
}

function updateEstimate() {
  const cols = Math.max(1, parseInt($("#cols").value, 10) || 9);
  const rows = Math.max(1, parseInt($("#rows").value, 10) || 12);
  let count = 0;
  if (state.preset === "custom") {
    count = countCustomChars($("#custom-text").value);
    $("#custom-count").textContent = String(count);
  } else {
    const preset = state.config?.presets.find((p) => p.id === state.preset);
    count = preset?.count || 0;
  }
  const pages = count ? Math.ceil(count / (cols * rows)) : 0;
  $("#estimate").textContent = count
    ? `约 ${count} 字 · ${pages} 页 · 每页 ${cols} × ${rows} 格`
    : "输入文字后显示页数";
}

/* ------------------------------------------------------------ template */

async function generateTemplate() {
  const family = ($("#family").value || "").trim() || "我的手写体";
  const cols = Math.min(12, Math.max(4, parseInt($("#cols").value, 10) || 9));
  const rows = Math.min(16, Math.max(6, parseInt($("#rows").value, 10) || 12));
  const text = $("#custom-text").value;
  if (state.preset === "custom" && !text.trim()) {
    toast("请先输入想写的文字", "error");
    $("#custom-text").focus();
    return;
  }
  if (state.preset === "custom" && countCustomChars(text) > 12000) {
    toast("一次最多 12000 字，请分批制作", "error");
    return;
  }
  localStorage.setItem("handfont.family", family);

  const btn = $("#btn-template");
  setBusy(btn, true, "正在排版…");
  try {
    const data = await api("/api/template", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset: state.preset, text, cols, rows, family }),
    });
    renderTemplateResult(data);
    state.projectId = data.project.id;
    await loadProjects(data.project.id);
    toast("模板已生成，可以打印了");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    setBusy(btn, false);
  }
}

function renderTemplateResult(data) {
  const panel = $("#template-result");
  const meta = data.meta;
  const project = data.project;
  const pngs = meta.pngs || [];
  const thumbs = (meta.thumbs || []).map(
    (url, i) => `<a href="${pngs[i] || url}" target="_blank" rel="noopener" title="打开第 ${i + 1} 页高清 PNG"><img src="${url}" alt="模板预览" loading="lazy"></a>`
  ).join("");
  const pdf = meta.pdf
    ? `<a class="btn primary small" href="${meta.pdf}" target="_blank" rel="noopener">下载 PDF</a>`
    : `<span class="hint">PDF 生成失败，请直接下载 PNG。</span>`;
  const pngZip = meta.png_zip
    ? `<a class="btn small" href="${meta.png_zip}" download>下载 PNG 压缩包</a>`
    : "";
  const pngList = pngs.length
    ? `<details class="png-list">
         <summary>单页高清 PNG（${pngs.length} 张 · ${meta.page_width}×${meta.page_height} · ${meta.dpi} DPI）</summary>
         <div class="png-links">
           ${pngs.map((url, i) => `<a href="${url}" target="_blank" rel="noopener" download="page_${String(i + 1).padStart(3, "0")}.png">第 ${i + 1} 页</a>`).join("")}
         </div>
       </details>`
    : "";
  panel.classList.remove("soft");
  panel.innerHTML = `
    <div class="template-done">
      <div>
        <p class="eyebrow small">模板已就绪</p>
        <h3>${escapeHtml(project.title)}</h3>
      </div>
      <div class="stat-row">
        <div><b>${meta.total_chars}</b>字</div>
        <div><b>${meta.pages}</b>页</div>
        <div><b>${meta.cols}×${meta.rows}</b>每页</div>
      </div>
      <div class="thumbs">${thumbs}</div>
      <div class="template-actions">
        ${pdf}
        ${pngZip}
        <button class="btn small" id="go-build">去合成字体 →</button>
      </div>
      ${pngList}
      <p class="hint">打印选 PDF；在平板/电脑上直接书写选 PNG。PNG 为 ${meta.dpi} DPI 高清图（${meta.page_width}×${meta.page_height}）。写完后按页序拍照，回到下面第 02 步上传。</p>
    </div>`;
  $("#go-build")?.addEventListener("click", () => {
    $("#step-build").scrollIntoView({ behavior: "smooth", block: "start" });
    $("#dropzone").classList.add("drag");
    setTimeout(() => $("#dropzone").classList.remove("drag"), 1200);
  });
}

/* ------------------------------------------------------------ projects */

async function loadProjects(selectId = null) {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  const select = $("#project-select");
  const previous = selectId || state.projectId;
  select.innerHTML = "";

  if (!state.projects.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "还没有模板，请先在上一步生成";
    select.appendChild(opt);
    state.projectId = null;
    renderProjectMeta();
    updateBuildState();
    return;
  }

  for (const project of state.projects) {
    const opt = document.createElement("option");
    opt.value = project.id;
    const done = project.font ? " · 已生成" : "";
    opt.textContent = `${project.title} · ${project.total_chars} 字 · ${project.pages} 页${done}`;
    select.appendChild(opt);
  }

  const exists = state.projects.some((p) => p.id === previous);
  state.projectId = exists ? previous : state.projects[0].id;
  select.value = state.projectId;
  renderProjectMeta();
  updateBuildState();
}

function currentProject() {
  return state.projects.find((p) => p.id === state.projectId) || null;
}

function renderProjectMeta() {
  const box = $("#project-meta");
  const project = currentProject();
  if (!project) {
    box.textContent = "";
    return;
  }
  const pdf = project.pdf
    ? ` · <a href="${project.pdf}" target="_blank" rel="noopener">模板 PDF</a>`
    : "";
  const png = project.png_zip
    ? ` · <a href="${project.png_zip}" download>模板 PNG</a>`
    : "";
  let text = `共 <b>${project.total_chars}</b> 字 · <b>${project.pages}</b> 页 · 每页 ${project.cols}×${project.rows}${pdf}${png}`;
  if (state.files.length && state.files.length !== project.pages) {
    text += `<br><span style="color:var(--accent)">已选 ${state.files.length} 张照片，与 ${project.pages} 页不匹配</span>`;
  }
  box.innerHTML = text;
  $("#link-pdf").href = project.pdf || "#";
}

/* ------------------------------------------------------------ files */

function addFiles(fileList) {
  const incoming = Array.from(fileList).filter((file) =>
    file.type.startsWith("image/") || /\.(jpe?g|png|webp|bmp|tiff?)$/i.test(file.name)
  );
  if (!incoming.length) {
    toast("请选择图片文件", "error");
    return;
  }
  const existing = new Set(state.files.map((f) => `${f.name}:${f.size}:${f.lastModified}`));
  for (const file of incoming) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (!existing.has(key)) {
      state.files.push(file);
      existing.add(key);
    }
  }
  state.files.sort((a, b) => naturalCompare(a.name, b.name));
  renderFiles();
  renderProjectMeta();
  updateBuildState();
}

function renderFiles() {
  const list = $("#file-list");
  state.fileUrls.forEach((url) => URL.revokeObjectURL(url));
  state.fileUrls = [];
  list.innerHTML = "";
  for (const [index, file] of state.files.entries()) {
    const url = URL.createObjectURL(file);
    state.fileUrls.push(url);
    const row = document.createElement("div");
    row.className = "file-item";
    row.innerHTML = `
      <img src="${url}" alt="">
      <span class="file-name" title="${escapeHtml(file.name)}">${index + 1}. ${escapeHtml(file.name)}</span>
      <span class="file-size">${formatBytes(file.size)}</span>
      <button type="button" title="移除" aria-label="移除">×</button>`;
    row.querySelector("button").addEventListener("click", () => {
      state.files.splice(index, 1);
      renderFiles();
      renderProjectMeta();
      updateBuildState();
    });
    list.appendChild(row);
  }
}

function updateBuildState() {
  const button = $("#btn-build");
  const hint = $("#build-hint");
  const ready = Boolean(state.projectId && state.files.length && !state.building);
  button.disabled = !ready;
  if (!state.projectId) hint.textContent = "请先在上一步生成模板。";
  else if (!state.files.length) hint.textContent = "请上传按页序排列的照片。";
  else if (state.building) hint.textContent = "正在合成，请保持页面打开。";
  else hint.textContent = `已选 ${state.files.length} 张照片，点击开始合成。`;
}

/* ------------------------------------------------------------ build */

function startBuild() {
  if (state.building) return;
  const project = currentProject();
  if (!project || !state.files.length) {
    toast("请先选择模板并上传照片", "error");
    return;
  }
  state.building = true;
  state.buildProjectId = state.projectId;
  state.pollFails = 0;
  state.logs = [];
  state.lastMessage = "";
  $("#project-select").disabled = true;
  $("#build-empty").classList.add("hidden");
  $("#result-panel").classList.add("hidden");
  $("#job-panel").classList.remove("hidden");
  $("#job-log").textContent = "";
  $("#job-status").textContent = "正在上传…";
  $("#job-percent").textContent = "0%";
  $("#progress-bar").style.width = "2%";
  updateBuildState();
  setBusy($("#btn-build"), true, "合成中…");

  const form = new FormData();
  for (const file of state.files) form.append("files", file, file.name);
  form.append("family", ($("#family").value || "").trim());
  form.append("normalize", $("#normalize").checked ? "1" : "0");

  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/build?project=${encodeURIComponent(state.projectId)}`);
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const pct = Math.round((event.loaded / event.total) * 100);
    $("#job-percent").textContent = `${pct}%`;
    $("#progress-bar").style.width = `${Math.max(2, pct * 0.15)}%`;
    $("#job-status").textContent = pct >= 100 ? "上传完成，等待处理…" : `正在上传 ${pct}%`;
  };
  xhr.onload = () => {
    let data;
    try { data = JSON.parse(xhr.responseText); } catch { data = { ok: false, error: "服务器返回异常" }; }
    if (xhr.status >= 400 || data.ok === false) {
      buildFailed(data.error || `上传失败 (${xhr.status})`);
      return;
    }
    state.jobId = data.job;
    $("#job-status").textContent = "已进入处理队列…";
    pollJob();
  };
  xhr.onerror = () => buildFailed("网络错误，请确认本地服务仍在运行");
  xhr.send(form);
}

function buildFailed(message) {
  state.building = false;
  $("#project-select").disabled = false;
  $("#job-status").textContent = "失败";
  $("#progress-bar").style.width = "100%";
  setBusy($("#btn-build"), false);
  updateBuildState();
  toast(message, "error");
  appendLog(`错误：${message}`);
}

function appendLog(message) {
  if (!message || message === state.lastMessage) return;
  state.lastMessage = message;
  state.logs.push(message);
  if (state.logs.length > 30) state.logs.shift();
  $("#job-log").textContent = state.logs.join("\n");
  $("#job-log").scrollTop = $("#job-log").scrollHeight;
}

async function pollJob() {
  clearTimeout(state.pollTimer);
  if (!state.jobId) return;
  try {
    const data = await api(`/api/jobs/${state.jobId}`);
    const job = data.job;
    const pct = Math.round((job.progress || 0) * 100);
    $("#job-percent").textContent = `${pct}%`;
    $("#progress-bar").style.width = `${Math.max(2, pct)}%`;
    $("#job-status").textContent = job.message || "处理中…";
    appendLog(job.message);
    if (job.status === "done") {
      state.building = false;
      $("#project-select").disabled = false;
      setBusy($("#btn-build"), false);
      updateBuildState();
      renderBuildResult(job.result);
      loadProjects(state.projectId).catch(() => {});
      toast("字体合成完成");
      return;
    }
    if (job.status === "error") {
      buildFailed(job.error || "合成失败");
      return;
    }
    state.pollFails = 0;
    state.pollTimer = setTimeout(pollJob, 700);
  } catch (err) {
    state.pollFails += 1;
    if (state.pollFails > 30) {
      buildFailed(`无法获取任务状态：${err.message}`);
      return;
    }
    state.pollTimer = setTimeout(pollJob, 1500);
    appendLog(`等待中：${err.message}`);
  }
}

async function renderBuildResult(result) {
  if (!result) return;
  $("#job-panel").classList.add("hidden");
  const panel = $("#result-panel");
  panel.classList.remove("hidden");
  const projectId = state.buildProjectId || state.projectId;
  const fontUrl = projectFileUrl(projectId, result.font);
  const previewUrl = projectFileUrl(projectId, result.preview);
  const specimenUrl = projectFileUrl(projectId, result.specimen);
  const project = currentProject();

  $("#result-name").textContent = result.family || "我的手写体";
  $("#download-font").href = fontUrl;
  $("#download-font").setAttribute("download", result.font.split("/").pop());
  $("#link-preview").href = previewUrl;
  $("#link-specimen").href = specimenUrl;
  $("#link-pdf").href = project?.pdf || "#";

  const missing = result.missing || [];
  const warnings = result.warnings || [];
  const sizeText = result.info?.size ? ` · ${formatBytes(result.info.size)}` : "";
  $("#result-meta").textContent =
    `已提取 ${result.glyphs} / ${result.total} 个字${sizeText}`;

  const missingBox = $("#missing-box");
  if (missing.length || warnings.length) {
    missingBox.classList.remove("hidden");
    const parts = [];
    if (missing.length) {
      parts.push(`缺字 ${missing.length} 个：${missing.slice(0, 120).join("")}${missing.length > 120 ? "…" : ""}`);
    }
    if (warnings.length) parts.push("提示：" + warnings.join("；"));
    $("#missing-text").textContent = parts.join("\n");
  } else {
    missingBox.classList.add("hidden");
    $("#missing-text").textContent = "";
  }

  const size = parseInt($("#font-size").value, 10) || 48;
  const preview = $("#font-preview");
  if (project?.sample) preview.textContent = project.sample;
  preview.style.fontSize = `${size}px`;
  try {
    const family = `HandFontPreview_${Date.now()}`;
    const face = new FontFace(family, `url("${new URL(fontUrl, location.href).href}")`);
    await face.load();
    document.fonts.add(face);
    if (state.fontFace) document.fonts.delete(state.fontFace);
    state.fontFace = face;
    state.fontFamily = family;
    preview.style.fontFamily = `"${family}", var(--serif)`;
  } catch (err) {
    preview.style.fontFamily = "var(--serif)";
    toast("字体预览加载失败，但文件可以正常下载", "error");
  }
  panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ------------------------------------------------------------ events */

function bindEvents() {
  $("#btn-template").addEventListener("click", generateTemplate);
  $("#custom-text").addEventListener("input", updateEstimate);
  $("#cols").addEventListener("input", updateEstimate);
  $("#rows").addEventListener("input", updateEstimate);

  $("#project-select").addEventListener("change", (event) => {
    state.projectId = event.target.value;
    state.jobId = null;
    clearTimeout(state.pollTimer);
    const project = currentProject();
    if (project?.family) $("#family").value = project.family;
    $("#result-panel").classList.add("hidden");
    $("#job-panel").classList.add("hidden");
    $("#build-empty").classList.remove("hidden");
    renderProjectMeta();
    updateBuildState();
  });

  const dropzone = $("#dropzone");
  const input = $("#file-input");
  dropzone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("drag");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer?.files) addFiles(event.dataTransfer.files);
  });

  $("#btn-build").addEventListener("click", startBuild);
  $("#font-size").addEventListener("input", (event) => {
    const size = event.target.value;
    $("#font-size-val").textContent = `${size} px`;
    $("#font-preview").style.fontSize = `${size}px`;
  });
  $$("[data-sample]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#font-preview").textContent = btn.dataset.sample;
    });
  });
  $("#font-preview").addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, text);
  });
}

document.addEventListener("DOMContentLoaded", init);
