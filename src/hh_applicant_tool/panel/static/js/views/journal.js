import { get } from "../api.js";
import { h, mount, skeleton, errorState, pageHeader, empty, select, input, segmented } from "../ui.js";

const RESULTS = {
  sent: ["Отклик отправлен", "success"],
  dry_run: ["Dry-run", "info"],
  ai_rejected: ["Отсеял AI", "warning"],
  excluded: ["Под фильтром", "info"],
  error: ["Ошибка", "danger"],
  redirect: ["Форма на сайте", "info"],
  limit: ["Лимит hh", "danger"],
  chat_sent: ["Ответил AI", "success"],
  chat_manual: ["Ответить самому", "warning"],
  chat_dry_run: ["Чат: dry-run", "info"],
  chat_left: ["Вышел из чата", "info"],
  form_sent: ["Анкета отправлена", "success"],
  form_error: ["Анкета: ошибка", "danger"],
};

// Полные записи с промптами: список приходит без них, догружаем по раскрытию
const fullCache = new Map();
async function fullRecord(line) {
  if (!fullCache.has(line)) fullCache.set(line, get(`journal/${line}`).catch((err) => { fullCache.delete(line); throw err; }));
  return fullCache.get(line);
}

const part = (label, text) => h("div", { class: "ai-part" }, h("div", { class: "field-label" }, label), h("pre", { class: "mono ai-text" }, text));

// Один вызов модели: что ушло на вход и что вернулось.
// pick достаёт этот же вызов из полной записи журнала
function aiCall(title, call, line, pick) {
  if (!call) return null;
  const sec = call.sec != null ? `${String(call.sec).replace(".", ",")} с` : "";
  const promptBox = h("div", { class: "stack-sm" },
    call.system ? part("Системный промпт", call.system) : null,
    call.prompt ? part("Вход", call.prompt) : null);
  const el = h("details", { class: "ai-call" },
    h("summary", null,
      h("span", null, title),
      h("span", { class: "subtle" }, [call.model || "?", sec].filter(Boolean).join(", ")),
      h("span", { class: "subtle ai-brief" }, (call.answer || "").replace(/\s+/g, " ").slice(0, 90))),
    promptBox,
    part("Ответ", call.answer || ""));
  if (call.has_prompt && line != null && pick) {
    el.addEventListener("toggle", async () => {
      if (!el.open || promptBox.dataset.loaded) return;
      promptBox.dataset.loaded = "1";
      mount(promptBox, h("p", { class: "subtle" }, "Загружаю промпт…"));
      try {
        const full = pick(await fullRecord(line)) || {};
        mount(promptBox, full.system ? part("Системный промпт", full.system) : null, full.prompt ? part("Вход", full.prompt) : null);
      } catch (err) { delete promptBox.dataset.loaded; mount(promptBox, h("p", { class: "field-error" }, err.message)); }
    });
  }
  return el;
}

// Время записи: сегодня только часы, иначе и дата
function stamp(ts) {
  if (!ts) return "";
  const [d, t] = ts.split("T");
  const today = new Date().toISOString().slice(0, 10);
  const hm = (t || "").slice(0, 5);
  return d === today ? hm : `${d.slice(8, 10)}.${d.slice(5, 7)} ${hm}`;
}

// Что решил AI, одной строкой: причина фильтра, ответ в чат или ошибка
function reason(r) {
  if (r.error) return r.error;
  if (r.kind === "chat") return r.reply ? `Ответ: ${r.reply}` : r.message ? `Работодатель: ${r.message}` : "";
  return r.ai_filter?.answer ? `Фильтр: ${r.ai_filter.answer}` : "";
}

// Строка ленты. compact - для обзора: без раскрывающихся вызовов модели
export function ledgerRow(r, { compact = false } = {}) {
  const [label, kind] = RESULTS[r.result] || [r.result, "info"];
  const f = r.ai_filter;
  const L = r._line;
  const why = reason(r).replace(/\s+/g, " ");
  const calls = compact ? [] : [
    f ? aiCall(`Фильтр: ${f.suitable ? "подходит" : "не подходит"}`, f, L, (x) => x.ai_filter) : null,
    r.letter_ai ? aiCall("Письмо", r.letter_ai, L, (x) => x.letter_ai) : r.letter ? h("pre", { class: "mono ai-text" }, r.letter) : null,
    ...(r.test || []).map((t, i) => aiCall(`Тест ${i + 1}`, t.model ? t : { ...t, model: "без AI", sec: 0 }, L, (x) => (x.test || [])[i])),
    r.chat_ai ? aiCall("Ответ в чат", r.chat_ai, L, (x) => x.chat_ai) : null,
    r.form ? h("div", { class: "stack-sm" }, r.form.map((q) => h("div", { class: "ai-part" }, h("div", { class: "field-label" }, q.q), h("div", null, Array.isArray(q.answer) ? q.answer.join(", ") : (q.answer || "нет ответа"))))) : null,
  ].filter(Boolean);
  return h("li", { class: "ledger-row" },
    h("time", { class: "ledger-time", datetime: r.ts || "" }, stamp(r.ts)),
    h("div", { class: "ledger-main" },
      h("div", { class: "ledger-title" }, h("a", { href: r.url, target: "_blank", rel: "noopener" }, r.vacancy || r.vacancy_id || "Чат")),
      h("div", { class: "ledger-meta" }, [r.employer, r.vacancy_experience].filter(Boolean).join(", ")),
      // В полной ленте причину фильтра видно в его строке ниже, не повторяем
      why && (compact || r.error || !f) ? h("div", { class: r.error ? "field-error" : "ledger-reason" }, compact ? why.slice(0, 140) : why.slice(0, 400)) : null),
    h("span", { class: `badge badge-${kind}` }, label),
    calls.length ? h("div", { class: "ledger-calls" }, ...calls) : null);
}

export { RESULTS };

export default async function journal(root) {
  const lead = "Что модель получила и что ответила по вакансиям и чатам. Пишут apply-vacancies и reply-chats, обновляется сам.";
  mount(root, pageHeader("Журнал AI", lead), skeleton(4));
  let timer, items = [], path = "", version = "";
  const f = { kind: "all", result: "", q: "" };
  const open = new Set();
  const listEl = h("ol", { class: "ledger" });
  const countEl = h("span", { class: "subtle" });
  const resultSel = select([], { "aria-label": "Результат", onchange: (e) => { f.result = e.target.value; render(); } });
  const searchEl = input({ type: "search", placeholder: "Вакансия или компания", "aria-label": "Поиск", oninput: (e) => { f.q = e.target.value.trim().toLowerCase(); render(); } });
  const kindCtl = segmented([["all", "Все"], ["apply", "Отклики"], ["chat", "Чаты"]], f.kind, (k) => { f.kind = k; f.result = ""; render(); }, "Тип записей");
  const byKind = (r) => f.kind === "all" || (f.kind === "chat") === (r.kind === "chat");

  function render() {
    // Варианты результата с количеством по выбранному типу
    const counts = {};
    for (const r of items) if (byKind(r)) counts[r.result] = (counts[r.result] || 0) + 1;
    mount(resultSel, h("option", { value: "" }, "Все результаты"),
      ...Object.keys(counts).map((k) => h("option", { value: k }, `${(RESULTS[k] || [k])[0]} (${counts[k]})`)));
    if (f.result && !counts[f.result]) f.result = "";
    resultSel.value = f.result;
    const shown = items.filter((r) => byKind(r)
      && (!f.result || r.result === f.result)
      && (!f.q || `${r.vacancy || ""} ${r.employer || ""}`.toLowerCase().includes(f.q)));
    countEl.textContent = `${shown.length} из ${items.length}`;
    // Перерисовка не должна схлопывать раскрытые блоки
    listEl.querySelectorAll("details[open] summary").forEach((s) => open.add(s.textContent));
    mount(listEl, ...(shown.length
      ? shown.map((r) => ledgerRow(r))
      : [h("li", null, items.length
        ? empty({ iconName: "search", title: "Ничего не найдено", text: "Поменяйте фильтры." })
        : empty({ iconName: "sparkle", title: "Журнал пока пуст", text: "Записи появятся после запуска apply-vacancies или reply-chats с AI." }))]));
    listEl.querySelectorAll("details summary").forEach((s) => { if (open.has(s.textContent)) s.parentElement.open = true; });
  }

  async function load() {
    let data;
    try { data = await get(`journal?limit=500&v=${encodeURIComponent(version)}`); } catch (err) { if (!items.length) mount(root, errorState(err, load)); return; }
    if (data.unchanged) return; // журнал не менялся: не перерисовываем
    version = data.v || "";
    items = data.items;
    if (path !== data.path) {
      path = data.path;
      mount(root,
        pageHeader("Журнал AI", `${lead} Файл: ${path}`),
        h("div", { class: "journal-filters" }, kindCtl, resultSel, searchEl, countEl),
        listEl);
    }
    render();
  }
  await load();
  timer = setInterval(load, 10000);
  return () => clearInterval(timer);
}
