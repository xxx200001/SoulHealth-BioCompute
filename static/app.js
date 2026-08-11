/* SOULHEALTH Demo 前端逻辑（原生 JS，同源 /api）
   阶段五：登录鉴权（管理员/普通用户）、档案检索/找回（姓名+身份证后四位精确匹配，
   不再有年龄模糊匹配）、图片上传抽取 + 手动结构化录入并存、localStorage 会话恢复、
   历次分析回放、健康问答（含历史趋势） */
"use strict";
const $ = (s) => document.querySelector(s);
const state = { patientId: null, llmMode: "unconfigured", analyzing: false,
                token: null, user: null, sessionUploadCount: 0 };
const PID_KEY = "soulhealth.pid";
const TOKEN_KEY = "soulhealth.token";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sexText = (s) => (s === "female" ? "女" : s === "male" ? "男" : "—");
const fmtTime = (iso) => (iso || "").replace("T", " ").replace("Z", "");

function updateSessionDocCount(count) {
  state.sessionUploadCount = count;
  const badge = $("#docCountBadge");
  const subCount = $("#dropzoneCount");
  if (badge) {
    if (count > 0) {
      badge.textContent = `本次已加入 ${count} 张图片`;
      badge.classList.add("active");
    } else {
      badge.textContent = "本次未上传图片";
      badge.classList.remove("active");
    }
  }
  if (subCount) {
    subCount.textContent = count;
  }
}

function toast(msg, ms) {
  const t = $("#toast");
  const text = String(msg ?? "");
  const hold = ms || Math.min(4000 + text.length * 55, 20000);
  t.innerHTML = `<span class="toast-msg"></span>
    <button class="toast-x" type="button" aria-label="关闭">×</button>`;
  t.querySelector(".toast-msg").textContent = text;
  t.querySelector(".toast-x").addEventListener("click", () => t.classList.add("hidden"));
  t.classList.remove("hidden");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), hold);
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch (_) { /* 非 JSON */ }
  if (res.status === 401) {
    logout({ silent: true });
    throw new Error((data && data.detail) || "登录已失效，请重新登录");
  }
  if (!res.ok) throw new Error((data && data.detail) || `请求失败（${res.status}）`);
  return data;
}

const unlock = (sel) => $(sel).classList.remove("locked");
const lock = (sel) => $(sel).classList.add("locked");

/* ---------------- 登录 / 注册 / 会话 ---------------- */
function showAuthMask(show) {
  $("#authMask").classList.toggle("hidden", !show);
  $("#mainApp").classList.toggle("hidden", show);
  $("#userChip").classList.toggle("hidden", show);
}

function renderUserChip() {
  if (!state.user) return;
  $("#userLabel").textContent =
    `${state.user.display_name || state.user.username}（${state.user.role === "admin" ? "管理员" : "用户"}）`;
  $("#btnAdminPanel").hidden = state.user.role !== "admin";
}

async function doLogin() {
  const username = $("#loginUser").value.trim();
  const password = $("#loginPass").value;
  if (!username || !password) { authErr("请输入用户名和密码"); return; }
  try {
    const d = await api("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    onAuthed(d);
  } catch (e) { authErr(e.message); }
}

async function doRegister() {
  const username = $("#regUser").value.trim();
  const password = $("#regPass").value;
  const display_name = $("#regDisplay").value.trim() || null;
  if (!username || password.length < 6) {
    authErr("用户名不能为空，密码至少 6 位"); return;
  }
  try {
    const d = await api("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, display_name }),
    });
    onAuthed(d);
  } catch (e) { authErr(e.message); }
}

function authErr(msg) {
  const el = $("#authErr");
  el.textContent = msg; el.classList.remove("hidden");
}

function onAuthed(d) {
  state.token = d.token; state.user = d.user;
  localStorage.setItem(TOKEN_KEY, d.token);
  $("#authErr").classList.add("hidden");
  showAuthMask(false);
  renderUserChip();
  boot();
}

function logout({ silent = false } = {}) {
  state.token = null; state.user = null; state.patientId = null;
  localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(PID_KEY);
  showAuthMask(true);
  $("#loginPass").value = "";
  if (!silent) toast("已退出登录");
}

/* ---------------- 初始化 ---------------- */
async function init() {
  $$(".auth-tab").forEach((b) => b.addEventListener("click", () => {
    $$(".auth-tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#paneLogin").classList.toggle("hidden", b.dataset.tab !== "login");
    $("#paneRegister").classList.toggle("hidden", b.dataset.tab !== "register");
    $("#authErr").classList.add("hidden");
  }));

  const saved = localStorage.getItem(TOKEN_KEY);
  if (!saved) { showAuthMask(true); return; }
  state.token = saved;
  try {
    const d = await api("/api/auth/me");
    state.user = d.user;
    showAuthMask(false);
    renderUserChip();
    await boot();
  } catch (_) {
    logout({ silent: true });
  }
}

function $$(sel) { return [...document.querySelectorAll(sel)]; }

async function boot() {
  try {
    const h = await api("/api/health");
    state.llmMode = h.llm_mode;
    const chip = $("#modeChip");
    chip.classList.remove("real", "warn");
    const bio = h.biocompute_mode === "real"
      ? `生物计算 real${h.evo2_ready ? "+EVO2" : ""}` : "生物计算演示缓存";
    if (h.llm_mode === "real") {
      chip.textContent = `模型已连接 · ${h.llm_model} · ${bio}`;
      chip.classList.add("real");
      $("#btnSelftest").classList.remove("hidden");
    } else if (h.llm_mode === "mock") {
      chip.textContent = `演示模式（显式 MOCK）· ${bio}`;
    } else {
      chip.textContent = `未配置模型密钥 · 抽取/问答不可用 · ${bio}`;
      chip.classList.add("warn");
      $("#btnSelftest").classList.remove("hidden");
    }
    if (h.secret_key_is_default) {
      toast("提示：当前使用开发默认签名密钥（SOULHEALTH_SECRET_KEY 未设置），"
        + "仅适合本地演示；生产部署请务必设置为随机长字符串。", 6000);
    }
  } catch (e) { $("#modeChip").textContent = "服务未连接"; toast(e.message); }

  await loadPatients();
  const saved = localStorage.getItem(PID_KEY);
  if (saved) {
    try { await selectPatient(saved, { silent: true }); }
    catch (_) { localStorage.removeItem(PID_KEY); }
  }
}

/* ---------------- 00 档案库 ---------------- */
async function loadPatients(query = "") {
  const d = await api(`/api/patients${query ? `?query=${encodeURIComponent(query)}` : ""}`);
  const box = $("#patientList");
  if (!d.patients.length) {
    box.innerHTML = `<p class="hint">${query ? "未找到匹配档案。" : "尚无档案，请在下方建立。"}</p>`;
    return;
  }
  box.innerHTML = d.patients.map((p) => `
    <div class="patient-row ${p.id === state.patientId ? "active" : ""}" data-pid="${p.id}">
      <div class="pr-main"><b>${esc(p.name || p.pseudonym)}</b>
        <span>${sexText(p.sex)} · ${p.age_years ?? "—"} 岁${p.id_last4 ? ` · 尾号${esc(p.id_last4)}` : ""} · ${esc(p.pseudonym)}</span></div>
      <div class="pr-meta">指标 ${p.obs_count ?? 0} · 分析 ${p.analysis_count}
        <em>${esc(fmtTime(p.last_seen_at))}</em></div>
    </div>`).join("");
  box.querySelectorAll(".patient-row").forEach((row) =>
    row.addEventListener("click", () =>
      selectPatient(row.dataset.pid).catch((e) => toast(e.message))));
}

async function selectPatient(pid, { silent = false } = {}) {
  const s = await api(`/api/patients/${pid}`);
  state.patientId = pid;
  localStorage.setItem(PID_KEY, pid);
  updateSessionDocCount(0);
  const p = s.patient;
  $("#patientChip").textContent =
    `${p.name || p.pseudonym} · ${sexText(p.sex)} ${p.age_years ?? "—"} 岁 · ${p.pseudonym}`;
  $("#patientChip").classList.remove("hidden");
  $("#btnExit").classList.remove("hidden");
  $("#fName").value = p.name || "";
  $("#fId4").value = p.id_last4 || "";
  if (p.sex) $("#fSex").value = p.sex;
  $("#fAge").value = p.age_years ?? "";
  $("#fHeight").value = p.height_cm ?? "";
  $("#fWeight").value = p.weight_kg ?? "";
  unlock("#secUpload"); unlock("#secAsk"); unlock("#secAnalyze");
  $("#docList").innerHTML = ""; $("#results").innerHTML = "";
  $("#reportList").innerHTML = ""; $("#qaLog").innerHTML = "";
  await refreshArchive(s);
  await loadHistory();
  await loadPatients($("#searchInput").value.trim());
  if (!silent) toast(`已载入档案：${p.name || p.pseudonym}`);
}

function exitPatient() {
  state.patientId = null;
  localStorage.removeItem(PID_KEY);
  updateSessionDocCount(0);
  $("#patientChip").classList.add("hidden");
  $("#btnExit").classList.add("hidden");
  ["#docList", "#results", "#reportList", "#qaLog", "#historyList"].forEach(
    (s) => { $(s).innerHTML = ""; });
  $("#archivePanel").classList.add("hidden");
  $("#historyWrap").classList.add("hidden");
  lock("#secUpload"); lock("#secAnalyze"); lock("#secReports"); lock("#secAsk");
  loadPatients();
}

/* ---------------- 01 建档 / 找回 ---------------- */
async function createPatient() {
  const name = $("#fName").value.trim();
  if (!name) { toast("请填写姓名（用于档案匹配找回）"); $("#fName").focus(); return; }
  const id4 = $("#fId4").value.trim();
  if (id4 && !/^\d{3}[\dXx]$/.test(id4)) {
    toast("身份证后四位格式不对：应为 3 位数字 + 1 位数字或校验位 X，如 1234 / 123X");
    $("#fId4").focus(); return;
  }
  const payload = {
    name, id_last4: id4 || null,
    sex: $("#fSex").value,
    age_years: +$("#fAge").value || null,
    height_cm: +$("#fHeight").value || null,
    weight_kg: +$("#fWeight").value || null,
  };
  const d = await api("/api/patients", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await selectPatient(d.patient_id, { silent: true });
  if (!id4) {
    toast("新档案已建立（未填身份证后四位，无法自动找回，下次同名会新建一个档案）");
  } else {
    toast(d.created ? "新档案已建立" : "已找回既有档案（姓名+身份证后四位精确匹配），历史数据已载入");
  }
  return d;
}

async function loadDemo() {
  $("#fName").value = "演示患者"; $("#fId4").value = "0000";
  $("#fSex").value = "female"; $("#fAge").value = 25;
  $("#fHeight").value = 163; $("#fWeight").value = 83;
  const d = await createPatient();
  if (!d) return;
  if (state.llmMode === "mock") {
    for (const name of ["demo_超声报告.jpg", "demo_肝功化验.jpg"]) {
      const blob = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], { type: "image/jpeg" });
      await uploadOne(new File([blob], name, { type: "image/jpeg" }));
    }
    toast("演示患者已就绪：档案 + 超声 + 化验单（演示抽取）");
    return;
  }
  // 真实/未配置模式：图片抽取需要真实报告或密钥，改用手动录入注入同一份经典案例
  const obs = [
    { code: "ALT", display: "丙氨酸氨基转移酶", value_num: 97, unit: "U/L", ref_low: 0, ref_high: 40 },
    { code: "GGT", display: "谷氨酰转肽酶", value_num: 64, unit: "U/L", ref_low: 0, ref_high: 45 },
  ];
  for (const o of obs) await addObservation(o, { silent: true });
  await addFinding({ organ: "肝脏", description: "肝脏体积增大，回声增强，分布欠均匀",
    flags: ["回声增强", "欠均匀"] }, { silent: true });
  await addImpression({ text: "脂肪肝" }, { silent: true });
  toast(state.llmMode === "real"
    ? "档案已就绪（已用手动录入注入经典案例）；如需体验图片抽取请上传真实报告图片"
    : "档案已就绪（已用手动录入注入经典案例）；未配置模型密钥，图片抽取暂不可用", 5200);
}

/* ---------------- 02 上传（视觉抽取） ---------------- */
function renderDoc(r) {
  const ext = r.extraction;
  const chips = [];
  (ext.impressions || []).forEach((t) => chips.push(`<span class="chip hi">${esc(t)}</span>`));
  (ext.observations || []).forEach((o) => chips.push(
    `<span class="chip ${o.abnormal_flag === "H" ? "hi" : ""}">${esc(o.code)} ${esc(o.value_num ?? o.value_text)}${esc(o.unit || "")}${o.abnormal_flag === "H" ? "↑" : o.abnormal_flag === "L" ? "↓" : ""}</span>`));
  const typeMap = { ultrasound_report: "超声报告", lab_report: "化验单",
    clinical_note: "病历", other: "其他" };
  const div = document.createElement("div");
  div.className = "doc-item";
  div.innerHTML = `<div class="doc-top">
      <span class="doc-name">${esc(r.source_filename || "文件")}</span>
      <span class="tag ok">解析成功</span>
      <span class="tag">${esc(typeMap[ext.document_type] || ext.document_type)}</span>
      <span class="tag dai">${r.engine === "mock" ? "演示抽取" : esc(r.engine)}</span>
      <span class="tag">已脱敏</span></div>
    <div class="chip-row">${chips.join("") || "<span class='hint'>无关键抽取项</span>"}</div>`;
  $("#docList").appendChild(div);
}

async function uploadOne(file) {
  const fd = new FormData();
  fd.append("file", file); fd.append("patient_id", state.patientId);
  const r = await api("/api/documents/upload", { method: "POST", body: fd });
  r.source_filename = file.name;
  renderDoc(r);
  updateSessionDocCount(state.sessionUploadCount + 1);
  toast(`上传成功！已将 "${file.name}" 解析入档并提取关键指标。`, 4500);
  await refreshArchive();
  await loadPatients($("#searchInput").value.trim());
}

async function handleFiles(list) {
  if (!state.patientId) { toast("请先建立或选择健康档案"); return; }
  for (const f of list) {
    try { await uploadOne(f); }
    catch (e) {
      toast(`${f.name} 摄取失败：${e.message}`);
      if (/没有收到图像|selftest\/vision/.test(e.message)) await runVisionSelftest();
    }
  }
}

async function runVisionSelftest() {
  const box = $("#selftestBox");
  box.classList.remove("hidden");
  box.className = "selftest";
  box.textContent = "正在发送探测图，确认模型是否真的能收到图像…";
  try {
    const d = await api("/api/selftest/vision");
    box.classList.add(d.ok ? "ok" : "bad");
    box.textContent = `视觉自检：${d.ok ? "通过" : "未通过"} · 模型 ${d.model}`
      + `　${d.reason}` + (d.reply ? `　（模型回复：${d.reply}）` : "");
  } catch (e) {
    box.classList.add("bad");
    box.textContent = `视觉自检请求失败：${e.message}`;
  }
}

/* ---------------- 02 手动录入 / 备注 / 档案面板 ---------------- */
async function addObservation(preset, { silent = false } = {}) {
  const body = preset || {
    code: $("#obsCode").value.trim(),
    display: $("#obsDisplay").value.trim() || null,
    value_num: $("#obsValue").value === "" ? null : +$("#obsValue").value,
    unit: $("#obsUnit").value.trim() || null,
    ref_low: $("#obsRefLow").value === "" ? null : +$("#obsRefLow").value,
    ref_high: $("#obsRefHigh").value === "" ? null : +$("#obsRefHigh").value,
  };
  if (!body.code) { toast("请填写指标代码，如 ALT、GLU"); return; }
  await api(`/api/patients/${state.patientId}/observations`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!preset) ["obsCode", "obsDisplay", "obsValue", "obsUnit", "obsRefLow", "obsRefHigh"]
    .forEach((id) => { $(`#${id}`).value = ""; });
  await refreshArchive();
  if (!silent) { await loadPatients($("#searchInput").value.trim()); toast("指标已录入"); }
}

async function addFinding(preset, { silent = false } = {}) {
  const body = preset || {
    organ: $("#fdOrgan").value.trim(),
    description: $("#fdDesc").value.trim(),
    flags: $("#fdFlags").value.trim(),
  };
  if (!body.organ || !body.description) { toast("脏器/部位与所见描述均不能为空"); return; }
  await api(`/api/patients/${state.patientId}/findings`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!preset) ["fdOrgan", "fdDesc", "fdFlags"].forEach((id) => { $(`#${id}`).value = ""; });
  await refreshArchive();
  if (!silent) { await loadPatients($("#searchInput").value.trim()); toast("所见已录入"); }
}

async function addImpression(preset, { silent = false } = {}) {
  const body = preset || { text: $("#impText").value.trim() };
  if (!body.text) { toast("请填写提示原文"); return; }
  await api(`/api/patients/${state.patientId}/impressions`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!preset) $("#impText").value = "";
  await refreshArchive();
  if (!silent) { await loadPatients($("#searchInput").value.trim()); toast("诊断提示已录入"); }
}

async function saveNote() {
  const text = $("#noteInput").value.trim();
  if (!text) { toast("备注内容为空"); return; }
  await api(`/api/patients/${state.patientId}/notes`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  $("#noteInput").value = "";
  await refreshArchive();
  await loadPatients($("#searchInput").value.trim());
  toast("备注已入档");
}

async function refreshArchive(pre) {
  const s = pre || await api(`/api/patients/${state.patientId}`);
  const latest = Object.values(s.observations_latest || {});
  const obsChips = latest.map((o) =>
    `<span class="chip ${o.abnormal_flag === "H" ? "hi" : ""}">${esc(o.code)} ${esc(o.value_num ?? o.value_text)} ${esc(o.unit || "")}${o.abnormal_flag === "H" ? "↑" : o.abnormal_flag === "L" ? "↓" : ""}</span>`).join("");
  const findingChips = (s.findings || []).map((f) =>
    `<span class="chip">${esc(f.organ)}：${esc((f.flags || []).join("、") || f.description)}</span>`).join("");
  const impChips = (s.impressions || []).map((i) =>
    `<span class="chip hi">提示：${esc(i.text)}</span>`).join("");
  const notes = (s.notes || []).map((n) =>
    `<div class="note-item">「${esc(n.text)}」<em>${esc(fmtTime(n.created_at))}</em></div>`).join("");
  const panel = $("#archivePanel");
  panel.classList.remove("hidden");
  panel.innerHTML = `<h3>健康档案快照</h3>
    <div class="chip-row">${obsChips}${findingChips}${impChips}${(!obsChips && !findingChips && !impChips) ? '<span class="hint">尚未录入指标、所见或提示</span>' : ""}</div>
    ${notes ? `<div class="notes">${notes}</div>` : ""}
    <p class="hint">资料 ${s.documents.length} 份 · 指标 ${s.observations_timeline.length} 条 ·
      影像/查体所见 ${s.findings.length} 项 · 备注 ${(s.notes || []).length} 条 —— 即 AI Agent 的分析输入</p>`;
  unlock("#secAnalyze");
}

/* ---------------- 03 分析 + 历史回放 ---------------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function playRail(trace, fast = false) {
  const items = [...document.querySelectorAll("#railTrack li")];
  items.forEach((li) => { li.className = ""; li.querySelector("em").textContent = ""; });
  for (const step of trace || []) {
    const li = items.find((x) => x.dataset.step === step.step);
    if (!li) continue;
    li.classList.add("running");
    await sleep(fast ? 120 : Math.min(Math.max(step.ms, 320), 900));
    li.classList.remove("running"); li.classList.add("done");
    li.querySelector("em").textContent = `${step.ms} ms · ${step.detail.slice(0, 26)}…`;
    li.title = step.detail;
  }
}

const block = (title, inner) =>
  `<div class="result-block"><h3>${esc(title)}</h3>${inner}</div>`;

function renderResults(d) {
  const sev = { info: "info", watch: "watch", high: "high" };
  const sevText = { info: "提示", watch: "关注", high: "建议就医评估" };
  const risks = (d.risk_tags || []).map((t) => `
    <div class="risk ${sev[t.severity] || "info"}"><b>${esc(t.label)} · ${sevText[t.severity] || ""}</b>
      <ul>${t.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`).join("")
    || `<p class="hint">未识别出显著风险标签；建议保持定期体检随访。</p>`;

  const syn = (d.syndrome_tags || []).map((s) => `
    <div class="risk syn"><b>${esc(s.label)} · 自述参考</b>
      <ul>${s.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`).join("");

  const levels = (d.mechanism_chain && d.mechanism_chain.levels) || [];
  const chain = levels.filter((l) => l.items.length).map((l) => `
    <div class="chain-level"><span class="chain-tag">${esc(l.level)}</span>
      <div class="chain-items">${l.items.map((i) => `<span>· ${esc(i)}</span>`).join("")}</div></div>`).join("")
    || `<p class="hint">无机制链条目。</p>`;

  const f = d.formula;
  let formulaHtml;
  if (f && f.ingredients && f.ingredients.length) {
    const head = f.formula_name
      ? `<div class="formula-head"><b>${esc(f.formula_name)}</b>
           <span class="formula-src">${esc(f.source || "")}</span>
           <span class="formula-principle">治则：${esc(f.treatment_principle || "")}</span></div>`
      : "";
    const modLog = (f.modification_log || []).length
      ? `<details class="mod-log"><summary>加减与化裁依据（${f.modification_log.length} 条，点击展开）</summary>
           <ul>${f.modification_log.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></details>`
      : "";
    formulaHtml = head + `<div class="tbl-wrap"><table class="tbl"><tr><th>原料</th><th>用量</th><th>角色</th><th>本方要点</th></tr>
      ${f.ingredients.map((i) => `<tr><td>${esc(i.display)}</td><td>${i.grams}g</td><td>${esc(i.role)}</td><td>${esc(i.purpose)}</td></tr>`).join("")}</table></div>`
      + f.substitutions.map((s) => `
      <div class="sub-stamp"><b>目录门禁 · 已替换</b><span>${esc(s.reason)}；已由目录内的「${esc(s.replaced_by)}」承接。</span></div>`).join("")
      + modLog
      + `<p class="hint">冲泡：${esc(f.brew.water_ml)}ml，${esc(f.brew.steep)}；${esc(f.brew.schedule)}</p>`;
  } else {
    formulaHtml = `<p class="hint">本次未生成代茶饮配方（无风险标签/证型，不硬凑组方）。</p>`;
  }

  const bio = (d.biocompute_plan || []).map((b) => {
    const srcMap = { mock_cache: ["warn", "演示缓存"], afdb_api: ["dai", "AlphaFold DB"],
      "nim+ensembl": ["dai", "EVO2+Ensembl"], ensembl: ["dai", "Ensembl 实时"],
      uniprot_api: ["dai", "UniProt"] };
    const [cls, txt] = srcMap[b.source] || ["", b.source || "—"];
    const src = `<span class="tag ${cls}">${esc(txt)}</span>`;
    if (b.service === "alphafold_db" && b.status === "done") {
      return `<div class="bio"><span class="g">${esc(b.gene)}</span> <span class="u">${esc(b.uniprot)}</span> ${src}
        <div class="bar"><i style="width:${Math.min(b.mean_plddt || 0, 100)}%"></i></div>
        <div>平均 pLDDT <span class="num">${esc(b.mean_plddt)}</span></div>
        <a href="${esc(b.page_url)}" target="_blank" rel="noopener">AlphaFold DB 结构页 →</a></div>`;
    }
    if (b.service === "evo2" && b.status === "done") {
      const loc = b.chrom ? `chr${esc(b.chrom)}:${esc(b.pos)} ` : "";
      const pct = b.percentile != null ? ` · 演示背景第 ${esc(b.percentile)} 百分位` : "";
      return `<div class="bio"><span class="g">${esc(b.gene)}</span> <span class="u">${esc(b.variant)}</span> ${src}
        <div>Δ logL <span class="num">${esc(b.delta_ll)}</span></div>
        <div class="hint">${loc}变异 vs 参考序列${pct}</div></div>`;
    }
    if (b.service === "evo2" && b.status === "skipped") {
      const loc = b.chrom ? `位点 chr${esc(b.chrom)}:${esc(b.pos)} ${esc(b.ref)}>${esc(b.alt)}（Ensembl 实时）` : "";
      return `<div class="bio pending"><span class="g">${esc(b.gene)}</span> <span class="u">${esc(b.variant)}</span> ${src}
        <div>${loc}</div><div class="hint">序列打分未执行：未配置 NVIDIA_API_KEY（不出演示分数）</div></div>`;
    }
    return `<div class="bio pending"><span class="g">${esc(b.gene)}</span> · ${esc(b.service)}<br>
      ${esc(b.note || b.status)}</div>`;
  }).join("") || `<div class="bio-empty">${esc(
      (d.mechanism_chain && d.mechanism_chain.biocompute_applicability)
      || "本次无生物计算调用。")}</div>`;

  const interp = d.interpretation || {};
  function renderMd(t) {
    if (!t) return "";
    let h = esc(t);
    h = h.replace(/^#+\s*(.*)$/gm, '<h4 class="md-h4">$1</h4>');
    h = h.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/\n/g, '<br>');
    return h;
  }
  const interpHtml = interp.available
    ? `<div class="interp-text">${renderMd(interp.text)}</div>
       <p class="hint">由大模型（${esc(interp.model || "")}）通读本次结构化结果生成，已过合规校验；供参考，不构成诊疗意见。</p>`
    : `<div class="interp-off">${esc(interp.reason || "本次未生成 AI 综合解读。")}</div>`;

  $("#results").innerHTML =
    block("AI 综合解读", interpHtml) +
    block("健康风险识别", `<div class="risk-grid">${risks}${syn}</div>`) +
    block("机制解释链", `<div class="chain">${chain}</div>` +
      (d.mechanism_chain && d.mechanism_chain.note
        ? `<p class="hint">${esc(d.mechanism_chain.note)}</p>` : "")) +
    block("药食同源组方（含目录校验）", formulaHtml) +
    block("生物计算辅助", `<div class="bio-grid">${bio}</div>`);

  const groups = {};
  (d.reports || []).forEach((r) => {
    (groups[r.report_type] ??= { title: r.title, items: [] }).items.push(r);
  });
  $("#reportList").innerHTML = Object.values(groups).map((g) => `
    <div class="report"><b>${esc(g.title)}</b>
      ${g.items.map((r) => `<a class="btn ${r.format === "docx" ? "primary" : "ghost"}"
        href="${esc(r.download_url)}?token=${encodeURIComponent(state.token || "")}" target="_blank" download>下载 ${r.format === "docx" ? "Word" : "Markdown"}</a>`).join("")}
    </div>`).join("") || `<p class="hint">本次分析无报告产物。</p>`;
  unlock("#secReports");
}

async function loadHistory() {
  const d = await api(`/api/patients/${state.patientId}/analyses`);
  const wrap = $("#historyWrap");
  if (!d.analyses.length) { wrap.classList.add("hidden"); $("#historyList").innerHTML = ""; return; }
  wrap.classList.remove("hidden");
  $("#historyList").innerHTML = d.analyses.map((a) => `
    <button class="history-item" data-aid="${a.id}">
      ${esc(fmtTime(a.created_at))} · ${esc(a.id.slice(0, 8))} <em>回放 →</em>
    </button>`).join("");
  $("#historyList").querySelectorAll(".history-item").forEach((b) =>
    b.addEventListener("click", () =>
      viewAnalysis(b.dataset.aid).catch((e) => toast(e.message))));
  unlock("#secAnalyze");
}

async function viewAnalysis(aid) {
  const d = await api(`/api/analyses/${aid}`);
  $("#analyzeHint").textContent = `回放历史分析 ${aid.slice(0, 8)}（${fmtTime(d.created_at)}）`;
  await playRail(d.trace, true);
  renderResults(d);
  $("#results").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function analyze() {
  if (state.analyzing || !state.patientId) return;
  state.analyzing = true;
  const btn = $("#btnAnalyze");
  btn.disabled = true; $("#analyzeHint").textContent = "Agent 分析中…";
  $("#results").innerHTML = ""; $("#reportList").innerHTML = "";
  try {
    const d = await api("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_id: state.patientId }),
    });
    await playRail(d.trace);
    renderResults(d);
    await loadHistory();
    await loadPatients($("#searchInput").value.trim());
    $("#analyzeHint").textContent = "分析完成，结果已入档（可在历次分析中回放）";
    $("#secReports").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) {
    $("#analyzeHint").textContent = "";
    toast(`分析失败：${e.message}`, 6000);
  } finally { state.analyzing = false; btn.disabled = false; }
}

/* ---------------- 05 健康问答 ---------------- */
async function ask() {
  const q = $("#qaInput").value.trim();
  if (!state.patientId) { toast("请先选择或建立档案"); return; }
  if (!q) { toast("请输入问题"); return; }
  if ($("#btnAsk").disabled) return;
  const log = $("#qaLog");
  log.insertAdjacentHTML("beforeend", `<div class="qa-q">${esc(q)}</div>
    <div class="qa-a pending">思考中…</div>`);
  const pendingEl = log.lastElementChild;
  log.scrollTop = log.scrollHeight;
  $("#qaInput").value = ""; $("#btnAsk").disabled = true;
  try {
    const d = await api(`/api/patients/${state.patientId}/ask`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    });
    pendingEl.classList.remove("pending");
    pendingEl.innerHTML = `${esc(d.answer).replace(/\n/g, "<br>")}
      <div class="qa-dis">${esc(d.disclaimer)}</div>`;
  } catch (e) {
    pendingEl.classList.remove("pending");
    pendingEl.classList.add("err");
    pendingEl.textContent = e.message;
  } finally {
    $("#btnAsk").disabled = false;
    log.scrollTop = log.scrollHeight;
  }
}

/* ---------------- 管理员面板 ---------------- */
async function openAdmin() {
  $("#adminMask").classList.remove("hidden");
  await renderAdminUsers();
}

async function renderAdminUsers() {
  const d = await api("/api/admin/users");
  $("#adminUserList").innerHTML = d.users.map((u) => `
    <div class="admin-user-row">
      <div><b>${esc(u.display_name || u.username)}</b>
        <span class="hint">@${esc(u.username)} · ${u.role === "admin" ? "管理员" : "用户"}
          · 档案 ${u.patient_count} · ${u.disabled ? "已停用" : "启用中"}</span></div>
      <div class="au-actions">
        <button class="btn ghost tiny" data-act="toggle" data-uid="${u.id}"
          data-disabled="${u.disabled ? 0 : 1}">${u.disabled ? "启用" : "停用"}</button>
        <button class="btn ghost tiny danger" data-act="del" data-uid="${u.id}">删除</button>
      </div>
    </div>`).join("");
  $("#adminUserList").querySelectorAll("[data-act]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const uid = btn.dataset.uid;
      try {
        if (btn.dataset.act === "toggle") {
          await api(`/api/admin/users/${uid}`, {
            method: "PATCH", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ disabled: btn.dataset.disabled === "1" }),
          });
        } else if (btn.dataset.act === "del") {
          if (!confirm("确认删除该用户？名下档案会保留但归属清空。")) return;
          await api(`/api/admin/users/${uid}`, { method: "DELETE" });
        }
        await renderAdminUsers();
      } catch (e) { toast(e.message); }
    }));
}

async function adminCreateUser() {
  const username = $("#auName").value.trim();
  const password = $("#auPass").value;
  const role = $("#auRole").value;
  if (!username || password.length < 6) { toast("用户名不能为空，密码至少 6 位"); return; }
  try {
    await api("/api/admin/users", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, role }),
    });
    $("#auName").value = ""; $("#auPass").value = "";
    await renderAdminUsers();
    toast("用户已创建");
  } catch (e) { toast(e.message); }
}

/* ---------------- 事件绑定 ---------------- */
$("#btnLogin").addEventListener("click", () => doLogin());
$("#btnRegister").addEventListener("click", () => doRegister());
$("#loginPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
$("#btnLogout").addEventListener("click", () => logout());
$("#btnAdminPanel").addEventListener("click", () => openAdmin().catch((e) => toast(e.message)));
$("#btnCloseAdmin").addEventListener("click", () => $("#adminMask").classList.add("hidden"));
$("#btnAdminCreate").addEventListener("click", () => adminCreateUser());

$("#btnCreate").addEventListener("click", () =>
  createPatient().catch((e) => toast(e.message)));
$("#btnDemo").addEventListener("click", () => loadDemo().catch((e) => toast(e.message)));
$("#btnExit").addEventListener("click", exitPatient);
$("#btnRefreshList").addEventListener("click", () =>
  loadPatients($("#searchInput").value.trim()).catch((e) => toast(e.message)));
$("#searchInput").addEventListener("input", () => {
  clearTimeout(state._sh);
  state._sh = setTimeout(() =>
    loadPatients($("#searchInput").value.trim()).catch(() => {}), 260);
});
$("#btnAnalyze").addEventListener("click", analyze);
$("#btnAddObs").addEventListener("click", () => addObservation().catch((e) => toast(e.message)));
$("#btnAddFinding").addEventListener("click", () => addFinding().catch((e) => toast(e.message)));
$("#btnAddImpression").addEventListener("click", () => addImpression().catch((e) => toast(e.message)));
$("#btnNote").addEventListener("click", () => saveNote().catch((e) => toast(e.message)));
$("#btnAsk").addEventListener("click", () => ask());
$("#btnSelftest").addEventListener("click", () => runVisionSelftest());
$("#qaInput").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });
$("#fileInput").addEventListener("change", (e) => handleFiles([...e.target.files]));
const dz = $("#dropzone");
dz.addEventListener("click", () => $("#fileInput").click());
["dragover", "dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => {
    e.preventDefault();
    dz.classList.toggle("drag", ev === "dragover");
    if (ev === "drop") handleFiles([...e.dataTransfer.files]);
  }));

init();
