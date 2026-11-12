import { get, put, post } from "../api.js";
import {
  h, mount, button, field, input, select, switchControl, segmented, toast, busy, empty,
  skeleton, errorState, statusBadge, relTime, dateTime, pageHeader, confirmDialog, callout,
} from "../ui.js";
import { shellQuote } from "./run.js";

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

// Готовые задачи: то, что обычно запускают по расписанию
const PRESETS = [
  { name: "Поднять резюме", args: ["update-resumes"], mode: "resume" },
  { name: "Отклики на вакансии", args: ["apply-vacancies", "--max-responses=50"], mode: "daily", times: ["10:00"], days: [0, 1, 2, 3, 4] },
  { name: "Автоответчик в чатах", args: ["reply-chats"], mode: "interval", every_minutes: 60 },
  { name: "Почистить отказы", args: ["clear-negotiations"], mode: "daily", times: ["23:00"], days: [6] },
];

function splitArgs(text) {
  // Простой разбор с кавычками, как в шелле
  const out = [];
  let cur = "", q = null, has = false;
  for (const ch of text.trim()) {
    if (q) { if (ch === q) q = null; else cur += ch; continue; }
    if (ch === "'" || ch === '"') { q = ch; has = true; continue; }
    if (/\s/.test(ch)) { if (cur || has) out.push(cur); cur = ""; has = false; continue; }
    cur += ch;
  }
  if (cur || has) out.push(cur);
  return out;
}

function describe(item) {
  if (item.mode === "resume") return "сразу, как hh разрешит поднять резюме";
  if (item.mode === "interval") {
    const m = item.every_minutes;
    return m % 60 === 0 ? `каждые ${m / 60} ч` : `каждые ${m} мин`;
  }
  const days = item.days && item.days.length && item.days.length < 7
    ? item.days.map((d) => DAYS[d]).join(", ")
    : "каждый день";
  return `${days} в ${item.times.join(", ")}`;
}

export default async function schedule(root, ctx) {
  let items = [];
  let editing = null; // id задачи в режиме правки или "new"
  let draft = null;

  mount(root, pageHeader("Расписание", "Задачи запускаются, пока работает контейнер с панелью. Время по часовому поясу сервера."), skeleton(4));

  async function load() {
    try { items = (await get("schedule")).items; } catch (err) { mount(root, errorState(err, () => schedule(root, ctx))); return false; }
    return true;
  }

  async function save(next, message) {
    items = (await put("schedule", { items: next })).items;
    editing = null; draft = null;
    render();
    toast(message);
  }

  // inline - правка в строке таблицы, иначе отдельная карточка новой задачи
  function editor(inline = false) {
    const d = draft;
    const name = input({ value: d.name || "", placeholder: "Например, утренние отклики" });
    const cmd = input({ class: "input mono", value: (d.args || []).map(shellQuote).join(" "), placeholder: "apply-vacancies --max-responses=50" });
    const times = input({ value: (d.times || []).join(", "), placeholder: "09:00, 18:30" });
    const every = input({ type: "number", min: 5, step: 5, value: d.every_minutes || 60 });
    const error = h("div", { class: "field-error", role: "alert" });

    const dayChips = h("div", { class: "chips", role: "group", "aria-label": "Дни недели" });
    const chosen = new Set(d.days && d.days.length ? d.days : [0, 1, 2, 3, 4, 5, 6]);
    DAYS.forEach((label, i) => {
      const b = h("button", { type: "button", class: "chip", "aria-pressed": String(chosen.has(i)) }, label);
      b.addEventListener("click", () => { chosen.has(i) ? chosen.delete(i) : chosen.add(i); b.setAttribute("aria-pressed", String(chosen.has(i))); });
      dayChips.appendChild(b);
    });

    const dailyBox = h("div", { class: "form-grid" },
      field({ label: "Время", control: times, help: "Через запятую, формат ЧЧ:ММ." }),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Дни"), dayChips));
    const intervalBox = h("div", { class: "form-grid" },
      field({ label: "Интервал, минут", control: every, help: "Не меньше 5 минут. Первый запуск сразу после сохранения." }));
    const resumeBox = h("p", { class: "card-hint" },
      "Панель узнаёт у hh, когда резюме можно поднять (раз в 4 часа), и запускает задачу в эту минуту. Подходит для update-resumes.");
    let mode = d.mode || "daily";
    const syncMode = () => { dailyBox.hidden = mode !== "daily"; intervalBox.hidden = mode !== "interval"; resumeBox.hidden = mode !== "resume"; };
    const modeCtl = segmented([["daily", "По времени"], ["interval", "Интервал"], ["resume", "По hh"]], mode, (m) => { mode = m; syncMode(); }, "Режим");
    syncMode();

    const saveBtn = button("Сохранить", { variant: "primary" });
    saveBtn.addEventListener("click", () => busy(saveBtn, async () => {
      error.textContent = "";
      const args = splitArgs(cmd.value);
      if (args[0] === "hh-applicant-tool") args.shift();
      const item = {
        id: d.id, name: name.value.trim(), args, enabled: d.enabled !== false, mode,
        times: times.value.split(/[,\s]+/).filter(Boolean),
        days: chosen.size === 7 ? [] : [...chosen].sort(),
        every_minutes: Number(every.value),
      };
      const next = d.id ? items.map((i) => (i.id === d.id ? { ...i, ...item } : i)) : [...items, item];
      try { await save(next, d.id ? "Задача обновлена" : "Задача добавлена"); } catch (err) { error.textContent = err.message; }
    }));

    const body = [
      h("div", { class: "form-grid" },
        field({ label: "Название", control: name }),
        field({ label: "Команда", control: cmd, help: "Аргументы как в терминале, без hh-applicant-tool." })),
      h("div", { class: "row" }, modeCtl),
      dailyBox, intervalBox, resumeBox, error];
    const actions = [button("Отмена", { variant: "ghost", onClick: () => { editing = null; draft = null; render(); } }), saveBtn];
    if (inline) return h("div", { class: "rows-editor stack", "aria-label": "Правка задачи" }, ...body, h("div", { class: "row" }, ...actions));
    return h("section", { class: "card is-active", "aria-label": d.id ? "Правка задачи" : "Новая задача" },
      h("div", { class: "card-header" }, h("h3", null, d.id ? "Правка задачи" : "Новая задача")),
      h("div", { class: "card-body stack" }, ...body),
      h("div", { class: "card-footer" }, ...actions));
  }

  function card(item) {
    const sw = switchControl("", { checked: item.enabled, onChange: async (on) => {
      try { await save(items.map((i) => (i.id === item.id ? { ...i, enabled: on } : i)), on ? "Задача включена" : "Задача на паузе"); }
      catch (err) { toast(err.message, "error"); sw.input.checked = !on; }
    } });
    sw.input.setAttribute("aria-label", `${item.enabled ? "Выключить" : "Включить"} «${item.name}»`);
    const runBtn = button("Запустить сейчас", { size: "sm", iconName: "play" });
    runBtn.addEventListener("click", () => busy(runBtn, async () => {
      try {
        const job = await post(`schedule/${item.id}/run`);
        ctx.refreshRunning();
        toast(job.status === "queued" ? "Поставлено в очередь" : "Запущено");
        ctx.navigate("jobs", { id: job.id });
      } catch (err) { toast(err.message, "error"); }
    }));
    const removeBtn = button("", { size: "sm", variant: "ghost", iconName: "trash", title: `Удалить «${item.name}»` });
    removeBtn.addEventListener("click", async () => {
      if (!(await confirmDialog({ title: `Удалить «${item.name}»?`, text: "Задача пропадёт из расписания. История запусков останется.", confirm: "Удалить", danger: true }))) return;
      try { await save(items.filter((i) => i.id !== item.id), "Задача удалена"); } catch (err) { toast(err.message, "error"); }
    });

    const isEditing = editing === item.id;
    const next = item.enabled && item.next_run_at
      ? h("div", { class: "subtle", title: dateTime(item.next_run_at) }, `следующий ${relTime(item.next_run_at)}`)
      : !item.enabled ? h("div", { class: "subtle" }, "на паузе") : null;
    return h("li", { class: `rows-item${isEditing ? " is-open" : ""}` },
      h("div", { class: "rows-line" },
        h("div", { class: "rows-switch" }, sw),
        h("div", { class: "rows-name" },
          h("div", { class: "row" }, h("h3", null, item.name), item.busy ? statusBadge("running") : null),
          h("code", { class: "mono subtle rows-cmd" }, ["hh-applicant-tool", ...item.args].map(shellQuote).join(" "))),
        h("div", { class: "rows-cell", "data-label": "Когда" }, h("div", null, describe(item)), next),
        h("div", { class: "rows-cell", "data-label": "Прошлый запуск" },
          item.last_run_at ? h("div", { class: "row" }, item.last_job_id ? h("a", { href: `#/jobs?id=${encodeURIComponent(item.last_job_id)}` }, relTime(item.last_run_at)) : h("span", null, relTime(item.last_run_at)), item.last_status ? statusBadge(item.last_status) : null)
            : h("span", { class: "subtle" }, "ещё не было")),
        h("div", { class: "rows-actions" }, runBtn,
          button(isEditing ? "Свернуть" : "Изменить", { size: "sm", variant: "ghost", attrs: { "aria-expanded": String(isEditing) }, onClick: () => { if (isEditing) { editing = null; draft = null; } else { editing = item.id; draft = { ...item }; } render(); } }),
          removeBtn)),
      isEditing ? editor(true) : null);
  }

  function render() {
    const addBtn = button("Добавить задачу", { variant: "primary", iconName: "plus", onClick: () => { editing = "new"; draft = { mode: "daily", times: ["09:00"], days: [], enabled: true }; render(); } });
    const presets = h("section", { class: "side-block" },
      h("h2", null, "Типовые задачи"),
      h("p", { class: "subtle" }, "Добавятся в форму новой задачи, там их можно поменять."),
      h("div", { class: "side-actions" }, PRESETS.map((p) => button(p.name, { size: "sm", iconName: "plus", onClick: () => { editing = "new"; draft = { ...p, enabled: true }; render(); } }))));

    mount(root,
      pageHeader("Расписание", "Работает, пока запущен контейнер с панелью. Время по часовому поясу сервера. Если прошлый запуск ещё идёт, следующий пропускается.", addBtn),
      h("div", { class: "stack-lg" },
        editing === "new" ? editor() : null,
        items.length
          ? h("div", { class: "card rows-schedule" },
              h("div", { class: "rows-head rows-line", "aria-hidden": "true" }, h("span", null), h("span", null, "Задача"), h("span", null, "Когда"), h("span", null, "Прошлый запуск"), h("span", null)),
              h("ul", { class: "rows" }, items.map(card)))
          : (editing ? null : h("div", { class: "card" }, empty({ iconName: "clock", title: "Расписание пустое", text: "Добавьте задачу или начните с типовой ниже." }))),
        presets));
  }

  if (!(await load())) return;
  // Черновик задачи с другого экрана (например, из подбора вакансий)
  if (ctx.params.get("draft")) {
    try {
      const d = JSON.parse(sessionStorage.getItem("hh-panel-schedule-draft") || "null");
      if (d) { editing = "new"; draft = { ...d, enabled: true }; }
    } catch { /* ничего */ }
    sessionStorage.removeItem("hh-panel-schedule-draft");
  }
  render();
  const timer = setInterval(async () => { if (!editing && (await load())) render(); }, 30000);
  return () => clearInterval(timer);
}
