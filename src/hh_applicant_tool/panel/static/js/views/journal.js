import { get } from "../api.js";
import { h, mount, skeleton, errorState, pageHeader, empty } from "../ui.js";

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
};

// Один вызов модели: что ушло на вход и что вернулось
function aiCall(title, call) {
  if (!call) return null;
  const head = `${title} · ${call.model || "?"} · ${call.sec ?? "?"} с`;
  return h("details", { class: "ai-call" },
    h("summary", null, h("span", null, head), h("span", { class: "subtle ai-brief" }, (call.answer || "").replace(/\s+/g, " ").slice(0, 90))),
    call.system ? h("div", { class: "ai-part" }, h("div", { class: "field-label" }, "Системный промпт"), h("pre", { class: "mono ai-text" }, call.system)) : null,
    call.prompt ? h("div", { class: "ai-part" }, h("div", { class: "field-label" }, "Вход"), h("pre", { class: "mono ai-text" }, call.prompt)) : null,
    h("div", { class: "ai-part" }, h("div", { class: "field-label" }, "Ответ"), h("pre", { class: "mono ai-text" }, call.answer || "")));
}

function entry(r) {
  const [label, kind] = RESULTS[r.result] || [r.result, "info"];
  const f = r.ai_filter;
  const tests = (r.test || []).map((t, i) => aiCall(`Тест ${i + 1}`, t.model ? t : { ...t, model: "без AI", sec: 0 }));
  return h("article", { class: "card" },
    h("div", { class: "card-body stack-sm" },
      h("div", { class: "row-between" },
        h("div", { class: "grow" },
          h("a", { href: r.url, target: "_blank", rel: "noopener" }, h("strong", null, r.vacancy || r.vacancy_id)),
          h("div", { class: "subtle" }, [r.employer, r.vacancy_experience, r.ts && r.ts.replace("T", " ")].filter(Boolean).join(" · "))),
        h("span", { class: `badge badge-${kind}` }, label)),
      f ? aiCall(`Фильтр: ${f.suitable ? "подходит" : "не подходит"}`, f) : null,
      r.letter_ai ? aiCall("Письмо", r.letter_ai) : r.letter ? h("pre", { class: "mono ai-text" }, r.letter) : null,
      ...tests,
      r.kind === "chat" && r.message ? h("div", { class: "subtle" }, "Работодатель: ", r.message.replace(/\s+/g, " ").slice(0, 300)) : null,
      r.chat_ai ? aiCall("Ответ в чат", r.chat_ai) : r.reply ? h("pre", { class: "mono ai-text" }, r.reply) : null,
      r.error ? h("div", { class: "field-error" }, r.error) : null));
}

export default async function journal(root) {
  mount(root, pageHeader("Журнал AI", "Что модель получила и что ответила по вакансиям и чатам. Пишут apply-vacancies и reply-chats, обновляется сам."), skeleton(4));
  let timer;
  const open = new Set();
  async function load() {
    let data;
    try { data = await get("journal?limit=100"); } catch (err) { mount(root, errorState(err, load)); return; }
    // Перерисовка не должна схлопывать раскрытые блоки
    root.querySelectorAll("details[open] summary").forEach((s) => open.add(s.textContent));
    mount(root,
      pageHeader("Журнал AI", `Последние ${data.items.length} записей. Файл: ${data.path}`),
      data.items.length
        ? h("div", { class: "stack" }, data.items.map(entry))
        : h("div", { class: "card" }, empty({ iconName: "sparkle", title: "Журнал пока пуст", text: "Записи появятся после запуска apply-vacancies или reply-chats с AI." })));
    root.querySelectorAll("details summary").forEach((s) => { if (open.has(s.textContent)) s.parentElement.open = true; });
  }
  await load();
  timer = setInterval(load, 10000);
  return () => clearInterval(timer);
}
