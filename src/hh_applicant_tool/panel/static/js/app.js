import { get, post, put, setUnauthorizedHandler, ApiError } from "./api.js";
import { h, icon, mount, button, field, input, toast, busy } from "./ui.js";
import { startPrompts } from "./prompts.js";
import overview from "./views/overview.js";
import vacancies from "./views/vacancies.js";
import negotiations from "./views/negotiations.js";
import chats from "./views/chats.js";
import resumes from "./views/resumes.js";
import run from "./views/run.js";
import jobs from "./views/jobs.js";
import schedule from "./views/schedule.js";
import journal from "./views/journal.js";
import models from "./views/models.js";
import texts from "./views/texts.js";
import config from "./views/config.js";
import system from "./views/system.js";
import login from "./views/login.js";

// Меню собрано по задачам соискателя: сначала поиск работы (то, что
// делают каждый день), потом автоматизация, потом редкие настройки.
// В группе не больше четырёх пунктов
const ROUTES = [
  { path: "overview", title: "Обзор", icon: "home", view: overview },
  { section: "Поиск работы" },
  { path: "vacancies", title: "Вакансии", icon: "search", view: vacancies },
  { path: "negotiations", title: "Отклики", icon: "inbox", view: negotiations },
  { path: "chats", title: "Чаты", icon: "chat", view: chats, counter: "chats" },
  { path: "resumes", title: "Резюме", icon: "user", view: resumes },
  { section: "Автоматизация" },
  { path: "run", title: "Запуск", icon: "play", view: run },
  { path: "jobs", title: "Задачи", icon: "terminal", view: jobs, counter: "jobs" },
  { path: "schedule", title: "Расписание", icon: "clock", view: schedule },
  { path: "journal", title: "Журнал AI", icon: "sparkle", view: journal },
  { section: "Настройки" },
  { path: "models", title: "Модели", icon: "cpu", view: models },
  { path: "texts", title: "Тексты", icon: "mail", view: texts },
  { path: "config", title: "Конфиг", icon: "file", view: config },
  { path: "system", title: "Система", icon: "settings", view: system, counter: "updates" },
  { path: "login", title: "Вход в hh.ru", view: login, hidden: true },
  // Старые адреса ведут в новые места
  { path: "stats", redirect: "negotiations?tab=stats" },
  { path: "letters", redirect: "texts" },
];

const state = { info: null, cleanup: null, timers: [], started: false, account: null, profiles: null };

// ---- Тема ----
function currentTheme() {
  const forced = document.documentElement.getAttribute("data-theme");
  if (forced) return forced;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function themeButton() {
  const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button" });
  const render = () => {
    const dark = currentTheme() === "dark";
    btn.replaceChildren(icon(dark ? "sun" : "moon", 16), dark ? "Светлая тема" : "Тёмная тема");
  };
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("hh-panel-theme", next); } catch { /* ничего */ }
    render();
  });
  render();
  return btn;
}

// ---- Аккаунт и профиль ----
const accountHost = h("div", { class: "account" });
document.addEventListener("click", (e) => { if (!accountHost.contains(e.target) && state.closeAccount) state.closeAccount(); });

function renderAccount() {
  const p = state.account;
  const profiles = state.profiles;
  const active = profiles ? profiles.items.find((x) => x.active) : null;
  let name = "Загрузка…", sub = "", status = "";
  if (p) {
    if (p.logged_in === false) { name = "Вход не выполнен"; sub = "Нажмите, чтобы войти"; status = "off"; }
    else if (p.error) { name = "Ошибка входа"; sub = "Войдите заново"; status = "error"; }
    else { name = [p.me.first_name, p.me.last_name].filter(Boolean).join(" ") || "Аккаунт hh.ru"; sub = p.me.email || ""; status = "on"; }
  }
  const trigger = h("button", { type: "button", class: "account-trigger", "aria-haspopup": "true", "aria-expanded": "false" },
    h("span", { class: `account-avatar account-${status}`, "aria-hidden": "true" }, icon("user", 16)),
    h("span", { class: "account-text" }, h("span", { class: "account-name truncate" }, name),
      h("span", { class: "account-sub truncate" }, [active && profiles.items.length > 1 ? `Профиль: ${active.title}` : null, sub].filter(Boolean).join(", "))),
    icon("down", 14));
  const menu = h("div", { class: "account-menu", role: "menu", hidden: true });
  const close = () => { menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
    if (open) (menu.querySelector("button, a") || menu).focus();
  });
  state.closeAccount = close;
  menu.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); trigger.focus(); } });

  const item = (label, iconName, onClick, extra = {}) => h("button", { type: "button", role: "menuitem", class: "account-item", onclick: () => { close(); onClick(); }, ...extra }, icon(iconName, 16), label);
  const items = [];
  if (!p || p.logged_in === false || p.error) items.push(item("Войти в hh.ru", "login", () => navigate("login")));
  else items.push(item("Войти в другой аккаунт", "login", () => navigate("login")));
  if (profiles && profiles.items.length) {
    items.push(h("div", { class: "account-sep", role: "separator" }), h("div", { class: "account-label" }, "Профили"));
    for (const pr of profiles.items) {
      items.push(item(pr.title, pr.active ? "check" : (pr.logged_in ? "user" : "x"), async () => {
        if (pr.active) return;
        try { state.profiles = await put("profiles/active", { name: pr.name }); toast(`Профиль «${pr.title}»`); await refreshAccount(); render(); }
        catch (err) { toast(err.message, "error"); }
      }, { "aria-current": pr.active ? "true" : undefined }));
    }
    items.push(item("Управление профилями", "settings", () => navigate("system", { tab: "profiles" })));
  }
  mount(menu, ...items);
  mount(accountHost, trigger, menu);
}

async function refreshAccount() {
  const [profile, profiles] = await Promise.allSettled([get("profile"), get("profiles")]);
  state.account = profile.status === "fulfilled" ? profile.value : { error: profile.reason.message };
  state.profiles = profiles.status === "fulfilled" ? profiles.value : null;
  renderAccount();
  // Тот же счётчик, что «Непрочитанные чаты» на обзоре: чаты, а не сообщения
  setUnreadChats(state.account?.me?.counters?.unread_negotiations ?? 0);
}

// ---- Каркас ----
function shell() {
  const app = h("div", { class: "app" });
  const nav = h("nav", { class: "nav", "aria-label": "Разделы" });
  for (const r of ROUTES) {
    if (r.hidden || r.redirect) continue;
    if (r.section) { nav.appendChild(h("div", { class: "nav-section" }, r.section)); continue; }
    const counter = r.counter ? h("span", { class: `count count-${r.counter}`, hidden: true }) : null;
    nav.appendChild(h("a", { class: "nav-item", href: `#/${r.path}`, dataset: { path: r.path } }, icon(r.icon), r.title, counter));
  }
  const closeNav = () => app.classList.remove("nav-open");
  nav.addEventListener("click", (e) => { if (e.target.closest("a")) closeNav(); });

  const sidebar = h("aside", { class: "sidebar", id: "sidebar" },
    h("div", { class: "brand" },
      h("img", { class: "brand-mark", src: "img/logo.svg", alt: "", width: 34, height: 34 }),
      h("div", null,
        h("div", { class: "brand-name" }, "Панель соискателя"),
        h("div", { class: "brand-version" }, state.info ? `hh-applicant-tool ${state.info.version}` : ""))),
    accountHost, nav,
    h("div", { class: "sidebar-footer" },
      h("a", { class: "subtle model-status", id: "model-status", href: "#/models", hidden: true }),
      themeButton(), state.info && state.info.auth_required ? h("div", { class: "subtle" }, "Доступ по токену") : null));

  const menuBtn = button("", { variant: "ghost", iconName: "menu", title: "Меню", onClick: () => app.classList.toggle("nav-open"), attrs: { "aria-controls": "sidebar" } });
  const topbar = h("div", { class: "topbar" }, menuBtn, h("span", { class: "topbar-title", id: "topbar-title" }, ""));
  const scrim = h("div", { class: "scrim", onclick: closeNav });
  const content = h("main", { class: "content", id: "content", tabindex: "-1" });
  app.append(sidebar, h("div", { class: "main" }, topbar, content), scrim);
  const syncScrim = () => { scrim.hidden = !app.classList.contains("nav-open"); };
  new MutationObserver(syncScrim).observe(app, { attributes: true, attributeFilter: ["class"] });
  syncScrim();
  return app;
}

// ---- Маршрутизация ----
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  return { path: path || "overview", params: new URLSearchParams(query) };
}

export function navigate(path, params) {
  const q = params ? `?${new URLSearchParams(params)}` : "";
  location.hash = `#/${path}${q}`;
}

async function render() {
  const { path, params } = parseHash();
  let route = ROUTES.find((r) => r.path === path) || ROUTES[0];
  if (route.redirect) { location.replace(`#/${route.redirect}`); return; }
  if (state.cleanup) { try { state.cleanup(); } catch { /* ничего */ } state.cleanup = null; }
  for (const a of document.querySelectorAll(".nav-item")) {
    if (a.dataset.path === route.path) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  document.title = `${route.title} · Панель hh-applicant-tool`;
  const tt = document.getElementById("topbar-title");
  if (tt) tt.textContent = route.title;
  const content = document.getElementById("content");
  mount(content);
  const ctx = { params, navigate, info: state.info, refreshRunning, refreshAccount, account: () => state.account };
  try {
    const cleanup = await route.view(content, ctx);
    if (typeof cleanup === "function") state.cleanup = cleanup;
  } catch (err) {
    console.error(err);
    toast(err.message || String(err), "error");
  }
}

// ---- Счётчики в меню ----
function setCount(name, n, label) {
  const el = document.querySelector(`.count-${name}`);
  if (!el) return;
  el.hidden = !n;
  el.textContent = n > 99 ? "99+" : String(n);
  el.setAttribute("aria-label", `${label}: ${n}`);
}

async function refreshRunning() {
  try {
    const { jobs: list } = await get("jobs");
    setCount("jobs", list.filter((j) => j.status === "running" || j.status === "queued").length, "активных задач");
  } catch { /* не мешаем основному экрану */ }
}

// Какая модель сейчас в видеокарте: видно, что Ollama жива и что грузится
async function refreshModel() {
  const el = document.getElementById("model-status");
  if (!el) return;
  try {
    const r = await get("ollama/ps");
    const gb = (b) => (b / 2 ** 30).toFixed(1).replace(".", ",");
    el.hidden = false;
    const text = !r.ok ? "Ollama недоступна"
      : r.models.length ? r.models.map((m) => `${m.name}, ${gb(m.size_vram || 0)} ГБ`).join("\n")
      : "Модель не загружена";
    mount(el, icon("cpu", 16), h("span", null, text));
    el.classList.toggle("is-live", Boolean(r.ok && r.models.length));
    el.title = r.ok ? "Модели в памяти Ollama" : r.error;
  } catch { el.hidden = true; }
}

async function refreshUpdates() {
  try {
    const u = await get("updates");
    setCount("updates", u.available ? 1 : 0, "доступно обновление");
  } catch { /* ничего */ }
}

export function setUnreadChats(n) { setCount("chats", n, "чатов с новыми сообщениями"); }

// ---- Вход в панель по токену ----
function loginScreen() {
  const root = document.getElementById("root");
  const tokenInput = input({ type: "password", autocomplete: "current-password", required: true });
  const errorEl = h("div", { class: "field-error", role: "alert" });
  const submit = button("Войти", { variant: "primary", type: "submit" });
  const form = h("form", { class: "card" },
    h("div", { class: "card-body stack" },
      h("div", { class: "brand" }, h("img", { class: "brand-mark", src: "img/logo.svg", alt: "", width: 34, height: 34 }),
        h("div", null, h("div", { class: "brand-name" }, "Панель соискателя"), h("div", { class: "brand-version" }, "Нужен токен доступа"))),
      field({ label: "Токен", control: tokenInput, help: "Лежит в файле panel-token.txt в папке конфига на компьютере с панелью (на ПК: D:\\hh-applicant-tool\\config). Вводится один раз, браузер запомнит вход."}),
      errorEl, submit));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    busy(submit, async () => {
      try { await post("login", { token: tokenInput.value }); start(); }
      catch (err) {
        errorEl.textContent = err instanceof ApiError ? err.message : String(err);
        tokenInput.setAttribute("aria-invalid", "true");
        tokenInput.focus();
      }
    });
  });
  mount(root, h("div", { class: "login" }, form));
  tokenInput.focus();
}

async function start() {
  const session = await get("session").catch(() => null);
  if (session && session.auth_required && !session.authorized) { loginScreen(); return; }
  state.info = await get("info").catch(() => null);
  mount(document.getElementById("root"), shell());
  renderAccount();
  if (!state.started) {
    window.addEventListener("hashchange", render);
    state.started = true;
  }
  render();
  refreshAccount();
  refreshRunning();
  refreshUpdates();
  state.timers.forEach(clearInterval);
  refreshModel();
  state.timers = [setInterval(refreshRunning, 5000), setInterval(refreshAccount, 120000), setInterval(refreshModel, 15000)];
  startPrompts({ onAnswered: refreshRunning });
}

setUnauthorizedHandler(() => loginScreen());
start();
