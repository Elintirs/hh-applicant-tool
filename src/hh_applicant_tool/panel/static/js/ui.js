// Базовые компоненты и утилиты интерфейса. Всё строится через h(),
// пользовательские строки попадают в DOM только как текст.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else if (k === "html") el.innerHTML = v; // только для своих SVG-иконок
      else el.setAttribute(k, v === true ? "" : v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...children) { clear(el); append(el, children); return el; }

// ---- Иконки (24x24, обводка) ----
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  play: '<path d="M7 4.5v15l13-7.5z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  file: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  check: '<path d="m5 12 5 5 9-10"/>',
  alert: '<path d="M12 3 2 20h20zM12 10v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  send: '<path d="M21 3 10 14M21 3l-7 18-4-7-7-4z"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  logout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l-5-5 5-5M5 12h11"/>',
};

export function icon(name, size = 18) {
  const span = document.createElement("span");
  span.className = "icon";
  span.setAttribute("aria-hidden", "true");
  span.style.display = "inline-flex";
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  return span;
}

// ---- Кнопки ----
export function button(label, { variant = "", size = "", iconName, onClick, title, type = "button", disabled, attrs = {} } = {}) {
  const cls = ["btn", variant && `btn-${variant}`, size && `btn-${size}`, !label && "btn-icon"].filter(Boolean).join(" ");
  return h("button", { class: cls, type, onclick: onClick, title, "aria-label": !label ? title : undefined, disabled, ...attrs },
    iconName ? icon(iconName, size === "sm" ? 16 : 18) : null, label || null);
}

// Кнопка с состоянием «идёт запрос»: блокируется и показывает спиннер,
// чтобы не отправить дважды
export async function busy(btn, fn) {
  if (btn.getAttribute("aria-busy") === "true") return;
  const prev = Array.from(btn.childNodes);
  btn.setAttribute("aria-busy", "true");
  btn.disabled = true;
  const sp = h("span", { class: "spinner", "aria-hidden": "true" });
  btn.replaceChildren(sp, ...prev.filter((n) => !(n.classList && n.classList.contains("icon"))));
  try {
    return await fn();
  } finally {
    btn.replaceChildren(...prev);
    btn.removeAttribute("aria-busy");
    btn.disabled = false;
  }
}

// ---- Поля ----
let fieldSeq = 0;
export function field({ label, flag, help, control, error, inherited, span2 }) {
  const id = control.id || `f${++fieldSeq}`;
  control.id = id;
  const helpId = help ? `${id}-help` : undefined;
  if (helpId) control.setAttribute("aria-describedby", helpId);
  return h("div", { class: `field${span2 ? " span-2" : ""}` },
    label ? h("label", { class: "field-label", for: id }, label, flag ? h("span", { class: "flag" }, flag) : null) : null,
    control,
    inherited ? h("div", { class: "field-inherited" }, inherited) : null,
    help ? h("div", { class: "field-help", id: helpId }, help) : null,
    error ? h("div", { class: "field-error" }, error) : null,
  );
}

export function input(attrs = {}) { return h("input", { class: "input", type: "text", autocomplete: "off", spellcheck: "false", ...attrs }); }
export function textarea(attrs = {}) { return h("textarea", { class: "textarea", spellcheck: "false", ...attrs }); }
export function select(options, attrs = {}) {
  const el = h("select", { class: "select", ...attrs });
  for (const o of options) {
    const [value, label] = Array.isArray(o) ? o : [o, o];
    el.appendChild(h("option", { value }, label));
  }
  if (attrs.value !== undefined) el.value = attrs.value;
  return el;
}

export function switchControl(label, { checked, onChange, id } = {}) {
  const inp = h("input", { type: "checkbox", role: "switch", checked, id, onchange: (e) => onChange && onChange(e.target.checked) });
  const el = h("label", { class: "switch" }, inp, h("span", { class: "switch-track", "aria-hidden": "true" }), label ? h("span", null, label) : null);
  el.input = inp;
  return el;
}

export function segmented(options, value, onChange, label) {
  const group = h("div", { class: "segmented", role: "group", "aria-label": label });
  const render = (v) => {
    for (const b of group.children) b.setAttribute("aria-pressed", String(b.dataset.value === v));
  };
  for (const [v, l] of options) {
    group.appendChild(h("button", { type: "button", dataset: { value: v }, onclick: () => { render(v); onChange(v); } }, l));
  }
  render(value);
  return group;
}

// ---- Обратная связь ----
export function toast(message, kind = "success", timeout = 4000) {
  const root = document.getElementById("toasts");
  const el = h("div", { class: `toast toast-${kind}` },
    icon(kind === "error" ? "alert" : kind === "info" ? "info" : "check"),
    h("div", { class: "grow" }, message),
    button("", { variant: "ghost", size: "sm", iconName: "x", title: "Закрыть", onClick: () => el.remove() }));
  root.appendChild(el);
  if (timeout) setTimeout(() => el.remove(), kind === "error" ? timeout * 2 : timeout);
}

export function callout(kind, ...children) {
  const iconName = { warning: "alert", danger: "alert", info: "info", success: "check" }[kind] || "info";
  return h("div", { class: `callout callout-${kind}` }, icon(iconName), h("div", { class: "grow stack-sm" }, ...children));
}

// Подтверждение только для необратимого. Возвращает Promise<boolean>
export function confirmDialog({ title, text, confirm = "Подтвердить", danger = false }) {
  return new Promise((resolve) => {
    const dlg = h("dialog", { class: "modal", "aria-labelledby": "dlg-title" });
    const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    const ok = button(confirm, { variant: danger ? "danger-solid" : "primary", onClick: () => close(true) });
    dlg.append(
      h("div", { class: "modal-body" }, h("h3", { id: "dlg-title" }, title), text ? h("p", { class: "muted" }, text) : null),
      h("div", { class: "modal-actions" }, button("Отмена", { onClick: () => close(false) }), ok));
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(false); });
    document.body.appendChild(dlg);
    dlg.showModal();
    ok.focus();
  });
}

export function empty({ iconName = "info", title, text, action }) {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon(iconName, 22)),
    title ? h("h3", null, title) : null,
    text ? h("p", null, text) : null,
    action || null);
}

export function skeleton(lines = 3) {
  return h("div", { class: "stack-sm", "aria-busy": "true", "aria-label": "Загрузка" },
    Array.from({ length: lines }, (_, i) => {
      const s = h("div", { class: "skeleton" });
      s.style.width = `${90 - i * 15}%`;
      return s;
    }));
}

export function errorState(err, retry) {
  return empty({
    iconName: "alert",
    title: "Не удалось загрузить",
    text: err && err.message ? err.message : String(err),
    action: retry ? button("Повторить", { iconName: "refresh", onClick: retry }) : null,
  });
}

// ---- Статусы задач ----
export const JOB_STATUS = {
  queued: ["В очереди", "warning"],
  running: ["Выполняется", "accent badge-running"],
  success: ["Готово", "success"],
  failed: ["Ошибка", "danger"],
  stopped: ["Остановлена", ""],
  canceled: ["Отменена", ""],
};

export function statusBadge(status) {
  const [label, kind] = JOB_STATUS[status] || [status, ""];
  return h("span", { class: `badge ${kind ? "badge-" + kind : ""}` }, label);
}

// ---- Форматирование ----
const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
export function relTime(ts) {
  if (!ts) return "";
  const diff = ts * 1000 - Date.now();
  const abs = Math.abs(diff);
  const units = [["day", 864e5], ["hour", 36e5], ["minute", 6e4]];
  for (const [u, ms] of units) if (abs >= ms) return rtf.format(Math.round(diff / ms), u);
  return diff < 0 ? "только что" : "через минуту";
}

export function dateTime(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function duration(a, b) {
  if (!a) return "";
  const s = Math.max(0, Math.round((b || Date.now() / 1000) - a));
  if (s < 60) return `${s} с`;
  if (s < 3600) return `${Math.floor(s / 60)} мин ${s % 60} с`;
  return `${Math.floor(s / 3600)} ч ${Math.floor((s % 3600) / 60)} мин`;
}

export function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// ---- ANSI → безопасный DOM ----
// Поддерживаем то, что реально печатает утилита: цвета 30-37, 90-97, жирный, тусклый
export function appendAnsi(target, text, state = { classes: [] }) {
  const re = /\x1b\[([0-9;]*)m/g;
  let last = 0, m;
  const push = (chunk) => {
    if (!chunk) return;
    if (!state.classes.length) target.appendChild(document.createTextNode(chunk));
    else target.appendChild(h("span", { class: state.classes.join(" ") }, chunk));
  };
  while ((m = re.exec(text))) {
    push(text.slice(last, m.index));
    last = re.lastIndex;
    const codes = m[1] === "" ? [0] : m[1].split(";").map(Number);
    for (const c of codes) {
      if (c === 0) state.classes = [];
      else if (c === 1) state.classes.push("ansi-bold");
      else if (c === 2) state.classes.push("ansi-dim");
      else if ((c >= 30 && c <= 37) || (c >= 90 && c <= 97)) {
        state.classes = state.classes.filter((x) => !/^ansi-\d/.test(x));
        state.classes.push(`ansi-${c >= 90 ? c - 60 : c}`);
      } else if (c === 39) state.classes = state.classes.filter((x) => !/^ansi-\d/.test(x));
    }
  }
  // Прочие управляющие последовательности (курсор, очистка строки) просто выкидываем
  push(text.slice(last).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "").replace(/\r(?!\n)/g, ""));
  return state;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано");
  } catch {
    toast("Не удалось скопировать", "error");
  }
}

// Панель несохранённых изменений: появляется снизу, когда есть правки.
// Кнопка сохранения всегда рядом и всегда читаемая, а не серый
// неактивный прямоугольник в шапке
export function saveBar({ onSave, onReset, label = "Есть несохранённые изменения" }) {
  const saveBtn = button("Сохранить", { variant: "primary" });
  const resetBtn = button("Отменить", { variant: "ghost" });
  const text = h("span", { class: "grow" }, label);
  const bar = h("div", { class: "savebar", role: "region", "aria-label": "Несохранённые изменения", hidden: true },
    h("div", { class: "savebar-inner" }, h("span", { class: "savebar-dot", "aria-hidden": "true" }), text, resetBtn, saveBtn));
  let dirty = false;
  saveBtn.addEventListener("click", () => busy(saveBtn, async () => {
    if (await onSave()) api.set(false);
  }));
  resetBtn.addEventListener("click", () => { onReset(); api.set(false); });
  const keydown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s" && dirty) { e.preventDefault(); saveBtn.click(); }
  };
  document.addEventListener("keydown", keydown);
  const beforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  const api = {
    el: bar,
    get dirty() { return dirty; },
    set(v, message) {
      dirty = v;
      bar.hidden = !v;
      if (message) text.textContent = message;
      saveBtn.disabled = false;
    },
    blockSave(reason) { text.textContent = reason; saveBtn.disabled = true; },
    destroy() { document.removeEventListener("keydown", keydown); window.removeEventListener("beforeunload", beforeUnload); bar.remove(); },
  };
  return api;
}

export function pageHeader(title, lead, ...actions) {
  return h("header", { class: "page-header" },
    h("div", null, h("h1", null, title), lead ? h("p", { class: "lead" }, lead) : null),
    actions.length ? h("div", { class: "page-actions" }, ...actions) : null);
}
