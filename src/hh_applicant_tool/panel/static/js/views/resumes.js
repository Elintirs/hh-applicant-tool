import { get, put, post } from "../api.js";
import {
  h, mount, button, field, input, textarea, select, toast, busy, empty, skeleton, errorState,
  pageHeader, callout, relTime, dateTime, icon, saveBar,
} from "../ui.js";

function chev() { const c = icon("chevron", 16); c.classList.add("chev"); return c; }

function notLoggedIn(ctx) {
  return h("div", { class: "card" }, empty({ iconName: "user", title: "Нужно войти в hh.ru", text: "Резюме загружаются из вашего аккаунта.",
    action: button("Войти", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") }) }));
}

// Поле ключевых навыков: чипы + подсказки hh
function skillsEditor(skills, onChange) {
  const list = [...skills];
  const chips = h("div", { class: "chips" });
  const datalist = h("datalist", { id: "skill-suggest" });
  const inp = input({ list: "skill-suggest", placeholder: "Добавить навык и Enter" });
  const render = () => {
    mount(chips, ...list.map((s, i) => h("span", { class: "chip", "aria-pressed": "true" }, s,
      h("button", { type: "button", class: "x", "aria-label": `Убрать ${s}`, onclick: () => { list.splice(i, 1); render(); onChange(list); } }, "×"))));
  };
  let t = null;
  inp.addEventListener("input", () => {
    clearTimeout(t);
    const q = inp.value.trim();
    if (q.length < 2) return;
    t = setTimeout(async () => {
      try { const r = await get(`suggest/skills?text=${encodeURIComponent(q)}`); mount(datalist, ...r.items.map((x) => h("option", { value: x }))); } catch { /* подсказки необязательны */ }
    }, 250);
  });
  const add = () => {
    const v = inp.value.trim();
    if (v && !list.some((x) => x.toLowerCase() === v.toLowerCase())) { list.push(v); render(); onChange(list); }
    inp.value = "";
  };
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  inp.addEventListener("change", () => { if (datalist.querySelector(`option[value="${CSS.escape(inp.value)}"]`)) add(); });
  render();
  return h("div", { class: "stack-sm" }, chips, inp, datalist);
}

export default async function resumes(root, ctx) {
  mount(root, pageHeader("Резюме", "Поднять в поиске, поправить главное и посмотреть, кто открывал."), skeleton(5));
  let items;
  try { items = (await get("resumes")).items; }
  catch (err) {
    if (/войдите/i.test(err.message)) { mount(root, pageHeader("Резюме"), notLoggedIn(ctx)); return; }
    mount(root, pageHeader("Резюме"), errorState(err, () => resumes(root, ctx))); return;
  }
  let openId = ctx.params.get("id");
  let bar = null;

  function stats(r) {
    const cell = (label, value) => h("div", null, h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value kpi-sm" }, value ?? "–"));
    return h("div", { class: "grid-3 resume-stats" },
      cell("Просмотры", r.total_views), cell("Новые", r.new_views), cell("Приглашения", r.invitations));
  }

  function card(r) {
    const publishBtn = button(r.can_publish_or_update ? "Поднять в поиске" : "Поднять", { size: "sm", variant: r.can_publish_or_update ? "primary" : "", iconName: "up", disabled: !r.can_publish_or_update });
    publishBtn.addEventListener("click", () => busy(publishBtn, async () => {
      try { await post(`resumes/${r.id}/publish`); toast("Резюме поднято"); items = (await get("resumes")).items; render(); }
      catch (err) { toast(err.message, "error"); }
    }));
    const isOpen = openId === r.id;
    return h("article", { class: `card${isOpen ? " is-active" : ""}` },
      h("div", { class: "card-body stack" },
        h("div", { class: "row-between" },
          h("div", { class: "grow" },
            h("div", { class: "row" }, h("h3", null, r.title || "Без названия"),
              h("span", { class: `badge ${r.status_id === "published" ? "badge-success" : ""}` }, r.status || "")),
            h("p", { class: "subtle" }, [r.area, r.updated_at ? `обновлено ${relTime(Date.parse(r.updated_at) / 1000)}` : null].filter(Boolean).join(" · "))),
          h("div", { class: "row" },
            r.url ? h("a", { class: "btn btn-sm btn-ghost", href: r.url, target: "_blank", rel: "noopener noreferrer" }, icon("external", 16), "На hh.ru") : null,
            button(isOpen ? "Свернуть" : "Изменить", { size: "sm", attrs: { "aria-expanded": String(isOpen) }, onClick: () => { openId = isOpen ? null : r.id; render(); } }),
            publishBtn)),
        !r.can_publish_or_update && r.next_publish_at ? h("p", { class: "subtle" }, `Поднять можно ${relTime(Date.parse(r.next_publish_at) / 1000)}, ${dateTime(Date.parse(r.next_publish_at) / 1000)}`) : null,
        stats(r)),
      isOpen ? editor(r) : null);
  }

  function editor(r) {
    const host = h("div", { class: "card-body stack resume-editor" }, skeleton(5));
    (async () => {
      let data;
      try { data = await get(`resumes/${r.id}`); } catch (err) { mount(host, errorState(err)); return; }
      const res = data.resume;
      const draft = {
        title: res.title || "",
        salary: res.salary ? { amount: res.salary.amount, currency: res.salary.currency || "RUR" } : { amount: "", currency: "RUR" },
        skill_set: [...(res.skill_set || [])],
        skills: res.skills || "",
      };
      const changed = () => bar.set(true);
      if (bar) bar.destroy();
      bar = saveBar({
        onSave: async () => {
          try {
            await put(`resumes/${r.id}`, { ...draft, salary: draft.salary.amount ? draft.salary : null });
            toast("Резюме сохранено на hh.ru");
            items = (await get("resumes")).items;
            render();
            return true;
          } catch (err) { toast(err.message, "error"); return false; }
        },
        onReset: () => render(),
      });
      const title = input({ value: draft.title, oninput: (e) => { draft.title = e.target.value; changed(); } });
      const amount = input({ type: "number", min: 0, step: 1000, value: draft.salary.amount || "", placeholder: "Не указана", oninput: (e) => { draft.salary.amount = e.target.value; changed(); } });
      const currency = select([["RUR", "₽"], ["USD", "$"], ["EUR", "€"], ["KZT", "₸"], ["BYR", "Br"]], { value: draft.salary.currency, onchange: (e) => { draft.salary.currency = e.target.value; changed(); } });
      const about = textarea({ rows: 8, value: draft.skills, oninput: (e) => { draft.skills = e.target.value; changed(); } });

      const viewsHost = h("div", null);
      const loadViews = button("Показать", { size: "sm" });
      loadViews.addEventListener("click", () => busy(loadViews, async () => {
        try {
          const v = (await get(`resumes/${r.id}/views`)).items;
          mount(viewsHost, v.length ? h("ul", { class: "list" }, v.map((x) => h("li", { class: "list-item" },
            h("div", { class: "list-item-main" }, x.employer_url ? h("a", { href: x.employer_url, target: "_blank", rel: "noopener noreferrer" }, x.employer || "Работодатель") : (x.employer || "Работодатель")),
            h("span", { class: "subtle nowrap" }, x.created_at ? relTime(Date.parse(x.created_at) / 1000) : ""))))
            : h("p", { class: "muted" }, "Пока никто не смотрел."));
        } catch (err) { toast(err.message, "error"); }
      }));

      const ph = Object.entries(data.placeholders || {}).filter(([, v]) => v);
      mount(host,
        callout("info", h("p", null, "Меняются только эти поля. Опыт, образование и контакты правьте на hh.ru.")),
        h("div", { class: "form-grid" },
          field({ label: "Желаемая должность", control: title, span2: true }),
          h("div", { class: "field" }, h("label", { class: "field-label", for: amount.id || (amount.id = `sal-${r.id}`) }, "Зарплата"), h("div", { class: "input-group" }, amount, currency))),
        h("div", { class: "field" }, h("span", { class: "field-label" }, "Ключевые навыки"), skillsEditor(draft.skill_set, (list) => { draft.skill_set = list; changed(); })),
        field({ label: "О себе", control: about }),
        h("details", { class: "disclosure" }, h("summary", null, chev(), "Кто смотрел резюме"), h("div", { class: "stack-sm" }, loadViews, viewsHost)),
        ph.length ? h("details", { class: "disclosure" }, h("summary", null, chev(), "Плейсхолдеры для писем"),
          h("table", { class: "table" }, h("tbody", null, ph.map(([k, v]) => h("tr", null, h("td", { class: "mono" }, `%(${k})s`), h("td", { class: "muted" }, String(v).slice(0, 160))))))) : null);
      root.appendChild(bar.el);
    })();
    return host;
  }

  function render() {
    if (bar && !openId) { bar.destroy(); bar = null; }
    const canPublish = items.filter((r) => r.can_publish_or_update);
    const publishAll = canPublish.length > 1 ? button(`Поднять все (${canPublish.length})`, { variant: "primary", iconName: "up" }) : null;
    if (publishAll) publishAll.addEventListener("click", () => busy(publishAll, async () => {
      let ok = 0;
      for (const r of canPublish) { try { await post(`resumes/${r.id}/publish`); ok++; } catch { /* покажем итог */ } }
      toast(`Поднято резюме: ${ok} из ${canPublish.length}`, ok === canPublish.length ? "success" : "error");
      items = (await get("resumes")).items; render();
    }));
    mount(root,
      pageHeader("Резюме", "Поднять в поиске, поправить главное и посмотреть, кто открывал.", publishAll,
        button("Поднимать по расписанию", { variant: publishAll ? "" : "primary", iconName: "clock", onClick: () => ctx.navigate("schedule") })),
      items.length ? h("div", { class: "stack" }, items.map(card))
        : h("div", { class: "card" }, empty({ iconName: "file", title: "Резюме нет", text: "Создайте резюме на hh.ru, и оно появится здесь." })));
  }

  render();
  return () => { if (bar) bar.destroy(); };
}
