import { get, post, put, del } from "../api.js";
import {
  h, mount, button, field, input, toast, busy, skeleton, errorState, pageHeader, icon,
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

// Как открыть панель с ноутбука, если она крутится на домашнем ПК
async function connectionTab(host) {
  mount(host, skeleton(4));
  let c;
  try { c = await get("connection"); } catch (err) { mount(host, errorState(err, () => connectionTab(host))); return; }
  if (!c.remote) {
    mount(host, callout("info",
      h("p", null, h("strong", null, "Панель открыта только на этом компьютере.")),
      h("p", null, "Чтобы заходить с ноутбука или телефона, запустите её так: ", h("code", null, "hh-applicant-tool panel --host 0.0.0.0"),
        ". Панель сама включит HTTPS и токен доступа. На Windows это делает скрипт установки, в Docker файл docker-compose.pc.yml.")));
    return;
  }
  const tokenBox = h("div", { class: "row" });
  const showToken = button("Показать токен", { size: "sm", iconName: "key" });
  showToken.addEventListener("click", () => busy(showToken, async () => {
    try {
      const { token } = await post("connection/token");
      mount(tokenBox, h("code", { class: "mono" }, token), button("", { size: "sm", variant: "ghost", iconName: "copy", title: "Копировать токен", onClick: () => copyText(token) }));
    } catch (err) { toast(err.message, "error"); }
  }));
  const logoutAll = button("Выйти на всех устройствах", { variant: "danger", iconName: "logout" });
  logoutAll.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "Выйти на всех устройствах?", text: "Везде, включая этот браузер, нужно будет снова ввести токен. Задачи и расписание продолжат работать.", confirm: "Выйти везде", danger: true }))) return;
    try { await post("connection/logout-all"); location.reload(); } catch (err) { toast(err.message, "error"); }
  });
  mount(host,
    h("section", { class: "card" },
      h("div", { class: "card-header" }, h("div", null, h("h3", null, "Адреса для входа"), h("p", { class: "card-hint" }, "Откройте один из них на ноутбуке в той же сети."))),
      c.urls.length
        ? h("ul", { class: "list" }, c.urls.map((u) => h("li", { class: "list-item" },
            h("code", { class: "mono grow" }, u),
            button("", { size: "sm", variant: "ghost", iconName: "copy", title: "Копировать адрес", onClick: () => copyText(u) }))))
        : h("div", { class: "card-body" }, h("p", { class: "muted" }, "Адрес в сети не определился. Задайте его флагом --public-host или переменной HH_PANEL_PUBLIC_HOSTS."))),
    h("section", { class: "card" },
      h("div", { class: "card-header" }, h("h3", null, "Защита")),
      h("div", { class: "card-body stack" },
        h("div", { class: "row-between" },
          h("div", null, h("div", { class: "list-item-title" }, "HTTPS"), h("p", { class: "subtle" }, c.tls ? "Трафик шифруется. Сертификат самоподписанный: при первом входе браузер предупредит, это ожидаемо." : "Выключен: токен и cookie идут по сети открытым текстом.")),
          h("span", { class: `badge ${c.tls ? "badge-success" : "badge-danger"}` }, c.tls ? "Включён" : "Выключен")),
        c.fingerprint ? h("details", { class: "disclosure" },
          h("summary", null, (() => { const c = icon("chevron", 16); c.classList.add("chev"); return c; })(), "Отпечаток сертификата"),
          h("p", { class: "subtle" }, "Сверьте с тем, что покажет браузер в сведениях о сертификате: так вы убедитесь, что подключились к своему ПК."),
          h("code", { class: "mono fingerprint" }, c.fingerprint)) : null,
        h("div", { class: "row-between" },
          h("div", null, h("div", { class: "list-item-title" }, "Токен доступа"), h("p", { class: "subtle" }, "Вводится один раз на каждом устройстве, браузер запоминает вход на год.", c.token_file ? ` Хранится в ${c.token_file}.` : "")),
          c.token_required ? showToken : h("span", { class: "badge badge-danger" }, "Не задан")),
        tokenBox,
        h("div", null, logoutAll))),
    h("section", { class: "card" },
      h("div", { class: "card-header" }, h("h3", null, "Как открыть с мака")),
      h("div", { class: "card-body" }, h("ol", { class: "stack-sm steps" },
        h("li", null, "Откройте адрес выше в браузере."),
        h("li", null, "Браузер предупредит о сертификате. Safari: «Показать подробности», затем «посетить этот веб-сайт». Chrome: «Дополнительные», затем «Перейти на сайт»."),
        h("li", null, "Введите токен. Дальше панель работает как обычный сайт: можно добавить в Dock через «Файл → Добавить в Dock» в Safari."),
        h("li", null, "Вне дома подключайтесь через свой VPN в домашнюю сеть, открывать порт в интернет не нужно.")))));
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
  let tab = ctx.params.get("tab") || "connection";
  const host = h("div", { class: "stack" });
  let cleanup = null;
  const show = async () => {
    if (cleanup) { cleanup(); cleanup = null; }
    if (tab === "profiles") await profilesTab(host, ctx);
    else if (tab === "log") cleanup = await logTab(host);
    else if (tab === "connection") await connectionTab(host);
    else await updatesTab(host);
  };
  mount(root,
    pageHeader("Система", "Доступ с других устройств, профили аккаунтов, лог утилиты и обновления."),
    h("div", { class: "stack" },
      h("div", null, segmented([["connection", "Подключение"], ["profiles", "Профили"], ["log", "Лог"], ["updates", "Обновления"]], tab, (t) => { tab = t; history.replaceState(null, "", `#/system?tab=${t}`); show(); }, "Раздел")),
      host));
  await show();
  return () => { if (cleanup) cleanup(); };
}
