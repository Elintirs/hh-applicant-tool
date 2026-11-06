import { get, post } from "../api.js";
import {
  h, mount, button, field, input, select, textarea, toast, busy, empty, skeleton, errorState,
  pageHeader, segmented, callout, relTime, icon, plural,
} from "../ui.js";
import { argsFor, control } from "./run.js";

const STORE = "hh-panel-vacancy-filters";
const load = () => { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(STORE, JSON.stringify(v)); } catch { /* ничего */ } };

function chev() { const c = icon("chevron", 16); c.classList.add("chev"); return c; }

function money(s) {
  if (!s) return null;
  const cur = { RUR: "₽", USD: "$", EUR: "€", KZT: "₸" }[s.currency] || s.currency || "";
  const f = (n) => n.toLocaleString("ru-RU");
  if (s.from && s.to) return `${f(s.from)}–${f(s.to)} ${cur}`;
  if (s.from) return `от ${f(s.from)} ${cur}`;
  if (s.to) return `до ${f(s.to)} ${cur}`;
  return null;
}

function needLogin(ctx) {
  return h("div", { class: "card" }, empty({ iconName: "user", title: "Нужно войти в hh.ru", text: "Поиск идёт от имени вашего аккаунта и по вашему резюме.",
    action: button("Войти", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") }) }));
}

// ---- Подбор ----
async function searchTab(host, ctx) {
  mount(host, skeleton(6));
  let schema, resumes;
  try {
    [schema, resumes] = await Promise.all([get("schema"), get("resumes").then((r) => r.items)]);
  } catch (err) {
    mount(host, /войдите/i.test(err.message) ? needLogin(ctx) : errorState(err, () => searchTab(host, ctx)));
    return;
  }
  const published = resumes.filter((r) => r.status_id === "published");
  const op = schema.operations.find((o) => o.name === "apply-vacancies");
  const all = op.groups.flatMap((g) => g.args);
  const searchGroup = op.groups.find((g) => /поиск/i.test(g.title || ""));
  const extra = (searchGroup ? searchGroup.args : []).filter((a) => !["text", "per_page"].includes(a.dest));
  const filterDefs = ["excluded_filter", "skip_tests"].map((d) => all.find((a) => a.dest === d)).filter(Boolean);
  const state = load();
  state.values ||= {};
  state.resume_id = published.some((r) => r.id === state.resume_id) ? state.resume_id : (published[0] && published[0].id);

  let results = null, page = 0;
  const resultsHost = h("div", { class: "stack" });
  const searchInput = input({ type: "search", value: state.values.text || "", placeholder: "Например, Python разработчик. Пусто: похожие на резюме", "aria-label": "Что ищем" });
  searchInput.addEventListener("input", () => { state.values.text = searchInput.value || undefined; });
  const resumeSel = select(published.map((r) => [r.id, r.title]), { value: state.resume_id, "aria-label": "Резюме", onchange: (e) => { state.resume_id = e.target.value; } });
  const setter = (dest) => (v) => { if (v === undefined) delete state.values[dest]; else state.values[dest] = v; };
  const lookups = {};
  const activeExtra = () => extra.filter((a) => state.values[a.dest] !== undefined).length;

  const args = () => argsFor([...all.filter((a) => a.dest === "text"), ...extra, ...filterDefs], state.values);
  const go = (p = 0) => {
    page = p;
    save(state);
    run();
  };

  async function run() {
    if (!state.resume_id) return;
    mount(resultsHost, skeleton(6));
    try {
      results = await post("vacancies/search", { args: args().opts, resume_id: state.resume_id, page });
    } catch (err) { mount(resultsHost, errorState(err, run)); return; }
    renderResults();
  }

  function vacancyCard(v) {
    const details = h("div", { class: "vacancy-details", hidden: true });
    const checkBtn = button("Проверить и откликнуться", { size: "sm", variant: v.skip_reason ? "" : "primary", iconName: "sparkle", disabled: !!v.skip_reason });
    const hideBtn = button("Скрыть", { size: "sm", variant: "ghost", iconName: "ban" });
    const hideEmpBtn = v.employer_id ? button("Скрыть компанию", { size: "sm", variant: "ghost" }) : null;
    const card = h("article", { class: "card vacancy" },
      h("div", { class: "card-body stack-sm" },
        h("div", { class: "row-between" },
          h("div", { class: "grow" },
            h("h3", null, h("a", { href: v.url, target: "_blank", rel: "noopener noreferrer" }, v.name)),
            h("div", { class: "subtle" }, [v.employer, v.area, v.experience, v.published_at ? relTime(Date.parse(v.published_at) / 1000) : null].filter(Boolean).join(" · "))),
          money(v.salary) ? h("div", { class: "vacancy-salary nowrap" }, money(v.salary)) : null),
        v.requirement || v.responsibility ? h("p", { class: "muted vacancy-snippet" }, [v.responsibility, v.requirement].filter(Boolean).join(" ")) : null,
        h("div", { class: "row-between" },
          h("div", { class: "row" },
            v.skip_reason ? h("span", { class: "badge badge-warning plain" }, v.skip_reason) : null,
            v.has_test ? h("span", { class: "badge plain" }, "Тест") : null,
            v.response_letter_required ? h("span", { class: "badge badge-info plain" }, "Нужно письмо") : null),
          h("div", { class: "row" }, hideEmpBtn, hideBtn, checkBtn)),
        details));

    const hide = async (kind, id, label) => {
      try {
        await post("blacklist", { kind, id, on: true });
        card.classList.add("is-hidden");
        mount(card, h("div", { class: "card-body row-between" }, h("span", { class: "muted" }, `${label} скрыта`),
          button("Вернуть", { size: "sm", variant: "ghost", iconName: "undo", onClick: async () => {
            try { await post("blacklist", { kind, id, on: false }); card.replaceWith(vacancyCard(v)); } catch (err) { toast(err.message, "error"); }
          } })));
      } catch (err) { toast(err.message, "error"); }
    };
    hideBtn.addEventListener("click", () => hide("vacancies", v.id, "Вакансия"));
    if (hideEmpBtn) hideEmpBtn.addEventListener("click", () => hide("employers", v.employer_id, `Компания «${v.employer}»`));

    checkBtn.addEventListener("click", () => busy(checkBtn, async () => {
      details.hidden = false;
      mount(details, h("div", { class: "result subtle" }, "Проверяем фильтрами и готовим письмо. С AI это до минуты…"));
      let r;
      try { r = await post("vacancies/check", { args: args().opts, resume_id: state.resume_id, vacancy_id: v.id }); }
      catch (err) { mount(details, callout("danger", h("p", null, err.message))); return; }
      const letter = textarea({ rows: 7, value: r.letter, "aria-label": "Сопроводительное письмо" });
      const apply = button("Откликнуться", { variant: "primary", iconName: "send" });
      apply.addEventListener("click", () => busy(apply, async () => {
        try {
          await post("vacancies/apply", { resume_id: state.resume_id, vacancy_id: v.id, letter: letter.value });
          mount(card, h("div", { class: "card-body row" }, icon("check", 18), h("span", null, "Отклик отправлен: "), h("a", { href: v.url, target: "_blank", rel: "noopener noreferrer" }, v.name)));
          card.classList.add("is-done");
          toast("Отклик отправлен");
        } catch (err) { toast(err.message, "error"); }
      }));
      const verdict = r.excluded
        ? callout("warning", h("p", null, "Вакансия попала под фильтр исключений (--excluded-filter)."))
        : r.suitable === false
          ? callout("warning", h("p", null, "AI-фильтр считает, что вакансия не подходит. Откликнуться всё равно можно."))
          : r.suitable === true ? callout("success", h("p", null, "AI-фильтр: подходит.")) : null;
      const source = { template: `Письмо из шаблона «${r.template}»`, ai: "Письмо написал AI", default: "Общее письмо" }[r.letter_source];
      mount(details,
        verdict,
        r.key_skills.length ? h("div", { class: "chips" }, r.key_skills.map((s) => h("span", { class: "chip" }, s))) : null,
        h("details", { class: "disclosure" }, h("summary", null, chev(), "Описание вакансии"), h("p", { class: "muted pre-line" }, r.description)),
        field({ label: source, control: letter, help: "Можно поправить перед отправкой." }),
        h("div", { class: "row-between" }, button("Закрыть", { variant: "ghost", onClick: () => { details.hidden = true; } }), apply));
      letter.focus();
    }));
    return card;
  }

  function renderResults() {
    if (!results) { mount(resultsHost); return; }
    if (!results.items.length) {
      mount(resultsHost, h("div", { class: "card" }, empty({ iconName: "search", title: "Ничего не нашлось", text: "Попробуйте другие слова или уберите часть фильтров." })));
      return;
    }
    const open = results.items.filter((v) => !v.skip_reason).length;
    const pager = h("div", { class: "row-between" },
      button("Назад", { variant: "ghost", iconName: "arrowLeft", disabled: page === 0, onClick: () => go(page - 1) }),
      h("span", { class: "subtle" }, `Страница ${page + 1} из ${results.pages || 1}`),
      button("Дальше", { variant: "ghost", disabled: page + 1 >= (results.pages || 1), onClick: () => go(page + 1) }));
    mount(resultsHost,
      h("div", { class: "row-between" },
        h("p", { class: "muted" }, `Найдено ${(results.found || 0).toLocaleString("ru-RU")} ${plural(results.found || 0, "вакансия", "вакансии", "вакансий")}. На этой странице можно откликнуться на ${open}.`),
        button("Откликаться на такие по расписанию", { size: "sm", iconName: "clock", onClick: () => {
          sessionStorage.setItem("hh-panel-schedule-draft", JSON.stringify({ name: state.values.text ? `Отклики: ${state.values.text}` : "Отклики по резюме", args: ["apply-vacancies", ...args().opts, ...(state.resume_id ? [`--resume-id=${state.resume_id}`] : [])], mode: "daily", times: ["10:00"], days: [0, 1, 2, 3, 4] }));
          ctx.navigate("schedule", { draft: "1" });
        } })),
      results.items.map(vacancyCard),
      pager);
  }

  const searchBtn = button("Найти", { variant: "primary", iconName: "search", type: "submit" });
  const form = h("form", { class: "card", role: "search" },
    h("div", { class: "card-body stack" },
      h("div", { class: "search-bar" }, searchInput, resumeSel, searchBtn),
      h("details", { class: "disclosure", open: activeExtra() > 0 || undefined },
        h("summary", null, chev(), "Фильтры", activeExtra() ? h("span", { class: "badge badge-accent plain" }, String(activeExtra())) : null),
        h("div", { class: "form-grid" }, [...filterDefs, ...extra].map((d) => control(d, state.values[d.dest], setter(d.dest), lookups))))));
  form.addEventListener("submit", (e) => { e.preventDefault(); go(0); });

  mount(host,
    !published.length ? callout("warning", h("p", null, "Нет опубликованных резюме: откликаться не с чем. Опубликуйте резюме на hh.ru.")) : null,
    form, resultsHost);
  if (state.resume_id && (state.values.text || results)) go(0);
  else mount(resultsHost, h("div", { class: "card" }, empty({ iconName: "search", title: "Найдите вакансии", text: "Без запроса покажем вакансии, похожие на выбранное резюме. Каждую можно проверить AI-фильтром и посмотреть письмо до отклика.",
    action: button("Показать похожие на резюме", { variant: "primary", onClick: () => go(0), disabled: !state.resume_id }) })));
}

// ---- Скрытые ----
async function hiddenTab(host, ctx, kind) {
  let page = 0;
  async function render() {
    mount(host, skeleton(5));
    let data;
    try { data = await get(`blacklist?kind=${kind}&page=${page}`); }
    catch (err) { mount(host, /войдите/i.test(err.message) ? needLogin(ctx) : errorState(err, render)); return; }
    if (!data.items.length) {
      mount(host, h("div", { class: "card" }, empty({ iconName: "ban", title: kind === "vacancies" ? "Скрытых вакансий нет" : "Скрытых компаний нет",
        text: kind === "vacancies" ? "Сюда попадают вакансии, которые вы скрыли или отсеял фильтр." : "Скрытые компании не показываются в поиске и не получают откликов." })));
      return;
    }
    const list = h("ul", { class: "list" }, data.items.map((i) => {
      const li = h("li", { class: "list-item" },
        h("div", { class: "list-item-main" }, h("div", { class: "list-item-title truncate" }, i.url ? h("a", { href: i.url, target: "_blank", rel: "noopener noreferrer" }, i.name) : i.name),
          h("div", { class: "list-item-meta" }, kind === "vacancies" ? (i.employer || "") : (i.vacancies != null ? `открытых вакансий: ${i.vacancies}` : ""))),
        button("Вернуть", { size: "sm", iconName: "undo", onClick: (e) => busy(e.currentTarget, async () => {
          try { await post("blacklist", { kind, id: i.id, on: false }); li.remove(); toast(kind === "vacancies" ? "Вакансия снова в поиске" : "Компания снова в поиске"); }
          catch (err) { toast(err.message, "error"); }
        }) }));
      return li;
    }));
    mount(host,
      h("p", { class: "muted" }, `Всего: ${data.found}`),
      h("div", { class: "card" }, list),
      data.pages > 1 ? h("div", { class: "row-between" },
        button("Назад", { variant: "ghost", disabled: page === 0, onClick: () => { page--; render(); } }),
        h("span", { class: "subtle" }, `${page + 1} из ${data.pages}`),
        button("Дальше", { variant: "ghost", disabled: page + 1 >= data.pages, onClick: () => { page++; render(); } })) : null);
  }
  render();
}

export default async function vacancies(root, ctx) {
  let tab = ctx.params.get("tab") || "search";
  const host = h("div", { class: "stack" });
  const tabs = segmented([["search", "Подбор"], ["vacancies", "Скрытые вакансии"], ["employers", "Скрытые компании"]], tab, (t) => {
    tab = t; history.replaceState(null, "", `#/vacancies?tab=${t}`); show();
  }, "Раздел");
  const show = () => (tab === "search" ? searchTab(host, ctx) : hiddenTab(host, ctx, tab));
  mount(root, pageHeader("Вакансии", "Найдите вакансии и откликайтесь по одной: с проверкой AI-фильтром и письмом, которое можно поправить."), h("div", { class: "stack" }, h("div", null, tabs), host));
  show();
}
