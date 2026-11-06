import { get, post } from "../api.js";
import { h, mount, button, busy, toast, empty, skeleton, errorState, pageHeader, relTime, callout, plural } from "../ui.js";

const SVG = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined) el.setAttribute(k, v);
  for (const c of children) if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return el;
}

// Группы статусов для графика: остальное складываем в «прочее»
const SERIES = [
  { id: "response", label: "Без ответа", match: (k) => k === "response" },
  { id: "invitation", label: "Приглашения", match: (k) => k === "invitation" || k === "interview" || k === "hired" || k === "offer" },
  { id: "discard", label: "Отказы", match: (k) => k === "discard" || k === "discard_by_applicant" || k === "discard_by_employer" },
  { id: "other", label: "Прочее", match: () => true },
];
const seriesOf = (k) => SERIES.find((x) => x.match(k)).id;

function stackedBars(days) {
  const W = 720, H = 200, pad = { l: 28, r: 8, t: 8, b: 22 };
  const bw = (W - pad.l - pad.r) / days.length;
  const totals = days.map((d) => Object.values(d.by_state).reduce((a, b) => a + b, 0));
  const max = Math.max(1, ...totals);
  const y = (v) => H - pad.b - (v / max) * (H - pad.t - pad.b);
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Отклики по дням за 30 дней" });
  // Сетка
  for (const v of [0, Math.round(max / 2), max]) {
    svg.appendChild(s("line", { class: "axis", x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }));
    svg.appendChild(s("text", { class: "tick", x: pad.l - 6, y: y(v) + 3, "text-anchor": "end" }, String(v)));
  }
  days.forEach((d, i) => {
    const groups = {};
    for (const [k, v] of Object.entries(d.by_state)) groups[seriesOf(k)] = (groups[seriesOf(k)] || 0) + v;
    let acc = 0;
    const x = pad.l + i * bw + bw * 0.15;
    const g = s("g");
    g.appendChild(s("title", {}, `${new Date(d.date).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}: ${totals[i]}`));
    for (const ser of SERIES) {
      const v = groups[ser.id] || 0;
      if (!v) continue;
      const rect = s("rect", { x, y: y(acc + v), width: bw * 0.7, height: Math.max(1, y(acc) - y(acc + v)), rx: 2, class: `series-${ser.id}` });
      rect.style.fill = "var(--series)";
      g.appendChild(rect);
      acc += v;
    }
    svg.appendChild(g);
    if (i % 5 === 0 || i === days.length - 1) {
      const label = new Date(d.date).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
      svg.appendChild(s("text", { class: "tick", x: x + bw * 0.35, y: H - 6, "text-anchor": "middle" }, label));
    }
  });
  return svg;
}

function kpi(label, value, note) {
  return h("div", { class: "card kpi" }, h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" }, value), note ? h("div", { class: "kpi-note" }, note) : null);
}

const pct = (a, b) => (b ? `${Math.round((a / b) * 100)}%` : "–");

export default async function stats(root) {
  mount(root, pageHeader("Статистика", "Отклики из hh.ru."), skeleton(6));
  let data;
  try { data = (await get("stats")).stats; } catch (err) { mount(root, errorState(err, () => stats(root))); return; }

  const refreshBtn = button("Обновить из hh.ru", { variant: "primary", iconName: "refresh" });
  const doRefresh = () => busy(refreshBtn, async () => {
    try { data = (await post("stats/refresh")).stats; render(); toast("Статистика обновлена"); }
    catch (err) { toast(err.message, "error"); }
  });
  refreshBtn.addEventListener("click", doRefresh);

  function render() {
    if (!data) {
      mount(root, pageHeader("Статистика", "Отклики из hh.ru.", refreshBtn),
        h("div", { class: "card" }, empty({ iconName: "chart", title: "Данных пока нет", text: "Загрузим отклики из hh.ru и посчитаем сводку. Это займёт до минуты, если откликов много." , action: refreshBtn })));
      return;
    }
    const count = (pred) => data.by_state.filter((x) => pred(x.id)).reduce((a, x) => a + x.count, 0);
    const invites = count((k) => seriesOf(k) === "invitation");
    const discards = count((k) => seriesOf(k) === "discard");
    const last30 = data.days.reduce((a, d) => a + Object.values(d.by_state).reduce((x, y) => x + y, 0), 0);
    const maxResume = Math.max(1, ...data.by_resume.map((r) => r.count));

    mount(root,
      pageHeader("Статистика", `Отклики из hh.ru. Обновлено ${relTime(data.updated_at)}.`, refreshBtn),
      h("div", { class: "stack-lg" },
        data.truncated ? callout("info", h("p", null, `Посчитаны последние ${data.total} откликов.`)) : null,
        h("div", { class: "grid-4" },
          kpi("Всего откликов", data.total.toLocaleString("ru-RU"), `${last30} за 30 дней`),
          kpi("Просмотрено", pct(data.viewed, data.total), `${data.viewed} ${plural(data.viewed, "отклик", "отклика", "откликов")}`),
          kpi("Приглашения", invites.toLocaleString("ru-RU"), `${pct(invites, data.total)} от всех`),
          kpi("Отказы", discards.toLocaleString("ru-RU"), `${pct(discards, data.total)} от всех`)),
        h("section", { class: "card" },
          h("div", { class: "card-header" }, h("h3", null, "Отклики по дням"),
            h("div", { class: "legend" }, SERIES.map((x) => { const i = h("i", { class: `series-${x.id}` }); i.style.background = "var(--series)"; return h("span", null, i, x.label); }))),
          h("div", { class: "card-body" }, stackedBars(data.days))),
        h("div", { class: "grid-2" },
          h("section", { class: "card" },
            h("div", { class: "card-header" }, h("h3", null, "По статусам")),
            h("table", { class: "table" },
              h("tbody", null, data.by_state.map((x) => h("tr", null, h("td", null, x.name), h("td", { class: "num" }, x.count.toLocaleString("ru-RU")), h("td", { class: "num subtle" }, pct(x.count, data.total))))))),
          h("section", { class: "card" },
            h("div", { class: "card-header" }, h("h3", null, "По резюме")),
            h("div", { class: "card-body stack" }, data.by_resume.length ? data.by_resume.map((r) => {
              const fill = h("span"); fill.style.width = `${(r.count / maxResume) * 100}%`;
              return h("div", { class: "stack-sm" }, h("div", { class: "row-between" }, h("span", { class: "truncate" }, r.title), h("span", { class: "subtle" }, String(r.count))), h("div", { class: "bar" }, fill));
            }) : h("p", { class: "muted" }, "Нет данных")))),
        h("section", { class: "card" },
          h("div", { class: "card-header" }, h("h3", null, "Последние отклики")),
          h("ul", { class: "list" }, data.recent.map((r) => h("li", { class: "list-item" },
            h("div", { class: "list-item-main" },
              h("div", { class: "list-item-title truncate" }, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.vacancy) : r.vacancy),
              h("div", { class: "list-item-meta truncate" }, [r.employer, r.updated_at ? relTime(Date.parse(r.updated_at) / 1000) : null].filter(Boolean).join(" · "))),
            r.has_updates ? h("span", { class: "badge badge-accent" }, "Новое") : null,
            h("span", { class: `badge ${seriesOf(r.state) === "invitation" ? "badge-success" : seriesOf(r.state) === "discard" ? "badge-danger" : ""}` }, r.state_name))))),
      ));
  }
  render();
}
