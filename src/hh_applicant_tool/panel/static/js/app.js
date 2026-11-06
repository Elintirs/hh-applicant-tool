import { get, post, setUnauthorizedHandler, ApiError } from "./api.js";
import { h, icon, mount, button, field, input, toast, busy } from "./ui.js";
import overview from "./views/overview.js";
import run from "./views/run.js";
import jobs from "./views/jobs.js";
import schedule from "./views/schedule.js";
import models from "./views/models.js";
import letters from "./views/letters.js";
import stats from "./views/stats.js";
import config from "./views/config.js";

const ROUTES = [
  { path: "overview", title: "Обзор", icon: "home", view: overview },
  { path: "run", title: "Запуск", icon: "play", view: run },
  { path: "jobs", title: "Задачи", icon: "terminal", view: jobs, counter: true },
  { path: "schedule", title: "Расписание", icon: "clock", view: schedule },
  { section: "Настройка" },
  { path: "models", title: "Модели", icon: "cpu", view: models },
  { path: "letters", title: "Письма", icon: "mail", view: letters },
  { path: "stats", title: "Статистика", icon: "chart", view: stats },
  { path: "config", title: "Конфиг", icon: "file", view: config },
];

const state = { info: null, cleanup: null, runningTimer: null };

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

// ---- Каркас ----
function shell() {
  const app = h("div", { class: "app" });
  const nav = h("nav", { class: "nav", "aria-label": "Разделы" });
  for (const r of ROUTES) {
    if (r.section) { nav.appendChild(h("div", { class: "nav-section" }, r.section)); continue; }
    const counter = r.counter ? h("span", { class: "count", hidden: true, "aria-label": "выполняется" }) : null;
    nav.appendChild(h("a", { class: "nav-item", href: `#/${r.path}`, dataset: { path: r.path } }, icon(r.icon), r.title, counter));
  }
  const closeNav = () => app.classList.remove("nav-open");
  nav.addEventListener("click", (e) => { if (e.target.closest("a")) closeNav(); });

  const footer = h("div", { class: "sidebar-footer" }, themeButton());
  if (state.info && state.info.auth_required) {
    footer.appendChild(h("div", { class: "subtle" }, "Доступ по токену"));
  }

  const sidebar = h("aside", { class: "sidebar", id: "sidebar" },
    h("div", { class: "brand" },
      h("div", { class: "brand-mark", "aria-hidden": "true" }, "HH"),
      h("div", null,
        h("div", { class: "brand-name" }, "Панель соискателя"),
        h("div", { class: "brand-version" }, state.info ? `hh-applicant-tool ${state.info.version}` : ""))),
    nav, footer);

  const menuBtn = button("", { variant: "ghost", iconName: "menu", title: "Меню", onClick: () => app.classList.toggle("nav-open"),
    attrs: { "aria-controls": "sidebar" } });
  const topbar = h("div", { class: "topbar" }, menuBtn, h("span", { class: "topbar-title", id: "topbar-title" }, ""));
  const scrim = h("div", { class: "scrim", onclick: closeNav });
  const content = h("main", { class: "content", id: "content", tabindex: "-1" });
  app.append(sidebar, h("div", { class: "main" }, topbar, content));
  app.appendChild(scrim);
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
  const route = ROUTES.find((r) => r.path === path) || ROUTES[0];
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
  const ctx = { params, navigate, info: state.info, refreshRunning };
  try {
    const cleanup = await route.view(content, ctx);
    if (typeof cleanup === "function") state.cleanup = cleanup;
  } catch (err) {
    console.error(err);
    toast(err.message || String(err), "error");
  }
}

// ---- Счётчик выполняющихся задач в меню ----
async function refreshRunning() {
  try {
    const { jobs } = await get("jobs");
    const active = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const el = document.querySelector('.nav-item[data-path="jobs"] .count');
    if (el) { el.hidden = !active; el.textContent = String(active); el.setAttribute("aria-label", `активных задач: ${active}`); }
  } catch { /* не мешаем основному экрану */ }
}

// ---- Вход по токену ----
function loginScreen() {
  const root = document.getElementById("root");
  const tokenInput = input({ type: "password", autocomplete: "current-password", required: true });
  const errorEl = h("div", { class: "field-error", role: "alert" });
  const submit = button("Войти", { variant: "primary", type: "submit" });
  const form = h("form", { class: "card" },
    h("div", { class: "card-body stack" },
      h("div", { class: "brand" }, h("div", { class: "brand-mark", "aria-hidden": "true" }, "HH"),
        h("div", null, h("div", { class: "brand-name" }, "Панель соискателя"), h("div", { class: "brand-version" }, "Нужен токен доступа"))),
      field({ label: "Токен", control: tokenInput, help: "Тот, что задан в HH_PANEL_TOKEN или флаге --token." }),
      errorEl, submit));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    errorEl.textContent = "";
    busy(submit, async () => {
      try {
        await post("login", { token: tokenInput.value });
        start();
      } catch (err) {
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
  window.addEventListener("hashchange", render);
  render();
  refreshRunning();
  clearInterval(state.runningTimer);
  state.runningTimer = setInterval(refreshRunning, 5000);
}

setUnauthorizedHandler(() => loginScreen());
start();
