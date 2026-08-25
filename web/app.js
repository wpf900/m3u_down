const $ = (id) => document.getElementById(id);

const STATUS = {
  queued: "排队中",
  parsing: "解析中",
  downloading: "下载中",
  paused: "已暂停",
  merging: "合并中",
  done: "已完成",
  error: "失败",
  cancelled: "已取消",
};

const ICONS = {
  pause:
    '<svg viewBox="0 0 24 24"><rect x="7" y="5" width="3.5" height="14" rx="1"/><rect x="13.5" y="5" width="3.5" height="14" rx="1"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13L19 12Z"/></svg>',
  x: '<svg class="outline" viewBox="0 0 24 24"><path d="M7 7l10 10M17 7 7 17"/></svg>',
  folder:
    '<svg class="outline" viewBox="0 0 24 24"><path d="M3.5 7.5h6l1.4 1.6H20.5v9.4H3.5V7.5Z"/></svg>',
};

let folderPath = "";
let booted = false;
let ticking = false;
let pendingAction = null;
const hiddenMosaics = new Set();

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function prefs() {
  return {
    output_dir: folderPath,
    task_workers: Number($("taskWorkers").value || 3),
    segment_workers: Number($("segWorkers").value || 16),
    user_agent: $("ua").value.trim(),
    referer: $("referer").value.trim(),
    filename: $("filename").value.trim(),
  };
}

function applyBootstrap(data) {
  const s = data.settings || {};
  folderPath = s.output_dir || "";
  $("folderLabel").textContent = folderPath;
  $("taskWorkers").value = s.task_workers ?? 3;
  $("segWorkers").value = s.segment_workers ?? 16;
  $("ua").value = s.user_agent || "";
  $("referer").value = s.referer || "";
  if (!data.ffmpeg) {
    $("hint").textContent = "未检测到 ffmpeg，合并 MP4 会失败。";
  }
  render(data.tasks || []);
}

function btn(action, id, icon, title, extra) {
  const data = extra ? ` data-path="${escapeHtml(extra)}"` : "";
  return `<button class="icon-btn" data-act="${action}" data-id="${id}"${data} title="${title}">${icon}</button>`;
}

function actionsHTML(task) {
  const canPause = task.status === "downloading" || task.status === "parsing";
  const canResume = task.status === "paused";
  const canRetry = task.status === "error" || task.status === "cancelled";
  const canRemove = canRetry;
  const canCancel = !["done", "error", "cancelled"].includes(task.status);
  const canReveal = Boolean(task.output) && task.status === "done";
  return `
    ${canPause ? `<button class="text-btn" data-act="pause" data-id="${task.id}">暂停</button>` : ""}
    ${canResume ? `<button class="text-btn" data-act="resume" data-id="${task.id}">继续</button>` : ""}
    ${canRetry ? `<button class="text-btn" data-act="retry" data-id="${task.id}">重试</button>` : ""}
    ${canReveal ? btn("reveal", task.id, ICONS.folder, "显示", task.output) : ""}
    ${canCancel ? `<button class="text-btn danger-text" data-act="cancel" data-id="${task.id}" data-name="${escapeHtml(task.name)}">取消</button>` : ""}
    ${canRemove ? `<button class="text-btn danger-text" data-act="remove" data-id="${task.id}" data-name="${escapeHtml(task.name)}">删除</button>` : ""}
  `;
}

function actionKey(task) {
  return [task.status, task.output || "", task.name || ""].join("|");
}

function metaText(task) {
  return [task.total ? `${task.done}/${task.total}` : "", task.speed, task.eta, task.error]
    .filter(Boolean)
    .join("  ·  ");
}

function setBar(el, progress) {
  const fill = el.querySelector(".bar-fill");
  if (fill) {
    fill.style.transform = `scaleX(${Math.max(0, Math.min(100, Number(progress) || 0)) / 100})`;
  }
}

function setMosaic(el, taskId, map) {
  const wrap = el.querySelector(".mosaic-wrap");
  const box = el.querySelector(".mosaic");
  const toggle = el.querySelector(".mosaic-toggle");
  if (!wrap || !box) return;
  const next = map || "";
  if (!next) {
    wrap.hidden = true;
    box.replaceChildren();
    return;
  }
  const collapsed = hiddenMosaics.has(taskId);
  wrap.hidden = false;
  wrap.classList.toggle("collapsed", collapsed);
  box.hidden = collapsed;
  if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  if (box.childElementCount !== next.length) {
    const frag = document.createDocumentFragment();
    for (const ch of next) {
      const cell = document.createElement("b");
      cell.className = `s${ch}`;
      frag.appendChild(cell);
    }
    box.replaceChildren(frag);
    return;
  }
  const cells = box.children;
  for (let i = 0; i < next.length; i += 1) {
    const cls = `s${next[i]}`;
    if (cells[i].className !== cls) cells[i].className = cls;
  }
}

function toggleMosaic(taskId, wrap) {
  if (hiddenMosaics.has(taskId)) hiddenMosaics.delete(taskId);
  else hiddenMosaics.add(taskId);
  const collapsed = hiddenMosaics.has(taskId);
  wrap.classList.toggle("collapsed", collapsed);
  const box = wrap.querySelector(".mosaic");
  const toggle = wrap.querySelector(".mosaic-toggle");
  if (box) box.hidden = collapsed;
  if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function taskTemplate(task) {
  const article = document.createElement("article");
  article.className = "task";
  article.dataset.task = task.id;
  article.innerHTML = `
    <div class="task-top">
      <div class="name"></div>
      <span class="pill"></span>
    </div>
    <div class="url-line"></div>
    <div class="bar"><span class="bar-fill"></span></div>
    <div class="mosaic-wrap" hidden>
      <button type="button" class="mosaic-toggle" aria-expanded="true">
        <span>网格进度</span>
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 8l3-3.5"/></svg>
      </button>
      <div class="mosaic"></div>
    </div>
    <div class="task-meta">
      <span class="meta-text"></span>
      <div class="actions"></div>
    </div>`;
  patchTask(article, task);
  return article;
}

function patchTask(el, task) {
  el.querySelector(".name").textContent = task.name || "未命名视频";
  const pill = el.querySelector(".pill");
  pill.className = `pill ${task.status}`;
  pill.textContent = STATUS[task.status] || task.status;
  el.querySelector(".url-line").textContent = task.url || "";
  el.querySelector(".meta-text").textContent = metaText(task);
  const key = actionKey(task);
  if (el.dataset.actKey !== key) {
    el.dataset.actKey = key;
    el.querySelector(".actions").innerHTML = actionsHTML(task);
  }
  setBar(el, task.progress);
  setMosaic(el, task.id, task.mosaic);
}

function render(tasks) {
  const list = Array.isArray(tasks) ? tasks : [];
  $("empty").hidden = list.length > 0;
  const running = list.filter((t) =>
    ["queued", "parsing", "downloading", "paused", "merging"].includes(t.status)
  ).length;
  const done = list.filter((t) => t.status === "done").length;
  $("queueMeta").textContent = list.length ? `${running} 进行中 · ${done} 已完成` : "";

  const root = $("tasks");
  const seen = new Set();
  list.forEach((task, index) => {
    seen.add(task.id);
    let el = root.querySelector(`[data-task="${task.id}"]`);
    if (!el) {
      el = taskTemplate(task);
      const before = root.children[index];
      if (before) root.insertBefore(el, before);
      else root.appendChild(el);
      return;
    }
    if (el !== root.children[index]) {
      root.insertBefore(el, root.children[index] || null);
    }
    patchTask(el, task);
  });
  [...root.querySelectorAll("[data-task]")].forEach((el) => {
    if (!seen.has(el.dataset.task)) {
      hiddenMosaics.delete(el.dataset.task);
      el.remove();
    }
  });
}

async function start() {
  const hint = $("hint");
  hint.textContent = "";
  $("btnStart").disabled = true;
  try {
    const result = await pywebview.api.start_downloads($("urls").value, prefs());
    if (!result.ok) {
      hint.textContent = result.error || "无法开始";
      return;
    }
    $("urls").value = "";
    render(result.tasks || []);
    refresh();
  } catch (err) {
    hint.textContent = String(err);
  } finally {
    $("btnStart").disabled = false;
  }
}

function openConfirm(act, id, name) {
  pendingAction = { act, id };
  $("confirmName").textContent = name || "该视频";
  if (act === "remove") {
    $("confirmTitle").textContent = "删除任务？";
    $("confirmPrefix").textContent = "将从列表中移除「";
    $("confirmSuffix").textContent = "」。";
    $("confirmYes").textContent = "删除";
  } else {
    $("confirmTitle").textContent = "取消下载？";
    $("confirmPrefix").textContent = "将删除「";
    $("confirmSuffix").textContent = "」已下载的分片和未完成文件，此操作无法恢复。";
    $("confirmYes").textContent = "删除并取消";
  }
  $("confirm").hidden = false;
}

function closeConfirm() {
  pendingAction = null;
  $("confirm").hidden = true;
}

async function confirmPending() {
  const action = pendingAction;
  if (!action) return;
  closeConfirm();
  try {
    if (action.act === "remove") await pywebview.api.remove_task(action.id);
    else await pywebview.api.cancel_task(action.id);
  } catch (_) {
    /* ignore */
  }
  refresh();
}

async function refresh() {
  if (ticking || !window.pywebview?.api?.snapshot) return;
  ticking = true;
  try {
    const data = await pywebview.api.snapshot();
    render(data.tasks || data || []);
  } catch (_) {
    /* window may be closing */
  } finally {
    ticking = false;
  }
}

function bind() {
  $("btnClose").onclick = () => pywebview.api.win_close();
  $("btnMin").onclick = () => pywebview.api.win_min();
  $("btnZoom").onclick = () => pywebview.api.win_zoom();
  $("btnStart").onclick = start;
  $("btnHeaders").onclick = () => {
    $("headersPanel").hidden = !$("headersPanel").hidden;
  };
  $("btnFolder").onclick = async () => {
    const path = await pywebview.api.choose_folder();
    if (path) {
      folderPath = path;
      $("folderLabel").textContent = path;
    }
  };
  $("tasks").onclick = (event) => {
    const wrap = event.target.closest(".mosaic-wrap");
    if (wrap && !event.target.closest("button[data-act]")) {
      const article = wrap.closest("[data-task]");
      if (article) toggleMosaic(article.dataset.task, wrap);
      return;
    }
    const button = event.target.closest("button[data-act]");
    if (!button) return;
    const { act, id, path, name } = button.dataset;
    if (act === "pause") pywebview.api.pause_task(id).then(refresh);
    if (act === "resume") pywebview.api.resume_task(id).then(refresh);
    if (act === "retry") pywebview.api.retry_task(id).then(refresh);
    if (act === "cancel") openConfirm("cancel", id, name);
    if (act === "remove") openConfirm("remove", id, name);
    if (act === "reveal") pywebview.api.reveal(path);
  };
  $("confirmNo").onclick = closeConfirm;
  $("confirmYes").onclick = confirmPending;
  $("confirm").addEventListener("click", (event) => {
    if (event.target.id === "confirm") closeConfirm();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeConfirm();
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      start();
    }
  });
  ["taskWorkers", "segWorkers", "ua", "referer"].forEach((id) => {
    $(id).addEventListener("change", () => pywebview.api.save_prefs(prefs()));
  });
}

async function boot() {
  if (booted) return;
  booted = true;
  bind();
  applyBootstrap(await pywebview.api.get_bootstrap());
  setInterval(refresh, 250);
}

window.addEventListener("pywebviewready", boot);
if (window.pywebview?.api) boot();
