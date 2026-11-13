import { get, post } from "../api.js";
import {
  h, mount, clear, button, textarea, toast, busy, empty, skeleton, errorState, pageHeader, segmented,
  relTime, confirmDialog, select, callout, input, dateTime, autoMore,
} from "../ui.js";

const STATE = {
  INVITATION: ["Приглашение", "success"],
  DISCARD: ["Отказ", "danger"],
  RESPONSE: ["Отклик", ""],
  INTERVIEW: ["Собеседование", "success"],
  OFFER: ["Предложение", "success"],
  HIRED: ["Выход на работу", "success"],
};

// Кто пишет со стороны работодателя; «неясно» без метки, чтобы не шуметь
const KIND = { human: ["Человек", "badge-success"], bot: ["Бот", ""] };

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
  // Фильтры поверх загруженного списка: без лишних запросов к hh
  const view = { q: "", state: "", sort: "new" };
  const STATE_FILTERS = [
    ["", "Все статусы"],
    ["invite", "Приглашения и собеседования"],
    ["RESPONSE", "Отклики без ответа"],
    ["DISCARD", "Отказы"],
    ["people", "Живые люди"],
    ["theirs", "Ждут меня (последнее от работодателя)"],
    ["mine", "Мой ответ последний"],
  ];
  const INVITE = new Set(["INVITATION", "INTERVIEW", "OFFER", "HIRED"]);
  const matches = (c) => {
    if (view.state === "invite" && !INVITE.has(c.state)) return false;
    if (view.state === "theirs" && c.last_is_mine) return false;
    if (view.state === "people" && c.kind !== "human") return false;
    if (view.state === "mine" && !c.last_is_mine) return false;
    if (["RESPONSE", "DISCARD"].includes(view.state) && c.state !== view.state) return false;
    if (!view.q) return true;
    return `${c.title || ""} ${c.subtitle || ""} ${c.vacancy || ""} ${c.last_text || ""}`.toLowerCase().includes(view.q);
  };
  const searchEl = input({ type: "search", placeholder: "Поиск: компания, вакансия, текст", "aria-label": "Поиск по чатам", oninput: (e) => { view.q = e.target.value.trim().toLowerCase(); renderList(); } });
  const sortEl = select([["new", "Сначала новые"], ["score", "Сначала важные"]], { "aria-label": "Порядок чатов", title: "Важные: приглашения от людей, потом люди, которые ждут ответа, боты в конце", onchange: (e) => { view.sort = e.target.value; renderList(); } });
  const stateEl = select(STATE_FILTERS, { "aria-label": "Статус чата", onchange: (e) => { view.state = e.target.value; renderList(); } });
  const filtersEl = h("div", { class: "chat-filters" });
  const pane = h("section", { class: "card chat-pane", "aria-live": "off" });

  mount(root, pageHeader("Чаты", "Переписка с работодателями. AI подготовит черновик ответа, отправляете вы."), skeleton(6));

  async function load(more = false) {
    const q = new URLSearchParams();
    if (filter === "unread") q.set("unread", "1");
    if (more && next) q.set("from", next);
    const data = await get(`chats?${q}`);
    list = more ? [...list, ...data.items] : data.items;
    next = data.next;
    if (!more) { try { sessionStorage.setItem(cacheKey(), JSON.stringify({ items: list, next })); } catch { /* без кэша */ } }
    return data;
  }

  // Список из прошлого открытия в этой вкладке: показываем сразу, свежий догружаем
  const cacheKey = () => `hh-chats:${filter}`;
  function readCache() {
    try {
      const c = JSON.parse(sessionStorage.getItem(cacheKey()) || "null");
      if (c && Array.isArray(c.items)) { list = c.items; next = c.next; return true; }
    } catch { /* нет кэша */ }
    return false;
  }

  function renderList() {
    const shown = list.filter(matches);
    if (view.sort === "score") shown.sort((a, b) => (b.score || 0) - (a.score || 0) || Date.parse(b.last_at || 0) - Date.parse(a.last_at || 0));
    if (list.length && !shown.length) {
      mount(listEl, empty({ iconName: "search", title: "Ничего не нашлось", text: next ? "Поиск идёт по загруженным чатам. Загрузите ещё или поменяйте фильтр." : "Поменяйте фильтр или запрос." }),
        next ? h("div", { class: "chat-more" }, autoMore(button("Показать ещё", { variant: "ghost", onClick: (e) => busy(e.currentTarget, async () => { try { await load(true); renderList(); } catch (err) { toast(err.message, "error"); } }) }))) : null);
      return;
    }
    if (!list.length) {
      mount(listEl, empty({ iconName: "chat", title: filter === "unread" ? "Непрочитанных нет" : "Чатов пока нет", text: filter === "unread" ? "Все сообщения прочитаны." : "Чаты появятся, когда работодатели ответят на отклики." }));
      return;
    }
    const rows = shown.map((c) => {
      const [stateLabel, kind] = STATE[c.state] || [null, ""];
      return h("button", { type: "button", role: "listitem", class: "chat-row", "aria-current": String(c.id === selected), onclick: () => open(c.id) },
        h("div", { class: "row-between" },
          h("span", { class: `chat-title truncate${c.unread ? " is-unread" : ""}` }, c.title),
          h("span", { class: "subtle nowrap" }, when(c.last_at))),
        h("div", { class: "subtle truncate" }, c.subtitle || ""),
        h("div", { class: "row-between" },
          h("span", { class: "chat-last truncate" }, c.last_is_mine ? "Вы: " : "", c.last_text || "Без текста"),
          h("span", { class: "row" }, KIND[c.kind] ? h("span", { class: `badge plain ${KIND[c.kind][1]}`, title: (c.kind_reasons || []).join(", ") }, KIND[c.kind][0]) : null,
            stateLabel ? h("span", { class: `badge plain ${kind ? "badge-" + kind : ""}` }, stateLabel) : null,
            c.unread ? h("span", { class: "count", "aria-label": `непрочитано: ${c.unread}` }, String(c.unread)) : null)));
    });
    const more = next ? button("Показать ещё", { variant: "ghost", onClick: (e) => busy(e.currentTarget, async () => { try { await load(true); renderList(); } catch (err) { toast(err.message, "error"); } }) }) : null;
    mount(listEl, ...rows, more ? h("div", { class: "chat-more" }, autoMore(more)) : null);
  }

  async function open(id) {
    selected = id;
    pane.classList.remove("is-empty");
    history.replaceState(null, "", `#/chats?id=${id}${filter === "unread" ? "&unread=1" : ""}`);
    renderList();
    mount(pane, h("div", { class: "card-body" }, skeleton(6)));
    // Сначала из кэша (сразу), потом свежая из hh; перерисовка, только если что-то изменилось
    let cachedData = null;
    try { cachedData = await get(`chats/${id}/cached`); } catch { /* нет кэша */ }
    if (selected !== id) return;
    if (cachedData && cachedData.messages) renderPane(id, cachedData);
    let data;
    try { data = await get(`chats/${id}`); }
    catch (err) { if (!cachedData) mount(pane, errorState(err, () => open(id))); else toast(err.message, "error"); return; }
    if (selected !== id) return;
    if (cachedData && cachedData.messages && cachedData.last_message_id === data.last_message_id && cachedData.writable === data.writable) return;
    const typed = pane.querySelector(".chat-composer textarea")?.value || "";
    renderPane(id, data);
    if (typed) { const t = pane.querySelector(".chat-composer textarea"); if (t && !t.disabled) t.value = typed; }
  }

  function renderPane(id, data) {
    const c = list.find((x) => x.id === id) || {};
    if (c.unread) { c.unread = 0; renderList(); }

    const thread = h("div", { class: "chat-thread", role: "log", "aria-label": "Сообщения" },
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

    const formRe = /https?:\/\/(forms\.gle\/\S+|docs\.google\.com\/forms\/\S+)/i;
    const hasForm = data.writable && [...data.messages].reverse().some((m) => !m.mine && formRe.test(m.text || ""));
    const formBtn = hasForm ? button("Заполнить анкету", { iconName: "sparkle" }) : null;
    if (formBtn) formBtn.addEventListener("click", () => busy(formBtn, async () => {
      let draftData;
      try { draftData = await post(`chats/${id}/form`); }
      catch (err) { toast(err.message, "error"); return; }
      openForm(draftData);
    }));

    function openForm(f) {
      const controls = f.questions.map((q) => {
        let input;
        if ((q.type === 2 || q.type === 3) && q.options.length) {
          input = select([["", "— не отвечать —"], ...q.options.map((o) => [o, o])], { value: q.value || "" });
        } else if (q.type === 4 && q.options.length) {
          const picked = Array.isArray(q.value) ? q.value : [];
          const boxes = q.options.map((o) => {
            const cb = h("input", { type: "checkbox", value: o, checked: picked.includes(o) });
            return h("label", { class: "check" }, cb, h("span", null, o));
          });
          input = h("div", { class: "form-boxes", dataset: { group: "1" } }, ...boxes);
        } else {
          input = textarea({ rows: 2, value: q.value || "" });
        }
        return h("div", { class: "form-q" },
          h("div", { class: "field-label" }, q.title, q.required ? h("span", { class: "req" }, " *") : null),
          input);
      });
      const submit = button("Отправить анкету", { variant: "primary", iconName: "send" });
      const cancel = button("Отмена", { variant: "ghost", onClick: () => open(id) });
      submit.addEventListener("click", () => busy(submit, async () => {
        const answers = f.questions.map((q, i) => {
          const el = controls[i].lastChild;
          let value;
          if (el.dataset && el.dataset.group) value = [...el.querySelectorAll("input:checked")].map((c) => c.value);
          else value = el.value;
          return { entry_id: q.entry_id, value };
        });
        try { await post(`chats/${id}/form/submit`, { answers }); toast("Анкета отправлена"); await load(); renderList(); open(id); }
        catch (err) { toast(err.message, "error"); }
      }));
      mount(pane,
        h("div", { class: "card-header" },
          h("div", { class: "grow" }, h("h2", { class: "card-title truncate" }, "Анкета: " + (f.title || "Google Forms")),
            h("p", { class: "card-hint" }, "Ответы заполнены моделью. Проверьте и отправьте, отправка необратима.")),
          cancel),
        f.sent_at ? callout("warning", `Эту анкету уже отправляли ${dateTime(Date.parse(f.sent_at) / 1000)}. Повторная отправка придёт работодателю ещё раз.`) : null,
        f.needs_login ? callout("warning", "Форма может требовать вход в Google. Если не отправится, заполните вручную: ", h("a", { href: f.url, target: "_blank", rel: "noopener noreferrer" }, "открыть анкету")) : null,
        h("div", { class: "card-body stack form-editor" }, ...controls),
        h("div", { class: "card-body row-between" }, cancel, submit));
      pane.scrollTop = 0;
    }

    const chipsEl = h("div", { class: "chips", "aria-label": data.options && data.options.length ? "Варианты ответа" : "Быстрые ответы hh" });
    const addChips = (items) => { for (const s of items) chipsEl.appendChild(h("button", { type: "button", class: "chip", onclick: () => fill(s) }, s)); chipsEl.hidden = !chipsEl.children.length; };
    addChips([...(data.options || []), ...(data.quick_replies || [])]);
    // Быстрые ответы hh приходят отдельным запросом, чтобы не задерживать переписку
    if (!data.quick_replies && data.last_message_id && data.writable) {
      get(`chats/${id}/quick/${data.last_message_id}`).then((r) => { if (selected === id) addChips(r.items || []); }).catch(() => {});
    }
    mount(pane,
      h("div", { class: "card-header" },
        h("div", { class: "grow" }, h("h2", { class: "card-title truncate" }, c.title || "Чат"),
          h("p", { class: "card-hint truncate" }, c.vacancy_url ? h("a", { href: c.vacancy_url, target: "_blank", rel: "noopener noreferrer" }, c.vacancy || "Вакансия") : (c.vacancy || ""), c.subtitle ? `, ${c.subtitle}` : "")),
        leave),
      thread,
      h("div", { class: "chat-composer" },
        chipsEl,
        !c.can_ai && data.writable ? h("p", { class: "subtle" }, "Для черновика от AI обновите список чатов.") : null,
        composer,
        h("div", { class: "row-between" }, h("span", { class: "row" }, draft, formBtn), send)));
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

  let cached = readCache();
  if (!cached) {
    // Кэш на сервере: список с прошлого раза, пока hh отвечает
    try {
      const c = await get(`chats/cached${filter === "unread" ? "?unread=1" : ""}`);
      if (c.items && c.items.length) { list = c.items; next = null; cached = true; }
    } catch { /* без кэша */ }
  }
  if (!cached) try { await load(); }
  catch (err) {
    if (/войдите/i.test(err.message)) {
      mount(root, pageHeader("Чаты"), h("div", { class: "card" }, empty({ iconName: "user", title: "Нужно войти в hh.ru", action: button("Войти", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") }) })));
      return;
    }
    mount(root, pageHeader("Чаты"), errorState(err, () => chats(root, ctx)));
    return;
  }

  const filterCtl = segmented([["all", "Все"], ["unread", "Непрочитанные"]], filter, async (f) => { filter = f; if (readCache()) renderList(); else mount(listEl, skeleton(5)); try { await load(); } catch (err) { toast(err.message, "error"); } renderList(); }, "Фильтр чатов");
  mount(root,
    pageHeader("Чаты", "AI готовит черновик, отправляете вы. Автоответчик ждёт 15 минут после сообщения работодателя, так что успеете ответить сами.",
      button("", { variant: "ghost", iconName: "refresh", title: "Обновить", onClick: (e) => busy(e.currentTarget, refresh) })),
    h("div", { class: "chats-layout" }, h("div", { class: "chat-side" }, mount(filtersEl, filterCtl, searchEl, stateEl, sortEl), listEl), pane));
  renderList();
  if (selected) open(selected); else mount(pane, placeholder());
  if (cached) refresh();
  timer = setInterval(refresh, 60000);
  return () => clearInterval(timer);
}
