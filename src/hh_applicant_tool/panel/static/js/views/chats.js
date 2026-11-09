import { get, post } from "../api.js";
import {
  h, mount, clear, button, textarea, toast, busy, empty, skeleton, errorState, pageHeader, segmented,
  relTime, confirmDialog,
} from "../ui.js";

const STATE = {
  INVITATION: ["Приглашение", "success"],
  DISCARD: ["Отказ", "danger"],
  RESPONSE: ["Отклик", ""],
  INTERVIEW: ["Собеседование", "success"],
  OFFER: ["Предложение", "success"],
  HIRED: ["Выход на работу", "success"],
};

function when(s) { const t = Date.parse(s); return isNaN(t) ? "" : relTime(t / 1000); }

function bubble(m) {
  return h("div", { class: `bubble ${m.mine ? "is-mine" : ""}` },
    !m.mine ? h("div", { class: "bubble-author" }, m.author || "Работодатель", m.is_bot ? h("span", { class: "badge plain" }, "бот") : null) : null,
    h("div", { class: "bubble-text" }, m.text || "Без текста"),
    h("div", { class: "bubble-time" }, when(m.at)));
}

export default async function chats(root, ctx) {
  let filter = ctx.params.get("unread") === "1" ? "unread" : "all";
  let list = [];
  let next = null;
  let selected = Number(ctx.params.get("id")) || null;
  let timer = null;

  const listEl = h("div", { class: "card chat-list", role: "list", "aria-label": "Чаты" });
  const pane = h("section", { class: "card chat-pane", "aria-live": "off" });

  mount(root, pageHeader("Чаты", "Переписка с работодателями. AI подготовит черновик ответа, отправляете вы."), skeleton(6));

  async function load(more = false) {
    const q = new URLSearchParams();
    if (filter === "unread") q.set("unread", "1");
    if (more && next) q.set("from", next);
    const data = await get(`chats?${q}`);
    list = more ? [...list, ...data.items] : data.items;
    next = data.next;
    return data;
  }

  function renderList() {
    if (!list.length) {
      mount(listEl, empty({ iconName: "chat", title: filter === "unread" ? "Непрочитанных нет" : "Чатов пока нет", text: filter === "unread" ? "Все сообщения прочитаны." : "Чаты появятся, когда работодатели ответят на отклики." }));
      return;
    }
    const rows = list.map((c) => {
      const [stateLabel, kind] = STATE[c.state] || [null, ""];
      return h("button", { type: "button", role: "listitem", class: "chat-row", "aria-current": String(c.id === selected), onclick: () => open(c.id) },
        h("div", { class: "row-between" },
          h("span", { class: `chat-title truncate${c.unread ? " is-unread" : ""}` }, c.title),
          h("span", { class: "subtle nowrap" }, when(c.last_at))),
        h("div", { class: "subtle truncate" }, c.subtitle || ""),
        h("div", { class: "row-between" },
          h("span", { class: "chat-last truncate" }, c.last_is_mine ? "Вы: " : "", c.last_text || "Без текста"),
          h("span", { class: "row" }, stateLabel ? h("span", { class: `badge plain ${kind ? "badge-" + kind : ""}` }, stateLabel) : null,
            c.unread ? h("span", { class: "count", "aria-label": `непрочитано: ${c.unread}` }, String(c.unread)) : null)));
    });
    const more = next ? button("Показать ещё", { variant: "ghost", onClick: (e) => busy(e.currentTarget, async () => { try { await load(true); renderList(); } catch (err) { toast(err.message, "error"); } }) }) : null;
    mount(listEl, ...rows, more ? h("div", { class: "chat-more" }, more) : null);
  }

  async function open(id) {
    selected = id;
    pane.classList.remove("is-empty");
    history.replaceState(null, "", `#/chats?id=${id}${filter === "unread" ? "&unread=1" : ""}`);
    renderList();
    mount(pane, h("div", { class: "card-body" }, skeleton(6)));
    let data;
    try { data = await get(`chats/${id}`); }
    catch (err) { mount(pane, errorState(err, () => open(id))); return; }
    const c = list.find((x) => x.id === id) || {};
    if (c.unread) { c.unread = 0; renderList(); }

    const thread = h("div", { class: "chat-thread", tabindex: "0", "aria-label": "Сообщения" },
      data.messages.length ? data.messages.map(bubble) : h("p", { class: "muted" }, "Сообщений нет."));

    const composer = textarea({ rows: 3, placeholder: data.writable ? "Сообщение" : "В этот чат писать нельзя", disabled: !data.writable, "aria-label": "Сообщение" });
    const send = button("Отправить", { variant: "primary", iconName: "send", disabled: !data.writable });
    const draft = button("Черновик от AI", { iconName: "sparkle", disabled: !data.writable || !c.can_ai });
    const leave = button("Покинуть чат", { variant: "ghost", iconName: "logout" });
    const fill = (text) => { composer.value = text; composer.focus(); composer.setSelectionRange(text.length, text.length); };

    send.addEventListener("click", () => busy(send, async () => {
      if (!composer.value.trim()) { composer.focus(); return; }
      const text = composer.value;
      try { await post(`chats/${id}/send`, { text }); }
      catch (err) { toast(err.message, "error"); return; }
      // Без перезагрузки: дописываем пузырь и обновляем строку в списке
      composer.value = "";
      if (!data.messages.length) clear(thread);
      thread.appendChild(bubble({ mine: true, text, at: new Date().toISOString() }));
      thread.scrollTop = thread.scrollHeight;
      Object.assign(c, { last_text: text, last_is_mine: true, last_at: new Date().toISOString() });
      data.messages.push({ mine: true, text });
      list = [c, ...list.filter((x) => x.id !== id)];
      renderList();
      composer.focus();
    }));
    composer.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send.click(); });
    draft.addEventListener("click", () => busy(draft, async () => {
      try { const r = await post(`chats/${id}/draft`); fill(r.text); toast("Черновик готов. Проверьте и отправьте", "info"); }
      catch (err) { toast(err.message, "error"); }
    }));
    leave.addEventListener("click", async () => {
      if (!(await confirmDialog({ title: "Покинуть чат?", text: "Чат пропадёт из списка, и работодатель больше не сможет написать в него. Вернуть нельзя.", confirm: "Покинуть", danger: true }))) return;
      try { await post(`chats/${id}/leave`); toast("Вы покинули чат"); selected = null; await load(); renderList(); mount(pane, placeholder()); }
      catch (err) { toast(err.message, "error"); }
    });

    const suggestions = [...(data.options || []), ...(data.quick_replies || [])];
    mount(pane,
      h("div", { class: "card-header" },
        h("div", { class: "grow" }, h("h2", { class: "card-title truncate" }, c.title || "Чат"),
          h("p", { class: "card-hint truncate" }, c.vacancy_url ? h("a", { href: c.vacancy_url, target: "_blank", rel: "noopener noreferrer" }, c.vacancy || "Вакансия") : (c.vacancy || ""), c.subtitle ? ` · ${c.subtitle}` : "")),
        leave),
      thread,
      h("div", { class: "chat-composer" },
        suggestions.length ? h("div", { class: "chips", "aria-label": data.options && data.options.length ? "Варианты ответа" : "Быстрые ответы hh" },
          suggestions.map((s) => h("button", { type: "button", class: "chip", onclick: () => fill(s) }, s))) : null,
        !c.can_ai && data.writable ? h("p", { class: "subtle" }, "Для черновика от AI обновите список чатов.") : null,
        composer,
        h("div", { class: "row-between" }, draft, send)));
    thread.scrollTop = thread.scrollHeight;
    // На телефоне список и переписка друг под другом: показываем переписку
    if (matchMedia("(max-width: 1080px)").matches) pane.scrollIntoView({ block: "start" });
  }

  const placeholder = () => {
    pane.classList.add("is-empty");
    return h("div", { class: "card-body" }, empty({ iconName: "chat", title: "Выберите чат", text: "Слева переписки, новые сверху." }));
  };

  async function refresh() {
    try { await load(); renderList(); } catch { /* тихо, при следующем опросе */ }
  }

  try { await load(); }
  catch (err) {
    if (/войдите/i.test(err.message)) {
      mount(root, pageHeader("Чаты"), h("div", { class: "card" }, empty({ iconName: "user", title: "Нужно войти в hh.ru", action: button("Войти", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") }) })));
      return;
    }
    mount(root, pageHeader("Чаты"), errorState(err, () => chats(root, ctx)));
    return;
  }

  const filterCtl = segmented([["all", "Все"], ["unread", "Непрочитанные"]], filter, async (f) => { filter = f; mount(listEl, skeleton(5)); try { await load(); } catch (err) { toast(err.message, "error"); } renderList(); }, "Фильтр чатов");
  mount(root,
    pageHeader("Чаты", "AI готовит черновик, отправляете вы. Автоответчик ждёт 15 минут после сообщения работодателя, так что успеете ответить сами.", filterCtl,
      button("", { variant: "ghost", iconName: "refresh", title: "Обновить", onClick: (e) => busy(e.currentTarget, refresh) })),
    h("div", { class: "chats-layout" }, listEl, pane));
  renderList();
  if (selected) open(selected); else mount(pane, placeholder());
  timer = setInterval(refresh, 60000);
  return () => clearInterval(timer);
}
