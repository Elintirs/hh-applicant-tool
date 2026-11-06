import { get, post, put, del } from "../api.js";
import {
  h, mount, button, field, input, toast, busy, skeleton, errorState, pageHeader,
  segmented, callout, relTime, confirmDialog, switchControl, copyText,
} from "../ui.js";

async function profilesTab(host, ctx) {
  mount(host, skeleton(4));
  let data;
  try { data = await get("profiles"); } catch (err) { mount(host, errorState(err, () => profilesTab(host, ctx))); return; }
  const name = input({ placeholder: "work", pattern: "[A-Za-z0-9_.@-]+", "aria-label": "Имя нового профиля" });
  const create = button("Создать", { iconName: "plus", type: "submit" });
  const error = h("div", { class: "field-error", role: "alert" });
  const form = h("form", { class: "card-body stack-sm" },
    field({ label: "Новый профиль", control: name, help: "Латиница, цифры, точка, дефис. Будет отдельный каталог со своим config.toml и входом." }),
    error, h("div", null, create));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(create, async () => {
      try { await post("profiles", { name: name.value.trim() }); toast("Профиль создан и выбран. Теперь войдите в hh.ru"); await ctx.refreshAccount(); ctx.navigate("login"); }
      catch (err) { error.textContent = err.message; name.setAttribute("aria-invalid", "true"); }
    });
  });
  mount(host,
    callout("info", h("p", null, "Профиль это отдельный аккаунт hh.ru со своими настройками. Расписание запоминает профиль, с которым задача создана.")),
    h("section", { class: "card" },
      h("div", { class: "card-header" }, h("h3", null, "Профили")),
      h("ul", { class: "list" }, data.items.map((p) => h("li", { class: "list-item" },
        h("div", { class: "list-item-main" }, h("div", { class: "list-item-title" }, p.title), h("div", { class: "list-item-meta" }, p.name === "." ? "Каталог конфига целиком" : `Каталог ${p.name}`)),
        h("span", { class: `badge ${p.logged_in ? "badge-success" : ""}` }, p.logged_in ? "Вход выполнен" : "Без входа"),
        p.active ? h("span", { class: "badge badge-accent plain" }, "Активный")
          : button("Сделать активным", { size: "sm", onClick: (e) => busy(e.currentTarget, async () => {
            try { await put("profiles/active", { name: p.name }); await ctx.refreshAccount(); toast(`Активный профиль: ${p.title}`); profilesTab(host, ctx); }
            catch (err) { toast(err.message, "error"); }
          }) }))))),
    h("section", { class: "card" }, form));
}

const LEVELS = [["", "Все"], ["ERROR", "Ошибки"], ["WARNING", "Предупреждения"], ["INFO", "Инфо"], ["DEBUG", "Отладка"]];

async function logTab(host) {
  let level = "";
  let timer = null;
  let follow = true;
  const pre = h("pre", { class: "log log-file", tabindex: "0", "aria-label": "Лог утилиты" });
  const meta = h("span", { class: "subtle" });
  const load = async () => {
    try {
      const data = await get(`log${level ? `?level=${level}` : ""}`);
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
      pre.textContent = data.lines.join("\n");
      meta.textContent = `${data.path} · ${(data.size / 1024).toFixed(0)} КБ`;
      if (follow && atBottom) pre.scrollTop = pre.scrollHeight;
    } catch (err) { pre.textContent = err.message; }
  };
  const auto = switchControl("Обновлять", { checked: true, onChange: (c) => { follow = c; clearInterval(timer); if (c) timer = setInterval(load, 3000); } });
  const clearBtn = button("Очистить", { size: "sm", variant: "danger", iconName: "trash" });
  clearBtn.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "Очистить log.txt?", text: "Записи удалятся из файла. История задач панели останется.", confirm: "Очистить", danger: true }))) return;
    try { await del("log"); toast("Лог очищен"); load(); } catch (err) { toast(err.message, "error"); }
  });
  mount(host, h("section", { class: "card" },
    h("div", { class: "log-toolbar" }, segmented(LEVELS, level, (l) => { level = l; load(); }, "Уровень"), h("span", { class: "grow" }), auto,
      button("", { size: "sm", variant: "ghost", iconName: "copy", title: "Копировать", onClick: () => copyText(pre.textContent) }), clearBtn),
    h("div", { class: "log-wrap" }, pre),
    h("div", { class: "card-footer" }, meta)));
  await load();
  pre.scrollTop = pre.scrollHeight;
  timer = setInterval(load, 3000);
  return () => clearInterval(timer);
}

async function updatesTab(host, force = false) {
  mount(host, skeleton(3));
  let u;
  try { u = await get(`updates${force ? "?force=1" : ""}`); } catch (err) { mount(host, errorState(err, () => updatesTab(host, true))); return; }
  const check = button("Проверить сейчас", { iconName: "refresh", onClick: (e) => busy(e.currentTarget, () => updatesTab(host, true)) });
  mount(host,
    u.error ? callout("warning", h("p", null, `Не удалось проверить: ${u.error}`))
      : u.available ? callout("info", h("p", null, h("strong", null, `Доступна версия ${u.remote}`), `, у вас ${u.local}.`),
        h("p", null, "В Docker пересоберите образ: ", h("code", null, "docker compose -f docker-compose.panel.yml up -d --build"), ". Без Docker: ", h("code", null, "pip install -U hh-applicant-tool"), "."),
        h("p", { class: "subtle" }, "Это версия апстрима на PyPI. Если вы работаете на форке, сначала подтяните изменения в него."))
        : callout("success", h("p", null, `У вас последняя версия: ${u.local}.`)),
    h("div", { class: "row" }, check, h("span", { class: "subtle" }, u.checked_at ? `Проверено ${relTime(u.checked_at)}` : "")));
}

export default async function system(root, ctx) {
  let tab = ctx.params.get("tab") || "profiles";
  const host = h("div", { class: "stack" });
  let cleanup = null;
  const show = async () => {
    if (cleanup) { cleanup(); cleanup = null; }
    if (tab === "profiles") await profilesTab(host, ctx);
    else if (tab === "log") cleanup = await logTab(host);
    else await updatesTab(host);
  };
  mount(root,
    pageHeader("Система", "Профили аккаунтов, лог утилиты и обновления."),
    h("div", { class: "stack" },
      h("div", null, segmented([["profiles", "Профили"], ["log", "Лог"], ["updates", "Обновления"]], tab, (t) => { tab = t; history.replaceState(null, "", `#/system?tab=${t}`); show(); }, "Раздел")),
      host));
  await show();
  return () => { if (cleanup) cleanup(); };
}
