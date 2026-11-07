import { get, post } from "../api.js";
import {
  h, mount, button, toast, busy, empty, skeleton, errorState, pageHeader, segmented, relTime, icon,
} from "../ui.js";
import stats from "./stats.js";

const KIND = { invitation: "success", interview: "success", hired: "success", offer: "success", discard: "danger", response: "" };
const GROUPS = [
  ["all", "Все"],
  ["response", "Без ответа"],
  ["invitation", "Приглашения"],
  ["discard", "Отказы"],
];
const inGroup = (g, s) => g === "all" || (g === "invitation" ? ["invitation", "interview", "hired", "offer"].includes(s) : s === g);

// Диалог действия: что именно сделать с откликом. Вместо «Вы уверены?»
// перечисляем последствия, и опасные галочки выключены по умолчанию
function hideDialog(n) {
  return new Promise((resolve) => {
    const dlg = h("dialog", { class: "modal", "aria-labelledby": "hide-title" });
    const isDiscard = n.state === "discard";
    const delChat = h("input", { type: "checkbox" });
    const banEmp = h("input", { type: "checkbox", checked: isDiscard });
    const done = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    dlg.append(
      h("div", { class: "modal-body" },
        h("h3", { id: "hide-title" }, isDiscard ? "Убрать отказ из списка" : "Отозвать отклик"),
        h("p", { class: "muted" }, isDiscard ? "Отклик пропадёт из списка на hh.ru." : "Работодатель увидит, что вы отозвали отклик. Вернуть его нельзя."),
        h("label", { class: "check" }, delChat, h("span", null, "Удалить и чат с работодателем")),
        n.employer_id ? h("label", { class: "check" }, banEmp, h("span", null, `Скрыть компанию «${n.employer}» из поиска`)) : null),
      h("div", { class: "modal-actions" },
        button("Отмена", { onClick: () => done(null) }),
        button(isDiscard ? "Убрать" : "Отозвать", { variant: "danger-solid", onClick: () => done({ delete_chat: delChat.checked, ban: banEmp.checked }) })));
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });
    document.body.appendChild(dlg);
    dlg.showModal();
  });
}

async function listTab(host, ctx) {
  let page = 0, group = "all", status = "all", data = null;
  const listHost = h("div", { class: "stack" });

  async function load() {
    mount(listHost, skeleton(6));
    try { data = await get(`negotiations?status=${status}&page=${page}`); }
    catch (err) {
      mount(listHost, /войдите/i.test(err.message)
        ? h("div", { class: "card" }, empty({ iconName: "user", title: "Нужно войти в hh.ru", action: button("Войти", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") }) }))
        : errorState(err, load));
      return;
    }
    render();
  }

  async function hide(n, row) {
    const choice = await hideDialog(n);
    if (!choice) return;
    try {
      await post(`negotiations/${n.id}/hide`, { decline: n.state !== "discard", delete_chat: choice.delete_chat });
      if (choice.ban && n.employer_id) await post("blacklist", { kind: "employers", id: n.employer_id, on: true });
      row.remove();
      toast(n.state === "discard" ? "Отказ убран" : "Отклик отозван");
    } catch (err) { toast(err.message, "error"); }
  }

  function render() {
    const items = data.items.filter((n) => inGroup(group, n.state));
    const discards = data.items.filter((n) => n.state === "discard");
    const clearBtn = discards.length ? button(`Убрать отказы на странице (${discards.length})`, { size: "sm", variant: "ghost", iconName: "trash" }) : null;
    if (clearBtn) clearBtn.addEventListener("click", () => busy(clearBtn, async () => {
      let ok = 0;
      for (const n of discards) { try { await post(`negotiations/${n.id}/hide`, { decline: false, delete_chat: false }); ok++; } catch { /* итог ниже */ } }
      toast(`Убрано отказов: ${ok} из ${discards.length}`, ok === discards.length ? "success" : "error");
      load();
    }));
    const rows = items.map((n) => {
      const li = h("li", { class: "list-item" },
        h("div", { class: "list-item-main" },
          h("div", { class: "list-item-title truncate" }, n.vacancy_url ? h("a", { href: n.vacancy_url, target: "_blank", rel: "noopener noreferrer" }, n.vacancy) : n.vacancy),
          h("div", { class: "list-item-meta truncate" }, [n.employer, n.resume, n.updated_at ? relTime(Date.parse(n.updated_at) / 1000) : null].filter(Boolean).join(" · "))),
        h("div", { class: "list-item-tags" },
          n.has_updates ? h("span", { class: "badge badge-accent" }, "Новое") : null,
          n.viewed ? h("span", { class: "subtle nowrap", title: "Работодатель открыл отклик" }, icon("eye", 14), h("span", { class: "sr-only" }, "Просмотрен")) : null,
          // hh называет статус без ответа «Отклик», а вкладка фильтра «Без ответа»: одно слово везде
          h("span", { class: `badge ${KIND[n.state] ? "badge-" + KIND[n.state] : ""}` }, n.state === "response" ? "Без ответа" : n.state_name)),
        button("", { size: "sm", variant: "ghost", iconName: n.state === "discard" ? "trash" : "x", title: n.state === "discard" ? "Убрать отказ из списка" : "Отозвать отклик" }));
      li.lastChild.addEventListener("click", () => hide(n, li));
      return li;
    });
    mount(listHost,
      h("div", { class: "row-between" },
        segmented(GROUPS, group, (g) => { group = g; render(); }, "Статус"),
        h("div", { class: "row" }, clearBtn,
          segmented([["all", "С архивом"], ["active", "Активные"], ["archived", "Архив"]], status, (s) => { status = s; page = 0; load(); }, "Активность"))),
      items.length ? h("div", { class: "card" }, h("ul", { class: "list" }, rows))
        : h("div", { class: "card" }, empty({ iconName: "inbox", title: "Здесь пусто", text: "На этой странице нет откликов с таким статусом." })),
      data.pages > 1 ? h("div", { class: "row-between" },
        button("Назад", { variant: "ghost", iconName: "arrowLeft", disabled: page === 0, onClick: () => { page--; load(); } }),
        h("span", { class: "subtle" }, `Страница ${page + 1} из ${data.pages} · всего ${data.found}`),
        button("Дальше", { variant: "ghost", disabled: page + 1 >= data.pages, onClick: () => { page++; load(); } })) : null);
  }
  mount(host, listHost);
  load();
}

export default async function negotiations(root, ctx) {
  let tab = ctx.params.get("tab") === "stats" ? "stats" : "list";
  const host = h("div", { class: "stack" });
  const show = () => (tab === "stats" ? stats(host, ctx, { embedded: true }) : listTab(host, ctx));
  mount(root,
    pageHeader("Отклики", "Ответы работодателей и сводка. Отказы можно убрать, а компании скрыть, чтобы не попадались снова.",
      segmented([["list", "Список"], ["stats", "Статистика"]], tab, (t) => { tab = t; history.replaceState(null, "", `#/negotiations?tab=${t}`); show(); }, "Раздел")),
    host);
  show();
}
