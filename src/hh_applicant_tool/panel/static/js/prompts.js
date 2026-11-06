// Глобальный диалог «нужен ваш ответ»: код из СМС, пароль, капча.
// Задача или запрос панели к hh стоят и ждут человека, поэтому это
// блокирующее уведомление (диалог), а не строка в логе. Его можно
// отложить: тогда в углу остаётся заметная плашка, и задача продолжает ждать.
import { get, post } from "./api.js";
import { h, mount, button, input, busy, icon } from "./ui.js";

let timer = null;
let snoozed = new Set();
let current = null;
const dlg = h("dialog", { class: "modal modal-prompt", "aria-labelledby": "prompt-title" });
const pill = h("button", { type: "button", class: "prompt-pill", hidden: true });

function keyOf(p) { return p.captcha_id || `${p.job_id}:${p.kind}:${p.prompt}:${p.image || ""}`; }

function show(p, onAnswered) {
  current = p;
  const answer = input({
    type: p.secret ? "password" : "text",
    autocomplete: p.kind === "captcha" ? "off" : "one-time-code",
    inputmode: /код/i.test(p.prompt || "") ? "numeric" : undefined,
    "aria-label": p.prompt || "Ответ",
  });
  const error = h("div", { class: "field-error", role: "alert" });
  const send = button("Отправить", { variant: "primary", type: "submit" });
  const later = button("Позже", { variant: "ghost", onClick: () => { snoozed.add(keyOf(p)); close(); } });
  const image = p.kind === "captcha"
    ? h("img", { class: "captcha-img", alt: "Капча: текст на картинке", src: p.captcha_id ? `data:image/png;base64,${p.image_data}` : `api/jobs/${p.job_id}/captcha?t=${Date.now()}` })
    : null;
  const form = h("form", { class: "modal-body" },
    h("div", { class: "row" }, icon(p.kind === "captcha" ? "eye" : "key", 20),
      h("h3", { id: "prompt-title" }, p.kind === "captcha" ? "Введите капчу" : "Нужен ваш ответ")),
    h("p", { class: "muted" }, p.title ? `${p.title}: ` : "", p.kind === "captcha" ? "hh.ru просит подтвердить, что вы человек." : (p.prompt || "Команда ждёт ввода.")),
    image, answer, error,
    p.job_id ? h("a", { href: `#/jobs?id=${p.job_id}`, class: "subtle", onclick: () => close() }, "Открыть задачу с логом") : null,
    h("div", { class: "row-between" }, later, send));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (!answer.value.trim() && p.kind === "captcha") { answer.setAttribute("aria-invalid", "true"); answer.focus(); return; }
    busy(send, async () => {
      try {
        await post("prompts/answer", { job_id: p.job_id, captcha_id: p.captcha_id, text: answer.value });
        close();
        onAnswered && onAnswered();
        setTimeout(poll, 600);
      } catch (err) {
        error.textContent = err.message;
      }
    });
  });
  mount(dlg, form);
  if (!dlg.open) dlg.showModal();
  answer.focus();
}

function close() {
  current = null;
  if (dlg.open) dlg.close();
}

let onAnsweredCb = null;

async function poll() {
  let items = [];
  try { items = (await get("prompts")).items; } catch { return; }
  const keys = new Set(items.map(keyOf));
  snoozed = new Set([...snoozed].filter((k) => keys.has(k)));
  // Текущий вопрос ушёл (ответили в логе или задача закончилась)
  if (current && !keys.has(keyOf(current))) close();
  const waiting = items.filter((p) => !snoozed.has(keyOf(p)));
  pill.hidden = !items.length || (dlg.open && items.length === 1);
  if (items.length) {
    pill.replaceChildren(icon("key", 16), items.length === 1 ? "Ждёт вашего ответа" : `Ждут ответа: ${items.length}`);
  }
  if (!dlg.open && waiting.length) show(waiting[0], onAnsweredCb);
}

dlg.addEventListener("cancel", (e) => { e.preventDefault(); if (current) snoozed.add(keyOf(current)); close(); });
pill.addEventListener("click", () => { snoozed.clear(); poll(); });

export function startPrompts({ onAnswered } = {}) {
  onAnsweredCb = onAnswered;
  if (!dlg.isConnected) document.body.append(dlg, pill);
  clearInterval(timer);
  timer = setInterval(poll, 2000);
  poll();
}

export function pokePrompts() { setTimeout(poll, 300); }
