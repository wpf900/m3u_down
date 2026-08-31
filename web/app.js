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
let activeTab = "active";
let selectMode = false;
const selected = new Set();
const hiddenMosaics = new Set();
const collapsedHistoryDates = new Set();
let lastSnapshot = { active: [], history: [] };
let historySearchQuery = "";
const collapsedHistorySeries = new Set();

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

function tasksInCurrentTab() {
  if (activeTab === "history") {
    return historyGroupsForView().flatMap((group) => group.tasks || []);
  }
  return lastSnapshot.active || [];
}

function taskMatchesSearch(task, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  return (
    String(task.series || "").toLowerCase().includes(q) ||
    String(task.name || "").toLowerCase().includes(q)
  );
}

function historyGroupsForView() {
  const q = historySearchQuery.trim();
  if (!q) return lastSnapshot.history || [];
  return (lastSnapshot.history || [])
    .map((group) => ({
      ...group,
      tasks: (group.tasks || []).filter((task) => taskMatchesSearch(task, q)),
    }))
    .filter((group) => group.tasks.length > 0);
}

function historySeriesKey(date, series) {
  return `${date}|${series || "__none__"}`;
}

function toggleHistorySeries(date, seriesKey) {
  if (seriesKey === "__flat__") return;
  const key = historySeriesKey(date, seriesKey === "__none__" ? "" : seriesKey);
  if (collapsedHistorySeries.has(key)) collapsedHistorySeries.delete(key);
  else collapsedHistorySeries.add(key);
  const scope = CSS.escape(date);
  const groupKey = CSS.escape(seriesKey);
  const wrap = $("history").querySelector(
    `[data-history-date="${scope}"] [data-series-group="${groupKey}"]`
  );
  if (!wrap) return;
  const collapsed = collapsedHistorySeries.has(key);
  wrap.classList.toggle("collapsed", collapsed);
  const toggle = wrap.querySelector(".series-toggle");
  const items = wrap.querySelector(".series-items");
  if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  if (items) items.hidden = collapsed;
}

function selectedTasks() {
  return tasksInCurrentTab().filter((task) => selected.has(task.id));
}

function canRetryTask(task) {
  return task.status === "error" || task.status === "cancelled";
}

function canRemoveTask(task) {
  return task.archived || canRetryTask(task) || task.status === "done";
}

function canCancelTask(task) {
  return !["done", "error", "cancelled"].includes(task.status);
}

function canPauseTask(task) {
  return task.status === "downloading" || task.status === "parsing";
}

function canResumeTask(task) {
  return task.status === "paused";
}

function setSelectMode(on) {
  selectMode = on;
  if (!on) selected.clear();
  document.body.classList.toggle("select-mode", on);
  $("btnSelectMode").classList.toggle("active", on);
  $("btnSelectMode").textContent = on ? "完成" : "多选";
  $("batchBar").hidden = !on;
  updateBatchBar();
  syncTaskChecks();
}

function syncTaskChecks() {
  document.querySelectorAll(".task[data-task]").forEach((el) => {
    const checkWrap = el.querySelector(".task-check");
    const input = el.querySelector(".pick");
    if (!checkWrap || !input) return;
    checkWrap.hidden = !selectMode;
    input.checked = selected.has(el.dataset.task);
    el.classList.toggle("selected", selected.has(el.dataset.task));
  });
  syncSeriesChecks();
  updateSelectAllState();
}

function syncSeriesChecks() {
  document.querySelectorAll("[data-series-group]").forEach((wrap) => {
    if (wrap.dataset.seriesGroup === "__flat__") return;
    const checkWrap = wrap.querySelector(".series-check");
    const pick = wrap.querySelector(".series-pick");
    if (!checkWrap || !pick) return;
    checkWrap.hidden = !selectMode;
    const ids = [...wrap.querySelectorAll(".task[data-task]")].map((el) => el.dataset.task);
    const picked = ids.filter((id) => selected.has(id)).length;
    pick.checked = ids.length > 0 && picked === ids.length;
    pick.indeterminate = picked > 0 && picked < ids.length;
    wrap.classList.toggle("series-selected", picked > 0 && picked === ids.length);
  });
}

function toggleSeriesGroup(wrap, checked) {
  wrap.querySelectorAll(".task[data-task]").forEach((el) => {
    if (checked) selected.add(el.dataset.task);
    else selected.delete(el.dataset.task);
  });
  syncTaskChecks();
  updateBatchBar();
}

function updateSelectAllState() {
  const visible = tasksInCurrentTab();
  const allSelected = visible.length > 0 && visible.every((task) => selected.has(task.id));
  $("selectAll").checked = allSelected;
  $("selectAll").indeterminate =
    !allSelected && visible.some((task) => selected.has(task.id));
}

function updateBatchBar() {
  const picked = selectedTasks();
  $("selectedCount").textContent = `已选 ${picked.length}`;
  const show = (id, visible) => {
    $(id).hidden = !visible;
  };
  if (activeTab === "active") {
    show("batchPause", picked.some(canPauseTask));
    show("batchResume", picked.some(canResumeTask));
    show("batchRetry", picked.some(canRetryTask));
    show("batchCancel", picked.some(canCancelTask));
    show("batchRemove", picked.some(canRemoveTask));
  } else {
    show("batchPause", false);
    show("batchResume", false);
    show("batchRetry", picked.some(canRetryTask));
    show("batchCancel", false);
    show("batchRemove", picked.some(canRemoveTask));
  }
  updateSelectAllState();
}

function toggleTaskSelection(taskId, checked) {
  if (checked) selected.add(taskId);
  else selected.delete(taskId);
  syncTaskChecks();
  updateBatchBar();
}

function toggleSelectAll(checked) {
  tasksInCurrentTab().forEach((task) => {
    if (checked) selected.add(task.id);
    else selected.delete(task.id);
  });
  syncTaskChecks();
  updateBatchBar();
}

function formatHistoryDate(dateKey) {
  if (!dateKey || dateKey === "未知日期") return "未知日期";
  const today = new Date();
  const key = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  if (dateKey === key) return "今天";
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const yKey = `${yesterday.getFullYear()}-${String(yesterday.getMonth() + 1).padStart(2, "0")}-${String(yesterday.getDate()).padStart(2, "0")}`;
  if (dateKey === yKey) return "昨天";
  const [year, month, day] = dateKey.split("-");
  if (Number(year) === today.getFullYear()) return `${Number(month)}月${Number(day)}日`;
  return `${year}年${Number(month)}月${Number(day)}日`;
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
  renderAll(data);
}

function btn(action, id, icon, title, extra) {
  const data = extra ? ` data-path="${escapeHtml(extra)}"` : "";
  return `<button class="icon-btn" data-act="${action}" data-id="${id}"${data} title="${title}">${icon}</button>`;
}

function actionsHTML(task, compact = false) {
  const canPause = !compact && (task.status === "downloading" || task.status === "parsing");
  const canResume = !compact && task.status === "paused";
  const canRetry = task.status === "error" || task.status === "cancelled";
  const canRemove = compact || canRetry || task.status === "done";
  const canCancel = !compact && !["done", "error", "cancelled"].includes(task.status);
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
  return [task.status, task.output || "", task.name || "", task.error || "", task.series || ""].join("|");
}

function metaText(task, compact = false) {
  if (compact) {
    return [task.error, task.output ? "已保存" : ""].filter(Boolean).join("  ·  ");
  }
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

function taskTemplate(task, compact = false) {
  const article = document.createElement("article");
  article.className = compact ? "task task-compact" : "task";
  article.dataset.task = task.id;
  article.innerHTML = `
    <label class="task-check" hidden>
      <input type="checkbox" class="pick" data-id="${task.id}" />
    </label>
    <div class="task-top">
      <div class="title-block">
        <div class="series-tag" hidden></div>
        <div class="name"></div>
      </div>
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
  patchTask(article, task, compact);
  return article;
}

function patchTask(el, task, compact = false, opts = {}) {
  const hideSeries = Boolean(opts.hideSeries);
  const seriesEl = el.querySelector(".series-tag");
  if (task.series && !hideSeries) {
    seriesEl.textContent = task.series;
    seriesEl.hidden = false;
  } else {
    seriesEl.textContent = "";
    seriesEl.hidden = true;
  }
  el.querySelector(".name").textContent = task.name || "未命名视频";
  const pill = el.querySelector(".pill");
  pill.className = `pill ${task.status}`;
  pill.textContent = STATUS[task.status] || task.status;
  const urlLine = el.querySelector(".url-line");
  urlLine.textContent = compact ? "" : task.url || "";
  urlLine.hidden = compact;
  el.querySelector(".meta-text").textContent = metaText(task, compact);
  const key = actionKey(task);
  const optsKey = `${compact}:${hideSeries}`;
  if (el.dataset.actKey !== key || el.dataset.compact !== optsKey) {
    el.dataset.actKey = key;
    el.dataset.compact = optsKey;
    el.querySelector(".actions").innerHTML = actionsHTML(task, compact);
  }
  setBar(el, task.progress);
  if (compact) {
    const wrap = el.querySelector(".mosaic-wrap");
    if (wrap) wrap.hidden = true;
  } else {
    setMosaic(el, task.id, task.mosaic);
  }
  const checkWrap = el.querySelector(".task-check");
  const input = el.querySelector(".pick");
  if (checkWrap && input) {
    checkWrap.hidden = !selectMode;
    input.checked = selected.has(task.id);
    el.classList.toggle("selected", selected.has(task.id));
  }
}

function groupTasksBySeries(tasks) {
  const groups = new Map();
  const order = [];
  (tasks || []).forEach((task) => {
    const key = task.series || "";
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key).push(task);
  });
  return order.map((series) => ({ series, tasks: groups.get(series) }));
}

function renderTaskList(root, tasks, compact = false, opts = {}) {
  const list = Array.isArray(tasks) ? tasks : [];
  const seen = new Set();
  list.forEach((task, index) => {
    seen.add(task.id);
    let el = root.querySelector(`[data-task="${task.id}"]`);
    if (!el) {
      el = taskTemplate(task, compact);
      const before = root.children[index];
      if (before) root.insertBefore(el, before);
      else root.appendChild(el);
    } else if (el !== root.children[index]) {
      root.insertBefore(el, root.children[index] || null);
    }
    patchTask(el, task, compact, opts);
  });
  [...root.querySelectorAll("[data-task]")].forEach((el) => {
    if (!seen.has(el.dataset.task)) {
      hiddenMosaics.delete(el.dataset.task);
      el.remove();
    }
  });
  return list;
}

function renderGroupedTaskList(root, tasks, compact = false) {
  const groups = groupTasksBySeries(tasks);
  const seenGroups = new Set();
  const seenTasks = new Set();

  groups.forEach((group, groupIndex) => {
    const groupKey = group.series || "__none__";
    seenGroups.add(groupKey);
    let wrap = root.querySelector(`[data-series-group="${groupKey}"]`);
    if (!wrap) {
      wrap = document.createElement("section");
      wrap.className = "series-group";
      wrap.dataset.seriesGroup = groupKey;
      wrap.innerHTML = `
        <div class="series-head">
          <label class="series-check" hidden>
            <input type="checkbox" class="series-pick" />
          </label>
          <span class="series-name"></span>
          <span class="series-count"></span>
        </div>
        <div class="series-items tasks"></div>`;
      const before = root.children[groupIndex];
      if (before) root.insertBefore(wrap, before);
      else root.appendChild(wrap);
    } else if (wrap !== root.children[groupIndex]) {
      root.insertBefore(wrap, root.children[groupIndex] || null);
    }

    const head = wrap.querySelector(".series-head");
    const items = wrap.querySelector(".series-items");
    if (group.series) {
      head.hidden = false;
      head.querySelector(".series-name").textContent = group.series;
      head.querySelector(".series-count").textContent = `${group.tasks.length} 集`;
    } else {
      head.hidden = true;
    }
    renderTaskList(items, group.tasks, compact, { hideSeries: Boolean(group.series) });
    group.tasks.forEach((task) => seenTasks.add(task.id));
  });

  [...root.querySelectorAll("[data-series-group]")].forEach((el) => {
    if (!seenGroups.has(el.dataset.seriesGroup)) el.remove();
  });
  return tasks || [];
}

function renderHistorySeriesList(root, tasks, dateKey, todayKey) {
  const groups = groupTasksBySeries(tasks);
  const seenGroups = new Set();
  const searching = Boolean(historySearchQuery.trim());

  groups.forEach((group, groupIndex) => {
    const groupKey = group.series || "__flat__";
    seenGroups.add(groupKey);

    if (!group.series) {
      let flat = root.querySelector('[data-series-group="__flat__"]');
      if (!flat) {
        flat = document.createElement("div");
        flat.className = "series-flat tasks";
        flat.dataset.seriesGroup = "__flat__";
        root.appendChild(flat);
      }
      if (flat !== root.children[groupIndex]) {
        root.insertBefore(flat, root.children[groupIndex] || null);
      }
      renderTaskList(flat, group.tasks, true, { hideSeries: false });
      return;
    }

    let wrap = root.querySelector(`[data-series-group="${groupKey}"]`);
    if (!wrap) {
      wrap = document.createElement("section");
      wrap.className = "series-group history-series";
      wrap.dataset.seriesGroup = groupKey;
      wrap.innerHTML = `
        <div class="series-head-row">
          <label class="series-check" hidden>
            <input type="checkbox" class="series-pick" />
          </label>
          <button type="button" class="series-toggle" aria-expanded="true">
            <span class="series-name"></span>
            <span class="series-count"></span>
            <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 8l3-3.5"/></svg>
          </button>
        </div>
        <div class="series-items tasks"></div>`;
      root.appendChild(wrap);
    }
    if (wrap !== root.children[groupIndex]) {
      root.insertBefore(wrap, root.children[groupIndex] || null);
    }

    const collapseKey = historySeriesKey(dateKey, group.series);
    if (!wrap.dataset.inited) {
      wrap.dataset.inited = "1";
      if (!searching && dateKey !== todayKey) collapsedHistorySeries.add(collapseKey);
    }
    if (searching) collapsedHistorySeries.delete(collapseKey);

    const collapsed = collapsedHistorySeries.has(collapseKey);
    wrap.classList.toggle("collapsed", collapsed);
    wrap.querySelector(".series-name").textContent = group.series;
    wrap.querySelector(".series-count").textContent = `${group.tasks.length} 集`;
    const toggle = wrap.querySelector(".series-toggle");
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    const items = wrap.querySelector(".series-items");
    items.hidden = collapsed;
    renderTaskList(items, group.tasks, true, { hideSeries: true });
  });

  [...root.querySelectorAll(":scope > .history-series, :scope > .series-flat")].forEach((el) => {
    if (!seenGroups.has(el.dataset.seriesGroup)) el.remove();
  });
}

function renderActive(tasks) {
  const list = renderGroupedTaskList($("tasks"), tasks, false);
  $("empty").hidden = list.length > 0;
  const running = list.filter((t) =>
    ["queued", "parsing", "downloading", "paused", "merging"].includes(t.status)
  ).length;
  $("queueMeta").textContent = list.length ? `${running} 进行中` : "";
  return list;
}

function toggleHistoryDate(date) {
  if (collapsedHistoryDates.has(date)) collapsedHistoryDates.delete(date);
  else collapsedHistoryDates.add(date);
  const group = $("history").querySelector(`[data-history-date="${CSS.escape(date)}"]`);
  if (!group) return;
  const collapsed = collapsedHistoryDates.has(date);
  group.classList.toggle("collapsed", collapsed);
  const toggle = group.querySelector(".history-toggle");
  const items = group.querySelector(".history-items");
  if (toggle) toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  if (items) items.hidden = collapsed;
}
function switchTab(tab) {
  activeTab = tab;
  selected.clear();
  const isActive = tab === "active";
  $("tabActive").classList.toggle("active", isActive);
  $("tabHistory").classList.toggle("active", !isActive);
  $("tabActive").setAttribute("aria-selected", isActive ? "true" : "false");
  $("tabHistory").setAttribute("aria-selected", isActive ? "false" : "true");
  $("panelActive").hidden = !isActive;
  $("panelHistory").hidden = isActive;
  $("queueMeta").hidden = !isActive;
  $("historyMeta").hidden = isActive;
  updateBatchBar();
  syncTaskChecks();
}

function renderHistory(groups) {
  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const root = $("history");
  const allGroups = lastSnapshot.history || [];
  const allTotal = allGroups.reduce((sum, group) => sum + (group.tasks?.length || 0), 0);
  const list = historyGroupsForView();
  const visibleTotal = list.reduce((sum, group) => sum + (group.tasks?.length || 0), 0);
  const searching = Boolean(historySearchQuery.trim());

  $("historySearchWrap").hidden = allTotal === 0;
  $("historyEmpty").hidden = allTotal > 0;
  $("historySearchEmpty").hidden = !searching || visibleTotal > 0 || allTotal === 0;
  root.hidden = visibleTotal === 0;
  $("historyMeta").textContent = searching
    ? visibleTotal
      ? `找到 ${visibleTotal} 条`
      : "无匹配"
    : allTotal
      ? `${allTotal} 条记录`
      : "";
  const badge = $("historyBadge");
  if (allTotal > 0) {
    badge.hidden = false;
    badge.textContent = String(allTotal);
  } else {
    badge.hidden = true;
  }
  $("historySearchClear").hidden = !searching;

  const seenDates = new Set();
  list.forEach((group, index) => {
    seenDates.add(group.date);
    let wrap = root.querySelector(`[data-history-date="${group.date}"]`);
    if (!wrap) {
      wrap = document.createElement("section");
      wrap.className = "history-group";
      wrap.dataset.historyDate = group.date;
      wrap.innerHTML = `
        <button type="button" class="history-toggle" aria-expanded="true">
          <span class="history-date"></span>
          <span class="history-count"></span>
          <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 8l3-3.5"/></svg>
        </button>
        <div class="history-items"></div>`;
      const before = root.children[index];
      if (before) root.insertBefore(wrap, before);
      else root.appendChild(wrap);
    } else if (wrap !== root.children[index]) {
      root.insertBefore(wrap, root.children[index] || null);
    }

    if (!wrap.dataset.inited) {
      wrap.dataset.inited = "1";
      if (group.date !== todayKey) collapsedHistoryDates.add(group.date);
    }
    if (searching) collapsedHistoryDates.delete(group.date);

    const collapsed = collapsedHistoryDates.has(group.date);
    wrap.classList.toggle("collapsed", collapsed);
    wrap.querySelector(".history-date").textContent = formatHistoryDate(group.date);
    wrap.querySelector(".history-count").textContent = `${group.tasks.length} 个`;
    const toggle = wrap.querySelector(".history-toggle");
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    const items = wrap.querySelector(".history-items");
    items.hidden = collapsed;
    renderHistorySeriesList(items, group.tasks || [], group.date, todayKey);
  });

  [...root.querySelectorAll("[data-history-date]")].forEach((el) => {
    if (!seenDates.has(el.dataset.historyDate)) el.remove();
  });
}

function renderAll(data) {
  lastSnapshot = {
    active: data.tasks || data.active || [],
    history: data.history || [],
  };
  renderActive(lastSnapshot.active);
  renderHistory(lastSnapshot.history);
  syncTaskChecks();
  updateBatchBar();
}

function backendReady() {
  return Boolean(window.pywebview?.api?.get_bootstrap);
}

async function callApi(name, args = []) {
  if (window.pywebview?.api?.[name]) {
    return window.pywebview.api[name](...args);
  }
  const res = await fetch(`/rpc/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || res.statusText || "请求失败");
  return payload.result;
}

const api = new Proxy(
  {},
  {
    get(_target, name) {
      if (name === "then") return undefined;
      return (...args) => callApi(name, args);
    },
  }
);

async function start() {
  const hint = $("hint");
  hint.textContent = "";
  $("btnStart").disabled = true;
  try {
    const result = await api.start_downloads($("urls").value, prefs());
    if (!result.ok) {
      hint.textContent = result.error || "无法开始";
      return;
    }
    $("urls").value = "";
    renderAll(result);
    refresh();
  } catch (err) {
    hint.textContent = String(err);
  } finally {
    $("btnStart").disabled = false;
  }
}

function openConfirm(act, id, name) {
  pendingAction = { act, ids: [id], batch: false };
  $("confirmName").textContent = name || "该视频";
  $("confirmName").hidden = false;
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

function openBatchConfirm(act) {
  const picked = selectedTasks();
  let eligible = picked;
  if (act === "remove") eligible = picked.filter(canRemoveTask);
  else if (act === "cancel") eligible = picked.filter(canCancelTask);
  else if (act === "retry") eligible = picked.filter(canRetryTask);
  if (!eligible.length) return;

  pendingAction = { act, ids: eligible.map((task) => task.id), batch: true };
  $("confirmName").hidden = false;
  $("confirmName").textContent = String(eligible.length);
  if (act === "remove") {
    $("confirmTitle").textContent = `删除 ${eligible.length} 个任务？`;
    $("confirmPrefix").textContent = "将从列表中移除已选的 ";
    $("confirmSuffix").textContent = " 个任务。";
  } else {
    $("confirmTitle").textContent = `取消 ${eligible.length} 个下载？`;
    $("confirmPrefix").textContent = "将删除已选的 ";
    $("confirmSuffix").textContent = " 个任务已下载的分片和未完成文件，此操作无法恢复。";
  }
  $("confirmYes").textContent = act === "remove" ? "删除" : "删除并取消";
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
    const ids = action.ids || [];
    if (action.batch) {
      if (action.act === "remove") await api.batch_remove(ids);
      else if (action.act === "cancel") await api.batch_cancel(ids);
      ids.forEach((id) => selected.delete(id));
    } else if (action.act === "remove") {
      await api.remove_task(ids[0]);
      selected.delete(ids[0]);
    } else {
      await api.cancel_task(ids[0]);
      selected.delete(ids[0]);
    }
  } catch (_) {
    /* ignore */
  }
  refresh();
}

async function runBatchAction(act) {
  const picked = selectedTasks();
  let eligible = picked;
  if (act === "pause") eligible = picked.filter(canPauseTask);
  else if (act === "resume") eligible = picked.filter(canResumeTask);
  else if (act === "retry") eligible = picked.filter(canRetryTask);
  else if (act === "remove") eligible = picked.filter(canRemoveTask);
  else if (act === "cancel") eligible = picked.filter(canCancelTask);
  const ids = eligible.map((task) => task.id);
  if (!ids.length) return;

  if (act === "remove" || act === "cancel") {
    openBatchConfirm(act);
    return;
  }

  try {
    if (act === "pause") await api.batch_pause(ids);
    else if (act === "resume") await api.batch_resume(ids);
    else if (act === "retry") {
      await api.batch_retry(ids);
      switchTab("active");
    }
  } catch (_) {
    /* ignore */
  }
  refresh();
}

async function refresh() {
  if (ticking) return;
  ticking = true;
  try {
    const data = await api.snapshot();
    renderAll(data);
  } catch (_) {
    /* window may be closing */
  } finally {
    ticking = false;
  }
}

function handleTaskClick(event) {
  if (event.target.closest(".pick") || event.target.closest(".series-check")) return;

  const seriesToggle = event.target.closest(".series-toggle");
  if (seriesToggle) {
    const wrap = seriesToggle.closest("[data-series-group]");
    const dateWrap = seriesToggle.closest("[data-history-date]");
    if (wrap && dateWrap) toggleHistorySeries(dateWrap.dataset.historyDate, wrap.dataset.seriesGroup);
    return;
  }

  const historyToggle = event.target.closest(".history-toggle");
  if (historyToggle) {
    const group = historyToggle.closest("[data-history-date]");
    if (group) toggleHistoryDate(group.dataset.historyDate);
    return;
  }

  const wrap = event.target.closest(".mosaic-wrap");
  if (wrap && !event.target.closest("button[data-act]")) {
    const article = wrap.closest("[data-task]");
    if (article) toggleMosaic(article.dataset.task, wrap);
    return;
  }
  const button = event.target.closest("button[data-act]");
  if (!button) return;
  const { act, id, path, name } = button.dataset;
  if (act === "pause") api.pause_task(id).then(refresh);
  if (act === "resume") api.resume_task(id).then(refresh);
  if (act === "retry") api.retry_task(id).then(() => { switchTab("active"); refresh(); });
  if (act === "cancel") openConfirm("cancel", id, name);
  if (act === "remove") openConfirm("remove", id, name);
  if (act === "reveal") api.reveal(path);
}

function handleTaskChange(event) {
  const seriesPick = event.target.closest(".series-pick");
  if (seriesPick) {
    const wrap = seriesPick.closest("[data-series-group]");
    if (wrap) toggleSeriesGroup(wrap, seriesPick.checked);
    return;
  }
  const input = event.target.closest(".pick");
  if (!input) return;
  toggleTaskSelection(input.dataset.id, input.checked);
}

function bind() {
  $("btnClose").onclick = () => api.win_close();
  $("btnMin").onclick = () => api.win_min();
  $("btnZoom").onclick = () => api.win_zoom();
  $("btnStart").onclick = start;
  $("btnHeaders").onclick = () => {
    $("headersPanel").hidden = !$("headersPanel").hidden;
    $("btnHeaders").classList.toggle("active", !$("headersPanel").hidden);
  };
  $("btnFolder").onclick = async () => {
    const path = await api.choose_folder();
    if (path) {
      folderPath = path;
      $("folderLabel").textContent = path;
    }
  };
  $("tasks").onclick = handleTaskClick;
  $("history").onclick = handleTaskClick;
  $("tasks").onchange = handleTaskChange;
  $("history").onchange = handleTaskChange;
  $("tabActive").onclick = () => switchTab("active");
  $("tabHistory").onclick = () => switchTab("history");
  $("btnSelectMode").onclick = () => setSelectMode(!selectMode);
  $("selectAll").onchange = (event) => toggleSelectAll(event.target.checked);
  $("batchPause").onclick = () => runBatchAction("pause");
  $("batchResume").onclick = () => runBatchAction("resume");
  $("batchRetry").onclick = () => runBatchAction("retry");
  $("batchCancel").onclick = () => runBatchAction("cancel");
  $("batchRemove").onclick = () => runBatchAction("remove");
  $("historySearch").oninput = (event) => {
    historySearchQuery = event.target.value;
    renderHistory(lastSnapshot.history);
    syncTaskChecks();
    updateBatchBar();
  };
  $("historySearchClear").onclick = () => {
    historySearchQuery = "";
    $("historySearch").value = "";
    renderHistory(lastSnapshot.history);
    syncTaskChecks();
    updateBatchBar();
  };
  switchTab("active");
  $("confirmNo").onclick = closeConfirm;
  $("confirmYes").onclick = confirmPending;
  $("confirm").addEventListener("click", (event) => {
    if (event.target.id === "confirm") closeConfirm();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (selectMode) {
        setSelectMode(false);
        return;
      }
      closeConfirm();
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      start();
    }
  });
  ["taskWorkers", "segWorkers", "ua", "referer"].forEach((id) => {
    $(id).addEventListener("change", () => api.save_prefs(prefs()));
  });
}

async function boot() {
  if (booted) return;
  booted = true;
  bind();
  const data = await api.get_bootstrap();
  if (data.host === "edge") document.body.classList.add("edge-host");
  applyBootstrap(data);
  setInterval(refresh, 250);
}

window.addEventListener("pywebviewready", () => {
  boot().catch(() => {});
});
if (backendReady()) {
  boot().catch(() => {});
} else {
  fetch("/rpc/get_bootstrap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "[]",
  })
    .then((res) => {
      if (res.ok) return boot();
      return null;
    })
    .catch(() => {});
}
