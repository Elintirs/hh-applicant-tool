import { get, put, post } from "../api.js";
import {
  h, mount, button, field, input, toast, busy, skeleton, errorState, pageHeader, callout, icon, saveBar,
} from "../ui.js";
import { localModelsCard } from "./local-models.js";

const FIELDS = [
  { key: "base_url", label: "Адрес API", help: "Часть адреса до /chat/completions. Для Ollama в Docker: http://<IP хоста>:11434/v1.", example: "https://api.openai.com/v1", wide: true },
  { key: "model", label: "Модель", example: "qwen3-vl:8b" },
  { key: "api_key", label: "Ключ API", type: "password", help: "Локальным моделям обычно не нужен." },
];
const ADVANCED = [
  { key: "temperature", label: "Температура", type: "number", step: "0.1" },
  { key: "max_completion_tokens", label: "Лимит токенов ответа", type: "number", help: "У думающих моделей сюда входят и рассуждения: для gpt-oss нужно 2000+." },
  { key: "reasoning_effort", label: "Рассуждения", example: "low", help: "none, low, medium или high. none отключает рассуждения у qwen3, у gpt-oss минимум low." },
  { key: "timeout", label: "Таймаут, с", type: "number" },
  { key: "connect_timeout", label: "Таймаут соединения, с", type: "number" },
  { key: "delay", label: "Пауза между запросами, с", type: "number", step: "0.1" },
  { key: "proxy_url", label: "Прокси", example: "socks5h://host:1080", wide: true },
];

function chev() { const c = icon("chevron", 16); c.classList.add("chev"); return c; }

export default async function models(root) {
  mount(root, pageHeader("Модели", "Основная модель работает везде. Для отдельных задач можно подключить другую."), skeleton(6));
  let data;
  try { data = await get("models"); } catch (err) { mount(root, errorState(err, () => models(root))); return; }

  const ac = new AbortController();
  let draft;
  const open = new Set(data.tasks.filter((t) => t.key !== "default" && Object.keys(t.values).length).map((t) => t.key));
  const resetDraft = () => { draft = Object.fromEntries(data.tasks.map((t) => [t.key, { ...t.values }])); };
  resetDraft();
  const def = () => draft.default || {};

  const bar = saveBar({
    onSave: async () => {
      try {
        data = await put("models", { sections: draft });
        resetDraft();
        render();
        toast("Модели сохранены");
        return true;
      } catch (err) { toast(err.message, "error"); return false; }
    },
    onReset: () => { resetDraft(); render(); },
  });
  const changed = () => bar.set(true);
  // Карточка Ollama живёт дольше перерисовок: у неё свой опрос загрузок
  const local = localModelsCard({
    onApplied: async () => {
      if (bar.dirty) { toast("Модель назначена. Сохраните или отмените свои правки, чтобы увидеть её в полях", "info"); return; }
      data = await get("models"); resetDraft(); render();
    },
  });

  // Поле настройки. Для задачи (не основной) пустое поле наследует значение
  // основной модели, и это значение видно прямо в поле как подсказка
  function fieldFor(task, f) {
    const values = draft[task.key];
    const isDefault = task.key === "default";
    const inherit = () => (!isDefault ? def()[f.key] : undefined);
    const inp = input({
      type: f.type === "password" ? "password" : f.type || "text",
      step: f.step, value: values[f.key] ?? "", list: f.key === "model" ? `models-${task.key}` : undefined,
      autocomplete: f.type === "password" ? "new-password" : "off",
    });
    inp.id = `${task.key}-${f.key}`;
    const setPlaceholder = () => {
      const v = inherit();
      if (v !== undefined && v !== "") inp.placeholder = f.key === "api_key" ? "Как у основной (задан)" : `Как у основной: ${v}`;
      else if (!isDefault) inp.placeholder = "Как у основной (не задано)";
      else inp.placeholder = f.example ? `Например, ${f.example}` : "";
    };
    setPlaceholder();
    document.addEventListener("models:default", setPlaceholder, { signal: ac.signal });
    inp.addEventListener("input", () => {
      if (inp.value === "") delete values[f.key]; else values[f.key] = inp.value;
      changed();
      if (isDefault) document.dispatchEvent(new CustomEvent("models:default"));
    });
    if (f.type !== "password") return field({ label: f.label, flag: f.key, control: inp, help: f.help, span2: f.wide });
    const toggle = button("", { variant: "ghost", iconName: "eye", title: "Показать ключ", attrs: { "aria-pressed": "false", "aria-controls": inp.id }, onClick: () => {
      inp.type = inp.type === "password" ? "text" : "password";
      toggle.setAttribute("aria-pressed", String(inp.type === "text"));
    } });
    return h("div", { class: "field" },
      h("label", { class: "field-label", for: inp.id }, f.label, h("span", { class: "flag" }, f.key)),
      h("div", { class: "input-group" }, inp, toggle),
      f.help ? h("div", { class: "field-help" }, f.help) : null);
  }

  function testControls(task) {
    const values = draft[task.key];
    const result = h("div", { "aria-live": "polite" });
    const datalist = h("datalist", { id: `models-${task.key}` });
    const testBtn = button(task.vision ? "Проверить на капче" : "Проверить", { size: "sm", iconName: "sparkle" });
    testBtn.addEventListener("click", () => busy(testBtn, async () => {
      mount(result, h("div", { class: "result subtle" }, "Жду ответ модели…"));
      try {
        const r = await post("models/test", { key: task.key, values });
        const ok = !task.vision || r.text.replace(/\s/g, "").toUpperCase().includes("7KQ4M");
        mount(result, callout(ok ? "success" : "warning",
          h("p", null, h("strong", null, task.vision ? (ok ? "Капча распознана верно" : "Капча распознана с ошибкой") : "Модель отвечает"), ` · ${r.seconds} с${r.model ? ` · ${r.model}` : ""}`),
          h("div", { class: "result" }, r.text || "(пустой ответ)"),
          task.vision && !ok ? h("p", null, "Правильный ответ 7KQ4M. Возьмите модель с поддержкой изображений (vision).") : null));
      } catch (err) {
        mount(result, callout("danger", h("p", null, err.message)));
      }
    }));
    const listBtn = button("Список моделей", { size: "sm", variant: "ghost", iconName: "list" });
    listBtn.addEventListener("click", () => busy(listBtn, async () => {
      try {
        const { models: names } = await post("models/remote", { key: task.key, values });
        mount(datalist, ...names.map((n) => h("option", { value: n })));
        toast(names.length ? `Моделей на сервере: ${names.length}. Они появятся подсказками в поле «Модель».` : "Сервер не вернул список моделей", names.length ? "success" : "info");
      } catch (err) { toast(err.message, "error"); }
    }));
    return { buttons: h("div", { class: "row" }, listBtn, testBtn), result, datalist };
  }

  function form(task) {
    const t = testControls(task);
    return h("div", { class: "stack" },
      h("div", { class: "form-grid" }, FIELDS.map((f) => fieldFor(task, f))),
      h("details", { class: "disclosure" }, h("summary", null, chev(), "Тонкая настройка"), h("div", { class: "form-grid" }, ADVANCED.map((f) => fieldFor(task, f)))),
      task.extra_keys && task.extra_keys.length ? h("p", { class: "subtle" }, `Ещё в секции: ${task.extra_keys.join(", ")}. Меняются на вкладке «Конфиг».`) : null,
      t.buttons, t.datalist, t.result);
  }

  function taskRow(task) {
    const values = draft[task.key];
    const own = Object.keys(values).length > 0;
    const isOpen = open.has(task.key);
    const effModel = values.model || def().model;
    const summary = own
      ? h("span", { class: "badge badge-info plain" }, `Своя${values.model ? `: ${values.model}` : " настройка"}`)
      : h("span", { class: "badge plain" }, `Как у основной${effModel ? `: ${effModel}` : ""}`);
    const toggle = button(isOpen ? "Свернуть" : own ? "Изменить" : "Своя модель", { size: "sm", variant: isOpen ? "ghost" : "", attrs: { "aria-expanded": String(isOpen), "aria-controls": `task-${task.key}` } });
    toggle.addEventListener("click", () => { isOpen ? open.delete(task.key) : open.add(task.key); render(); });
    const clearBtn = own ? button("Вернуть основную", { size: "sm", variant: "ghost", onClick: () => {
      draft[task.key] = {}; open.delete(task.key); changed(); render();
    } }) : null;
    return h("div", { class: `task-row${isOpen ? " is-open" : ""}` },
      h("div", { class: "task-row-head" },
        h("div", { class: "grow" },
          h("div", { class: "row" }, h("h4", null, task.title), summary),
          h("p", { class: "card-hint" }, task.hint)),
        clearBtn, toggle),
      isOpen ? h("div", { class: "task-row-body", id: `task-${task.key}` }, form(task)) : null);
  }

  function render() {
    const mainTask = data.tasks.find((t) => t.key === "default");
    const others = data.tasks.filter((t) => t.key !== "default");
    mount(root,
      pageHeader("Модели", "Основная модель работает везде. Для отдельных задач можно подключить другую, например дешёвую для фильтра и умную для писем."),
      h("div", { class: "stack" },
        !data.configured ? callout("info", h("p", null, "AI ещё не настроен. Скачайте локальную модель ниже или укажите адрес API основной модели, и утилита начнёт писать письма, фильтровать вакансии и отвечать в чатах.")) : null,
        local,
        h("section", { class: "card", "aria-labelledby": "main-model" },
          h("div", { class: "card-header" }, h("div", null, h("h3", { id: "main-model" }, "Основная модель"), h("p", { class: "card-hint" }, mainTask.hint))),
          h("div", { class: "card-body" }, form(mainTask))),
        h("section", { class: "card", "aria-labelledby": "task-models" },
          h("div", { class: "card-header" }, h("div", null, h("h3", { id: "task-models" }, "Модели для задач"), h("p", { class: "card-hint" }, "Пустое поле берёт значение основной модели."))),
          h("div", null, others.map(taskRow))),
        data.has_comments ? h("p", { class: "subtle" }, "При сохранении комментарии в config.toml пропадут, копия останется в config.toml.bak.") : null),
      bar.el);
    bar.set(bar.dirty);
  }

  render();
  return () => { ac.abort(); bar.destroy(); local.destroy(); };
}
