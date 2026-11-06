import { get, put, post } from "../api.js";
import {
  h, mount, button, field, input, textarea, toast, busy, empty, skeleton, errorState,
  pageHeader, callout, confirmDialog, segmented, saveBar, icon,
} from "../ui.js";

// Живой предпросмотр с примером данных. Ошибки плейсхолдеров видны сразу
function previewBox() {
  const box = h("div", { class: "result", "aria-live": "polite" });
  let timer = null;
  const update = (text) => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!text.trim()) { box.textContent = "Пусто"; box.classList.add("subtle"); return; }
      box.classList.remove("subtle");
      try {
        const r = await post("letters/preview", { text });
        box.textContent = r.ok ? r.text : r.error;
        box.classList.toggle("field-error", !r.ok);
      } catch (err) { box.textContent = err.message; }
    }, 250);
  };
  return { box, update };
}

function placeholderChips(list, target) {
  return h("div", { class: "chips", "aria-label": "Плейсхолдеры" },
    list.map((p) => h("button", {
      type: "button", class: "chip", title: "Вставить в текст",
      onclick: () => {
        const token = `%(${p})s`;
        const { selectionStart: a = target.value.length, selectionEnd: b = target.value.length } = target;
        target.setRangeText(token, a, b, "end");
        target.dispatchEvent(new Event("input"));
        target.focus();
      },
    }, p)));
}

export default async function letters(root) {
  mount(root, pageHeader("Письма", "Сопроводительные письма к откликам."), skeleton(6));
  let data;
  try { data = await get("letters"); } catch (err) { mount(root, errorState(err, () => letters(root))); return; }

  let draft;
  const resetDraft = () => {
    draft = {
      cover_letter: data.cover_letter || "",
      cover_letter_prompt: data.cover_letter_prompt || "",
      templates: (data.templates || []).map((t) => ({ ...t, keywords: [...(t.keywords || [])] })),
    };
  };
  resetDraft();
  let tab = "templates";
  const body = h("div", { class: "stack" });
  const bar = saveBar({
    onSave: async () => {
      try {
        data = await put("letters", draft);
        toast("Письма сохранены");
        return true;
      } catch (err) { toast(err.message, "error"); return false; }
    },
    onReset: () => { resetDraft(); renderBody(); },
  });
  const markDirty = () => bar.set(true);

  // ---- шаблоны по ключевым словам ----
  function templateCard(t, i) {
    const name = input({ value: t.name, placeholder: "Например, Python-бэкенд" });
    const kw = input({ value: t.keywords.join(", "), placeholder: "python, django, fastapi" });
    const text = textarea({ rows: 6, value: t.text });
    const pv = previewBox();
    name.addEventListener("input", () => { t.name = name.value; markDirty(); });
    kw.addEventListener("input", () => { t.keywords = kw.value.split(",").map((s) => s.trim()).filter(Boolean); markDirty(); });
    text.addEventListener("input", () => { t.text = text.value; markDirty(); pv.update(text.value); });
    pv.update(t.text);

    const move = (d) => {
      const j = i + d;
      if (j < 0 || j >= draft.templates.length) return;
      [draft.templates[i], draft.templates[j]] = [draft.templates[j], draft.templates[i]];
      markDirty(); renderBody();
    };
    const removeBtn = button("", { size: "sm", variant: "ghost", iconName: "trash", title: "Удалить шаблон" });
    removeBtn.addEventListener("click", async () => {
      if (!(await confirmDialog({ title: `Удалить шаблон «${t.name || "без названия"}»?`, text: "Изменение запишется после «Сохранить».", confirm: "Удалить", danger: true }))) return;
      draft.templates.splice(i, 1); markDirty(); renderBody();
    });

    return h("article", { class: "card" },
      h("div", { class: "card-header" },
        h("div", { class: "row" }, h("span", { class: "badge plain" }, `#${i + 1}`), h("h3", null, t.name || "Без названия"),
          !t.keywords.length ? h("span", { class: "badge badge-info plain" }, "Запасной") : null),
        h("div", { class: "row" },
          button("", { size: "sm", variant: "ghost", iconName: "up", title: "Выше", onClick: () => move(-1), disabled: i === 0 }),
          button("", { size: "sm", variant: "ghost", iconName: "down", title: "Ниже", onClick: () => move(1), disabled: i === draft.templates.length - 1 }),
          removeBtn)),
      h("div", { class: "card-body stack" },
        h("div", { class: "form-grid" },
          field({ label: "Название", control: name, help: "По нему шаблон выбирают в запуске и в флаге --letter-template." }),
          field({ label: "Ключевые слова", control: kw, help: "Через запятую. Ищутся в названии и описании вакансии. Пусто: запасной шаблон." })),
        field({ label: "Текст", control: text, help: "{Здравствуйте|Добрый день} выберет вариант случайно." }),
        placeholderChips(data.placeholders, text),
        h("div", { class: "stack-sm" }, h("span", { class: "field-label" }, "Как будет выглядеть"), pv.box)));
  }

  function renderTemplates() {
    const add = button("Добавить шаблон", { iconName: "plus", onClick: () => {
      draft.templates.push({ name: `Шаблон ${draft.templates.length + 1}`, keywords: [], text: data.cover_letter_default || "" });
      markDirty(); renderBody();
    } });
    return [
      data.ai_configured ? callout("info", h("p", null, "Сейчас письма пишет AI. Шаблоны сработают при запуске с ", h("code", null, "--no-ai"), " или если выбрать шаблон явно.")) : null,
      h("details", { class: "disclosure" }, h("summary", null, (() => { const c = icon("chevron", 16); c.classList.add("chev"); return c; })(), "Как выбирается шаблон"),
        h("ol", { class: "muted stack-sm" },
          h("li", null, "Берётся первый шаблон сверху, чьё ключевое слово есть в названии или описании вакансии."),
          h("li", null, "Ничего не совпало: шаблон без ключевых слов (запасной)."),
          h("li", null, "Запасного нет: общее письмо."))),
      draft.templates.length ? draft.templates.map(templateCard)
        : h("div", { class: "card" }, empty({ iconName: "mail", title: "Шаблонов нет", text: "Сейчас для всех вакансий отправляется общее письмо. Добавьте шаблоны под разные направления." })),
      h("div", null, add),
    ];
  }

  // ---- общее письмо ----
  function renderDefault() {
    const ta = textarea({ rows: 8, value: draft.cover_letter, placeholder: data.cover_letter_default });
    const pv = previewBox();
    ta.addEventListener("input", () => { draft.cover_letter = ta.value; markDirty(); pv.update(ta.value || data.cover_letter_default); });
    pv.update(draft.cover_letter || data.cover_letter_default);
    return h("section", { class: "card" },
      h("div", { class: "card-header" }, h("div", null, h("h3", null, "Общее письмо"), h("p", { class: "card-hint" }, "Ключ cover_letter. Отправляется, когда AI выключен и ни один шаблон не подошёл. Пусто: письмо утилиты по умолчанию."))),
      h("div", { class: "card-body stack" }, field({ label: "Текст", control: ta }), placeholderChips(data.placeholders, ta),
        h("div", { class: "stack-sm" }, h("span", { class: "field-label" }, "Как будет выглядеть"), pv.box)));
  }

  // ---- промпт для AI ----
  function renderPrompt() {
    const ta = textarea({ rows: 14, class: "textarea code", value: draft.cover_letter_prompt, placeholder: data.cover_letter_prompt_default });
    ta.addEventListener("input", () => { draft.cover_letter_prompt = ta.value; markDirty(); });
    const useDefault = button("Вставить промпт по умолчанию", { size: "sm", variant: "ghost", onClick: () => { ta.value = data.cover_letter_prompt_default; ta.dispatchEvent(new Event("input")); } });
    return h("section", { class: "card" },
      h("div", { class: "card-header" }, h("div", null, h("h3", null, "Промпт для AI"), h("p", { class: "card-hint" }, "Ключ cover_letter_prompt. По нему модель из раздела «Модели → Сопроводительные письма» пишет письмо.")), useDefault),
      h("div", { class: "card-body stack" }, field({ label: "Промпт", control: ta }), placeholderChips(data.placeholders, ta)));
  }

  function renderBody() {
    const views = { templates: renderTemplates, default: renderDefault, prompt: renderPrompt };
    mount(body, ...[].concat(views[tab]()));
  }

  mount(root,
    pageHeader("Письма", "Сопроводительные письма к откликам: шаблоны по ключевым словам, общее письмо и промпт для AI."),
    h("div", { class: "stack" },
      h("div", null, segmented([["templates", "Шаблоны"], ["default", "Общее письмо"], ["prompt", "Промпт для AI"]], tab, (t) => { tab = t; renderBody(); }, "Раздел")),
      body,
      data.has_comments ? h("p", { class: "subtle" }, "При сохранении комментарии в config.toml пропадут, копия останется в config.toml.bak.") : null),
    bar.el);
  renderBody();
  return () => bar.destroy();
}
