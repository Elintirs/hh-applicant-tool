import { get, post } from "../api.js";
import {
  h, mount, button, field, input, textarea, select, switchControl, segmented, toast, busy,
  callout, pageHeader, skeleton, errorState, icon, copyText,
} from "../ui.js";

const STORAGE_KEY = "hh-panel-run-values";

function loadSaved() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { return {}; }
}
function saveValues(all) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(all)); } catch { /* ничего */ }
}

// Кавычки как в POSIX-шелле: для предпросмотра и копирования
export function shellQuote(s) {
  if (s === "") return "''";
  if (/^[A-Za-z0-9_\-+=.,/:@%]+$/.test(s)) return s;
  return `'${s.replace(/'/g, `'"'"'`)}'`;
}

function splitMulti(v) {
  return String(v || "").split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
}

// Превращает значения формы в аргументы CLI
export function argsFor(defs, values) {
  const opts = [], positional = [];
  for (const a of defs) {
    const v = values[a.dest];
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    switch (a.kind) {
      case "flag":
      case "flag_false":
        if (v === true) opts.push(a.flag);
        break;
      case "bool":
        if (v === "yes") opts.push(a.flag);
        else if (v === "no") opts.push(a.neg_flag);
        break;
      case "count": {
        const n = Math.max(0, Math.min(5, parseInt(v, 10) || 0));
        for (let i = 0; i < n; i++) opts.push(a.flag);
        break;
      }
      case "append":
        for (const x of splitMulti(v)) opts.push(a.flag, x);
        break;
      case "positional":
        if (a.multiple) positional.push(...(Array.isArray(v) ? v : splitMulti(v)));
        else positional.push(String(v));
        break;
      default:
        if (a.multiple) {
          const list = Array.isArray(v) ? v : splitMulti(v);
          if (list.length) opts.push(a.flag, ...list);
        } else {
          // Через «=», чтобы значения с минусом в начале не путались с флагами
          opts.push(`${a.flag}=${v}`);
        }
    }
  }
  return { opts, positional };
}

export function buildArgs(schema, op, values, globalValues) {
  const g = argsFor(schema.global, globalValues || {});
  const defs = op.groups.flatMap((gr) => gr.args);
  const o = argsFor(defs, values || {});
  return [...g.opts, op.name, ...o.opts, ...o.positional];
}

function chev() {
  const c = icon("chevron", 16);
  c.classList.add("chev");
  return c;
}

function isDry(vals) {
  return vals.dry_run === true || vals.dry_run === "yes";
}

// Один контрол для аргумента. lookups: списки резюме и шаблонов писем
// Коды API в вариантах argparse показываем по-человечески
const CHOICE_LABELS = {
  REMOTE: "Удалённо", HYBRID: "Гибрид", ON_SITE: "В офисе", FIELD_WORK: "Разъездная",
  FULL: "Полная", PART: "Частичная", PROJECT: "Проектная", VOLUNTEER: "Волонтёрство", PROBATION: "Стажировка",
  noExperience: "Без опыта", between1And3: "1–3 года", between3And6: "3–6 лет", moreThan6: "Больше 6 лет",
  fullDay: "Полный день", shift: "Сменный", flexible: "Гибкий", remote: "Удалённо", flyInFlyOut: "Вахта",
  publication_time: "По дате", salary_desc: "Зарплата по убыванию", salary_asc: "Зарплата по возрастанию", relevance: "По соответствию", distance: "По удалённости",
  RUR: "₽ рубли", USD: "$ доллары", EUR: "€ евро",
};
const choiceLabel = (c) => CHOICE_LABELS[c] || c;

export function control(def, value, onChange, lookups) {
  const label = def.help ? def.help.split(/[.(]/)[0].trim() : def.dest;
  const shortLabel = label.length > 90 ? label.slice(0, 87) + "…" : label;
  // Подсказка не повторяет название поля: показываем только то, что после первой фразы
  let help;
  if (def.help && shortLabel !== label) help = def.help;
  else if (def.help) {
    const rest = def.help.slice(label.length).replace(/^[\s.]+/, "").trim();
    help = rest.length > 1 ? rest.charAt(0).toUpperCase() + rest.slice(1) : undefined;
  }
  const fieldOpts = { label: shortLabel, flag: def.flag || def.dest, help };

  if (def.kind === "flag" || def.kind === "flag_false" || def.kind === "bool") {
    // Переключатель показывает итоговое значение: заданное явно или
    // значение по умолчанию. В команду попадает только отличие от умолчания
    const defOn = def.kind === "bool" && def.default === true;
    const on = def.kind === "bool" ? (value === "yes" || (value === undefined && defOn)) : value === true;
    const sw = switchControl(shortLabel, { checked: on, onChange: (c) => {
      if (def.kind !== "bool") onChange(c || undefined);
      else onChange(c === defOn ? undefined : (c ? "yes" : "no"));
    } });
    return h("div", { class: "field switch-field" }, sw,
      h("div", { class: "field-help" }, h("span", { class: "flag" }, def.flag), help ? ` · ${help}` : ""));
  }
  if (def.kind === "count") {
    const sel = select([["0", "Обычный"], ["1", "Подробный"], ["2", "Отладка"], ["3", "Максимум"]], { value: String(value || 0), onchange: (e) => onChange(e.target.value === "0" ? undefined : e.target.value) });
    return field({ ...fieldOpts, label: "Подробность лога", control: sel });
  }
  if (def.dest === "resume_id" && lookups.resumes && lookups.resumes.length) {
    const sel = select([["", "Все / по умолчанию"], ...lookups.resumes.map((r) => [r.id, r.title || r.id])], { value: value || "", onchange: (e) => onChange(e.target.value || undefined) });
    return field({ ...fieldOpts, label: "Резюме", control: sel });
  }
  if (def.dest === "letter_template" && lookups.templates) {
    const sel = select([["", "Автоподбор по ключевым словам"], ...lookups.templates.map((t) => [t.name, t.name])], { value: value || "", onchange: (e) => onChange(e.target.value || undefined) });
    return field({ ...fieldOpts, label: "Шаблон письма", control: sel, help: "Работает, когда AI для писем выключен или шаблон выбран явно." });
  }
  if (def.choices && !def.multiple) {
    const sel = select([["", "Не задано"], ...def.choices.map((c) => [c, choiceLabel(c)])], { value: value || "", onchange: (e) => onChange(e.target.value || undefined) });
    return field({ ...fieldOpts, control: sel });
  }
  if (def.choices && def.multiple) {
    const chosen = new Set(Array.isArray(value) ? value : []);
    const chips = h("div", { class: "chips", role: "group", "aria-label": shortLabel });
    for (const c of def.choices) {
      const b = h("button", { type: "button", class: "chip", "aria-pressed": String(chosen.has(c)), title: c }, choiceLabel(c));
      b.addEventListener("click", () => {
        chosen.has(c) ? chosen.delete(c) : chosen.add(c);
        b.setAttribute("aria-pressed", String(chosen.has(c)));
        onChange(chosen.size ? [...chosen] : undefined);
      });
      chips.appendChild(b);
    }
    return h("div", { class: "field span-2" }, h("div", { class: "field-label" }, shortLabel, h("span", { class: "flag" }, def.flag)), chips, help ? h("div", { class: "field-help" }, help) : null);
  }
  const numeric = def.type === "int" || def.type === "float";
  const inp = input({
    type: numeric ? "number" : "text",
    step: def.type === "float" ? "any" : undefined,
    value: value ?? "",
    placeholder: def.default != null ? `По умолчанию: ${def.default}` : (def.multiple ? "Несколько значений через пробел" : ""),
    oninput: (e) => onChange(e.target.value === "" ? undefined : e.target.value),
  });
  const wide = def.dest === "search" || def.dest === "excluded_filter" || def.kind === "positional";
  return field({ ...fieldOpts, control: inp, span2: wide });
}

export default async function run(root, ctx) {
  mount(root, pageHeader("Запуск", "Соберите команду в форме или введите её целиком. Задачи, которые ходят в hh.ru, выполняются по очереди."), skeleton(6));
  let schema;
  try {
    schema = await get("schema");
  } catch (err) {
    mount(root, errorState(err, () => run(root, ctx)));
    return;
  }

  const lookups = { resumes: null, templates: null };
  get("profile").then((p) => { lookups.resumes = p.resumes || []; if (current) renderForm(); }).catch(() => {});
  get("letters").then((l) => { lookups.templates = l.templates || []; if (current) renderForm(); }).catch(() => {});

  const saved = loadSaved();
  const ops = schema.operations;
  let mode = ctx.params.get("mode") === "raw" ? "raw" : "form";
  let current = ops.find((o) => o.name === ctx.params.get("op")) || ops[0];

  const listCard = h("nav", { class: "card op-list", "aria-label": "Команды" });
  const formHost = h("div", { class: "stack" });

  function values(name) { return (saved[name] ||= {}); }
  function globals() { return (saved.__global ||= {}); }

  const opSelect = select(ops.map((o) => [o.name, o.title]), { class: "select op-select", "aria-label": "Команда" });
  opSelect.addEventListener("change", () => {
    current = ops.find((o) => o.name === opSelect.value);
    mode = "form"; renderList(); renderForm();
    history.replaceState(null, "", `#/run?op=${current.name}`);
  });

  function renderList() {
    opSelect.value = current.name;
    opSelect.hidden = mode !== "form";
    mount(listCard, ...ops.map((op) => h("button", {
      type: "button", class: "op-item", "aria-current": String(mode === "form" && op === current),
      onclick: () => { current = op; mode = "form"; renderList(); renderForm(); history.replaceState(null, "", `#/run?op=${op.name}`); },
    }, h("span", { class: "op-title" }, op.title), h("span", { class: "op-name" }, op.name))));
  }

  async function submit(args, btn, title) {
    await busy(btn, async () => {
      try {
        const job = await post("jobs", { args, title });
        saveValues(saved);
        ctx.refreshRunning();
        toast(job.status === "queued" ? "Задача поставлена в очередь" : "Задача запущена");
        ctx.navigate("jobs", { id: job.id });
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  function renderForm() {
    if (mode === "raw") return renderRaw();
    const op = current;
    const vals = values(op.name);
    const preview = h("code", { class: "cmd-preview", "aria-live": "polite" });
    const updatePreview = () => {
      preview.textContent = ["hh-applicant-tool", ...buildArgs(schema, op, vals, globals())].map(shellQuote).join(" ");
    };
    const setter = (store, dest) => (v) => { if (v === undefined) delete store[dest]; else store[dest] = v; updatePreview(); };

    const allArgs = op.groups.flatMap((g) => g.args);
    const primaryNames = op.primary || allArgs.slice(0, 6).map((a) => a.dest);
    const primary = primaryNames.map((n) => allArgs.find((a) => a.dest === n)).filter(Boolean);
    const rest = op.groups.map((g) => ({ ...g, args: g.args.filter((a) => !primary.includes(a)) })).filter((g) => g.args.length);

    const mkGrid = (defs, store) => h("div", { class: "form-grid" }, defs.map((d) => control(d, store[d.dest], setter(store, d.dest), lookups)));

    const advancedCount = rest.reduce((n, g) => n + g.args.filter((a) => vals[a.dest] !== undefined).length, 0);
    const globalCount = schema.global.filter((a) => globals()[a.dest] !== undefined).length;

    const runBtn = button(op.mutating && isDry(vals) ? "Пробный запуск" : "Запустить", { variant: "primary", iconName: "play" });
    runBtn.addEventListener("click", () => submit(buildArgs(schema, op, vals, globals()), runBtn, op.title));

    mount(formHost,
      h("section", { class: "card", "aria-labelledby": "op-title" },
        h("div", { class: "card-header" },
          h("div", null, h("h2", { id: "op-title", class: "card-title" }, op.title),
            op.help ? h("p", { class: "card-hint" }, op.help) : null)),
        h("div", { class: "card-body stack" },
          op.mutating && "dry_run" in Object.fromEntries(allArgs.map((a) => [a.dest, 1]))
            ? callout("info", h("p", null, "Сначала попробуйте с флагом ", h("code", null, "--dry-run"), ": команда покажет, что сделает, но ничего не отправит."))
            : null,
          primary.length ? mkGrid(primary, vals) : h("p", { class: "muted" }, "У команды нет параметров."),
          rest.length ? h("details", { class: "disclosure", open: advancedCount > 0 || undefined },
            h("summary", null, chev(), "Дополнительно", advancedCount ? h("span", { class: "badge badge-accent plain" }, String(advancedCount)) : null),
            h("div", { class: "stack" }, rest.map((g) => h("div", { class: "stack-sm" }, g.title ? h("h4", { class: "muted" }, g.title) : null, mkGrid(g.args, vals))))) : null,
          h("details", { class: "disclosure", open: globalCount > 0 || undefined },
            h("summary", null, chev(), "Общие параметры утилиты", globalCount ? h("span", { class: "badge badge-accent plain" }, String(globalCount)) : null),
            mkGrid(schema.global, globals())),
          h("div", { class: "stack-sm" }, h("div", { class: "row-between" }, h("span", { class: "field-label" }, "Команда"),
            button("Копировать", { size: "sm", variant: "ghost", iconName: "copy", onClick: () => copyText(preview.textContent) })), preview)),
        h("div", { class: "card-footer" },
          button("Сбросить", { variant: "ghost", onClick: () => { saved[op.name] = {}; saveValues(saved); renderForm(); } }),
          runBtn)));
    updatePreview();

    // Подпись основной кнопки следует за dry-run
    formHost.onchange = () => {
      runBtn.lastChild.textContent = op.mutating && isDry(vals) ? "Пробный запуск" : "Запустить";
    };
  }

  function renderRaw() {
    const ta = textarea({ class: "textarea code", rows: 4, value: saved.__raw || "", placeholder: 'apply-vacancies --search "Python" --dry-run' });
    const runBtn = button("Запустить", { variant: "primary", iconName: "play" });
    runBtn.addEventListener("click", () => {
      const text = ta.value.trim();
      if (!text) { ta.setAttribute("aria-invalid", "true"); ta.focus(); return; }
      saved.__raw = text;
      submit(text, runBtn, "Своя команда");
    });
    ta.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runBtn.click(); });
    mount(formHost,
      h("section", { class: "card" },
        h("div", { class: "card-header" }, h("div", null, h("h2", { class: "card-title" }, "Своя команда"),
          h("p", { class: "card-hint" }, "Аргументы как в терминале, без hh-applicant-tool в начале. Cmd/Ctrl+Enter запускает."))),
        h("div", { class: "card-body" }, field({ label: "Аргументы", control: ta })),
        h("div", { class: "card-footer" }, runBtn)));
    ta.focus();
  }

  const modeSwitch = segmented([["form", "Форма"], ["raw", "Своя команда"]], mode, (m) => {
    mode = m; renderList(); renderForm();
  }, "Способ запуска");

  mount(root,
    pageHeader("Запуск", "Соберите команду в форме или введите её целиком. Задачи, которые ходят в hh.ru, выполняются по очереди.", modeSwitch),
    h("div", { class: "run-layout" }, listCard, h("div", { class: "stack" }, opSelect, formHost)));
  renderList();
  renderForm();
}
