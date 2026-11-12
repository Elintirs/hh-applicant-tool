// Блок «Локальные модели»: Ollama на этом же компьютере (или в том же
// docker compose). Скачать модель, назначить на задачу, удалить.
import { get, post, put } from "../api.js";
import { h, mount, button, input, select, toast, busy, callout, confirmDialog, skeleton, icon } from "../ui.js";

const TASKS = [
  ["default", "Основная (все задачи)"],
  ["cover_letter", "Сопроводительные письма"],
  ["vacancy_filter", "Фильтр вакансий"],
  ["test_solver", "Тесты работодателей"],
  ["reply_chat", "Ответы в чатах"],
  ["reply_chat_options", "Выбор вариантов в чатах"],
  ["captcha", "Капча (нужна vision-модель)"],
];

const gb = (n) => (n ? `${(n / 1e9).toFixed(1).replace(".", ",")} ГБ` : "");

export function localModelsCard({ onApplied } = {}) {
  const card = h("section", { class: "card", "aria-labelledby": "local-title" });
  const body = h("div", { class: "card-body stack" }, skeleton(3));
  const header = h("div", { class: "card-header" },
    h("div", null, h("div", { class: "row" }, h("h3", { id: "local-title" }, "Локальные модели"), h("span", { class: "badge plain", id: "ollama-badge" }, "Ollama")),
      h("p", { class: "card-hint" }, "Нейронки на этом компьютере: бесплатно и без отправки резюме в чужие сервисы.")));
  card.append(header, body);
  let timer = null;
  let data = null;

  async function load() {
    try { data = await get("ollama"); } catch (err) { mount(body, callout("danger", h("p", null, err.message))); return; }
    render();
    const active = (data.pulls || []).some((p) => !p.done_at);
    clearTimeout(timer);
    timer = setTimeout(load, active ? 1500 : 15000);
  }

  function urlEditor() {
    const inp = input({ value: data.url, "aria-label": "Адрес Ollama" });
    const save = button("Сохранить", { size: "sm" });
    save.addEventListener("click", () => busy(save, async () => {
      try { data = await put("ollama", { url: inp.value }); toast("Адрес Ollama сохранён"); render(); } catch (err) { toast(err.message, "error"); }
    }));
    return h("details", { class: "disclosure" },
      h("summary", null, (() => { const c = icon("chevron", 16); c.classList.add("chev"); return c; })(), `Адрес: ${data.url}`),
      h("div", { class: "stack-sm" }, h("div", { class: "input-group" }, inp, save),
        h("p", { class: "subtle" }, "В docker compose это http://ollama:11434. Если Ollama стоит прямо на Windows, а панель в Docker: http://host.docker.internal:11434.")));
  }

  function pulls() {
    const list = data.pulls || [];
    if (!list.length) return null;
    return h("div", { class: "stack-sm", "aria-live": "polite" }, list.map((p) => {
      const pct = p.total ? Math.round((p.completed / p.total) * 100) : null;
      const fill = h("span"); fill.style.width = `${pct ?? (p.done_at ? 100 : 5)}%`;
      return h("div", { class: "stack-sm" },
        h("div", { class: "row-between" }, h("strong", null, p.name),
          h("span", { class: p.error ? "field-error" : "subtle" }, p.error || (pct != null && !p.done_at ? `${p.status}, ${pct}% из ${gb(p.total)}` : p.status))),
        h("div", { class: "bar" }, fill));
    }));
  }

  function installed() {
    if (!data.models.length) return h("p", { class: "muted" }, "Моделей пока нет. Скачайте одну из предложенных ниже.");
    return h("ul", { class: "list list-flush" }, data.models.map((m) => {
      const target = select(TASKS, { "aria-label": `Задача для ${m.name}` });
      const use = button("Назначить", { size: "sm", variant: "primary" });
      use.addEventListener("click", () => busy(use, async () => {
        try {
          await post("ollama/use", { name: m.name, tasks: [target.value] });
          toast(`${m.name}: ${TASKS.find((t) => t[0] === target.value)[1].toLowerCase()}`);
          onApplied && onApplied();
        } catch (err) { toast(err.message, "error"); }
      }));
      // В память заранее, чтобы черновик и отклики не ждали загрузки с диска
      const mem = button(m.loaded ? "Выгрузить" : "В память", { size: "sm", iconName: m.loaded ? "x" : "up", title: m.loaded ? "Освободить видеокарту" : "Загрузить в видеокарту на 3 часа" });
      mem.addEventListener("click", () => busy(mem, async () => {
        try {
          if (!m.loaded) toast(`Загружаю ${m.name}: это до пары минут`, "info");
          data = await post(m.loaded ? "ollama/unload" : "ollama/load", { name: m.name });
          render(); toast(m.loaded ? "Выгружена" : "Модель в памяти");
        } catch (err) { toast(err.message, "error"); }
      }));
      const del = button("", { size: "sm", variant: "ghost", iconName: "trash", title: `Удалить ${m.name}` });
      del.addEventListener("click", async () => {
        if (!(await confirmDialog({ title: `Удалить ${m.name}?`, text: `Освободится ${gb(m.size)}. Чтобы вернуть модель, её придётся скачать заново.`, confirm: "Удалить", danger: true }))) return;
        try { data = await post("ollama/delete", { name: m.name }); render(); toast("Модель удалена"); } catch (err) { toast(err.message, "error"); }
      });
      return h("li", { class: "list-item" },
        h("div", { class: "list-item-main" },
          h("div", { class: "row" }, h("span", { class: "list-item-title mono" }, m.name), m.loaded ? h("span", { class: "badge badge-success" }, "В памяти") : null),
          h("div", { class: "list-item-meta" }, [m.params, m.quant, gb(m.size)].filter(Boolean).join(", "))),
        h("div", { class: "model-controls" }, target, use, mem, del));
    }));
  }

  function pullForm() {
    const have = new Set(data.models.map((m) => m.name));
    const name = input({ placeholder: "Например, qwen3:8b", "aria-label": "Имя модели" });
    const go = button("Скачать", { iconName: "down" });
    const start = (n, btn) => busy(btn, async () => {
      try { await post("ollama/pull", { name: n }); name.value = ""; load(); } catch (err) { toast(err.message, "error"); }
    });
    go.addEventListener("click", () => start(name.value, go));
    const suggestions = data.suggested.filter((s) => !have.has(s.name));
    return h("div", { class: "stack-sm" },
      suggestions.length ? h("span", { class: "field-label" }, "Рекомендуем") : null,
      suggestions.length ? h("ul", { class: "list list-flush" }, suggestions.map((s) => {
        const b = button("Скачать", { size: "sm", iconName: "down" });
        b.addEventListener("click", () => start(s.name, b));
        return h("li", { class: "list-item" },
          h("div", { class: "list-item-main" },
            h("div", { class: "row" }, h("span", { class: "list-item-title mono" }, s.name), h("span", { class: "subtle" }, s.size),
              s.vision ? h("span", { class: "badge badge-info plain" }, "картинки") : null),
            h("div", { class: "list-item-meta" }, s.note)),
          b);
      })) : null,
      h("span", { class: "field-label" }, "Другая модель"),
      h("div", { class: "input-group" }, name, go),
      h("p", { class: "subtle" }, "Имя как в каталоге ollama.com/library."));
  }

  function render() {
    const badge = card.querySelector("#ollama-badge");
    if (!data.ok) {
      badge.className = "badge badge-warning"; badge.textContent = "Не подключена";
      mount(body,
        callout("warning",
          h("p", null, "Ollama не отвечает. На ПК запустите панель вместе с Ollama: ", h("code", null, "docker compose -f docker-compose.pc.yml up -d"), ", или установите Ollama для Windows и укажите адрес ниже."),
          h("p", { class: "subtle" }, data.error)),
        urlEditor());
      return;
    }
    badge.className = "badge badge-success"; badge.textContent = `Ollama ${data.version || ""}`.trim();
    mount(body, pulls(), installed(), pullForm(), urlEditor());
  }

  load();
  card.destroy = () => clearTimeout(timer);
  return card;
}
