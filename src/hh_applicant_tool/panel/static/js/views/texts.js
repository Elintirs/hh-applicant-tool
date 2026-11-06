import { get, put, post } from "../api.js";
import {
  h, mount, button, field, input, textarea, toast, busy, skeleton, errorState, pageHeader,
  segmented, saveBar, switchControl,
} from "../ui.js";
import letters from "./letters.js";

// Все тексты, которые утилита отправляет от вашего имени или в модель,
// в одном разделе: письма к откликам, промпты, ответы в чатах, почта
const TABS = [
  ["letters", "Письма"],
  ["prompts", "Промпты AI"],
  ["chats", "Чаты"],
  ["mail", "Почта"],
];
const GROUPS = { prompts: ["vacancies", "tests"], chats: ["chats"], mail: ["mail"] };

function previewer() {
  const box = h("div", { class: "result", "aria-live": "polite" });
  let t = null;
  const update = (text) => {
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        const r = await post("texts/preview", { text });
        box.textContent = r.ok ? r.text : r.error;
        box.classList.toggle("field-error", !r.ok);
      } catch (err) { box.textContent = err.message; }
    }, 300);
  };
  return { box, update };
}

async function textsTab(host, tab) {
  mount(host, skeleton(6));
  let data;
  try { data = await get("texts"); } catch (err) { mount(host, errorState(err, () => textsTab(host, tab))); return; }
  const values = {};
  let smtp = { ...data.smtp };
  let smtpFrom = data.smtp_from || "";
  const bar = saveBar({
    onSave: async () => {
      try {
        data = await put("texts", tab === "mail" ? { values, smtp, smtp_from: smtpFrom } : { values });
        toast("Тексты сохранены");
        return true;
      } catch (err) { toast(err.message, "error"); return false; }
    },
    onReset: () => textsTab(host, tab),
  });

  const items = data.items.filter((i) => GROUPS[tab].includes(i.group));
  const cards = items.map((item) => {
    const ta = textarea({ rows: item.default.length > 200 ? 12 : 3, class: `textarea${item.default.length > 200 ? " code" : ""}`, value: item.value || item.default });
    const pv = previewer();
    const custom = h("span", { class: `badge plain ${item.value ? "badge-info" : ""}` }, item.value ? "Свой текст" : "По умолчанию");
    ta.addEventListener("input", () => {
      values[item.key] = ta.value;
      custom.textContent = ta.value && ta.value !== item.default ? "Свой текст" : "По умолчанию";
      custom.classList.toggle("badge-info", ta.value !== item.default);
      bar.set(true);
      pv.update(ta.value);
    });
    pv.update(ta.value);
    return h("section", { class: "card" },
      h("div", { class: "card-header" },
        h("div", null, h("div", { class: "row" }, h("h3", null, item.title), custom), h("p", { class: "card-hint" }, item.hint, " ", h("code", null, item.key))),
        button("Вернуть по умолчанию", { size: "sm", variant: "ghost", iconName: "undo", onClick: () => { ta.value = item.default; ta.dispatchEvent(new Event("input")); } })),
      h("div", { class: "card-body stack" }, ta,
        h("details", { class: "disclosure" }, h("summary", null, "Как будет выглядеть"), h("p", { class: "subtle" }, "Неизвестные здесь данные показаны как ‹имя›: их подставит утилита."), pv.box)));
  });

  let mail = null;
  if (tab === "mail") {
    const f = (key, label, attrs = {}) => {
      const inp = input({ value: smtp[key] ?? "", ...attrs });
      inp.addEventListener("input", () => { smtp[key] = inp.value; bar.set(true); });
      return field({ label, control: inp, flag: key });
    };
    const fromInp = input({ value: smtpFrom, type: "email", placeholder: "Как у пользователя SMTP" });
    fromInp.addEventListener("input", () => { smtpFrom = fromInp.value; bar.set(true); });
    const ssl = switchControl("SSL с первого байта (порт 465)", { checked: !!smtp.ssl, onChange: (c) => { smtp.ssl = c; bar.set(true); } });
    const tls = switchControl("STARTTLS (порт 587)", { checked: smtp.starttls !== false, onChange: (c) => { smtp.starttls = c; bar.set(true); } });
    const to = input({ type: "email", placeholder: "Куда прислать проверку" });
    const testBtn = button("Отправить проверку", { iconName: "send" });
    testBtn.addEventListener("click", () => busy(testBtn, async () => {
      if (bar.dirty) { toast("Сначала сохраните настройки", "info"); return; }
      try { await post("smtp/test", { to: to.value }); toast("Письмо отправлено. Проверьте почту"); }
      catch (err) { toast(err.message, "error"); }
    }));
    mail = h("section", { class: "card" },
      h("div", { class: "card-header" }, h("div", null, h("h3", null, "Почтовый сервер"), h("p", { class: "card-hint" }, "Для отклика с флагом --send-email: письмо уходит ещё и на почту работодателя, если она есть в вакансии."))),
      h("div", { class: "card-body stack" },
        h("div", { class: "form-grid" }, f("host", "Сервер", { placeholder: "smtp.yandex.ru" }), f("port", "Порт", { type: "number", placeholder: "465" }),
          f("user", "Логин"), f("password", "Пароль", { type: "password", autocomplete: "new-password" }),
          field({ label: "Адрес отправителя", control: fromInp, flag: "from" })),
        h("div", { class: "row" }, ssl, tls),
        h("div", { class: "input-group" }, to, testBtn)));
  }

  mount(host, ...(tab === "mail" ? [mail] : []), ...cards,
    data.has_comments ? h("p", { class: "subtle" }, "При сохранении комментарии в config.toml пропадут, копия останется в config.toml.bak.") : null,
    bar.el);
  host._cleanup = () => bar.destroy();
}

export default async function texts(root, ctx) {
  let tab = ctx.params.get("tab") || "letters";
  const host = h("div", { class: "stack" });
  let cleanup = null;
  const show = async () => {
    if (cleanup) { cleanup(); cleanup = null; }
    if (host._cleanup) { host._cleanup(); host._cleanup = null; }
    if (tab === "letters") cleanup = await letters(host, ctx, { embedded: true });
    else await textsTab(host, tab);
  };
  mount(root,
    pageHeader("Тексты", "Всё, что утилита пишет от вашего имени: письма к откликам, промпты для модели, ответы в чатах и письма на почту."),
    h("div", { class: "stack" },
      h("div", null, segmented(TABS, tab, (t) => { tab = t; history.replaceState(null, "", `#/texts?tab=${t}`); show(); }, "Раздел")),
      host));
  await show();
  return () => { if (cleanup) cleanup(); if (host._cleanup) host._cleanup(); };
}
