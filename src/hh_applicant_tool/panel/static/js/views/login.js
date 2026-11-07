import { get, post } from "../api.js";
import { h, mount, button, field, input, busy, toast, pageHeader, callout, segmented, icon, appendAnsi } from "../ui.js";
import { pokePrompts } from "../prompts.js";

// Вход в hh.ru формой. Под капотом обычная команда authorize: панель
// запускает её, а код из СМС или капчу спрашивает в общем диалоге
export default async function login(root, ctx) {
  let mode = "code";
  let es = null;
  const username = input({ autocomplete: "username", inputmode: "email", placeholder: "+7 900 000-00-00 или name@example.com" });
  const password = input({ type: "password", autocomplete: "current-password" });
  const pwdField = field({ label: "Пароль", control: password, help: "Не попадёт в историю задач и в список процессов." });
  pwdField.hidden = true;
  const error = h("div", { class: "field-error", role: "alert" });
  const submit = button("Получить код", { variant: "primary", type: "submit", iconName: "login" });
  const progress = h("div", { class: "stack-sm", "aria-live": "polite" });

  const modeCtl = segmented([["code", "По коду"], ["password", "По паролю"]], mode, (m) => {
    mode = m;
    pwdField.hidden = m !== "password";
    submit.lastChild.textContent = m === "password" ? "Войти" : "Получить код";
  }, "Способ входа");

  const form = h("form", { class: "card", "aria-labelledby": "login-title" },
    h("div", { class: "card-body stack" },
      h("div", { class: "login-hero" },
        h("img", { src: "img/logo.svg", alt: "", width: 56, height: 56 }),
        h("div", null, h("h2", { id: "login-title" }, "Аккаунт соискателя"), h("p", { class: "subtle" }, "Войдите тем же телефоном или почтой, что на hh.ru"))),
      h("p", { class: "muted" }, "Код придёт в СМС или на почту, его спросим здесь же. Если hh попросит капчу, она тоже появится в окне."),
      h("div", null, modeCtl),
      field({ label: "Телефон или почта", control: username }),
      pwdField, error, submit, progress));

  const step = (state, text) => h("div", { class: `login-step login-${state}` },
    state === "active" ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon(state === "done" ? "check" : state === "error" ? "alert" : "info", 16), text);

  function follow(job) {
    const log = h("pre", { class: "log log-compact", "aria-label": "Ход входа" });
    let ansi = { classes: [] };
    mount(progress, step("active", "Открываем страницу входа hh.ru…"), log);
    es = new EventSource(`api/jobs/${job.id}/stream`);
    es.addEventListener("output", (e) => { ansi = appendAnsi(log, JSON.parse(e.data).text, ansi); log.scrollTop = log.scrollHeight; });
    es.addEventListener("status", (e) => {
      const j = JSON.parse(e.data);
      if (j.prompt) { progress.firstChild.replaceWith(step("active", "Ждём ваш ответ в окне")); pokePrompts(); }
      else if (j.status === "running") progress.firstChild.replaceWith(step("active", "Входим…"));
      if (j.status === "success") {
        progress.firstChild.replaceWith(step("done", "Вход выполнен"));
        toast("Вход в hh.ru выполнен");
        ctx.refreshAccount();
        setTimeout(() => ctx.navigate("overview"), 800);
      } else if (["failed", "stopped", "canceled"].includes(j.status)) {
        progress.firstChild.replaceWith(step("error", "Войти не получилось. Причина в логе ниже."));
        submit.disabled = false;
      }
    });
    es.addEventListener("end", () => { es.close(); es = null; });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    error.textContent = "";
    if (!username.value.trim()) { username.setAttribute("aria-invalid", "true"); error.textContent = "Укажите телефон или почту"; username.focus(); return; }
    if (mode === "password" && !password.value) { password.setAttribute("aria-invalid", "true"); error.textContent = "Введите пароль"; password.focus(); return; }
    busy(submit, async () => {
      try {
        const job = await post("account/login", { username: username.value.trim(), password: mode === "password" ? password.value : "" });
        password.value = "";
        submit.disabled = true;
        follow(job);
      } catch (err) { error.textContent = err.message; }
    });
  });

  const account = ctx.account();
  mount(root,
    pageHeader("Вход в hh.ru", null),
    h("div", { class: "login-layout" }, form,
      h("div", { class: "stack" },
        account && account.logged_in && !account.error ? callout("info", h("p", null, "Вы уже вошли. Новый вход заменит текущий аккаунт в этом профиле. Чтобы держать несколько аккаунтов, заведите профиль в разделе «Система».")) : null,
        callout("warning", h("p", null, "Вход занимает до минуты: утилита выжидает паузы, как живой человек.")))));
  username.focus();
  get("profiles").catch(() => {});
  return () => { if (es) es.close(); };
}
