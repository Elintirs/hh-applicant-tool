import { get, post } from "../api.js";
import {
  h, mount, button, empty, skeleton, pageHeader, callout, statusBadge, relTime, dateTime, plural, busy, toast,
} from "../ui.js";

import { ledgerRow } from "./journal.js";

function block(title, action, ...body) {
  return h("section", { class: "side-block" },
    h("div", { class: "panel-head" }, h("h2", null, title), action || null),
    ...body);
}

const today = () => new Date().toISOString().slice(0, 10);

export default async function overview(root, ctx) {
  mount(root, pageHeader("Обзор", "Что утилита сделала сегодня и что ждёт вас."),
    h("div", { class: "overview" }, h("div", { class: "overview-main" }, skeleton(6)), h("div", { class: "overview-side" }, skeleton(4))));

  // Профиль ходит в hh.ru и бывает медленным: грузим параллельно и рисуем
  // остальное, не дожидаясь его
  const [profileRes, jobsRes, schedRes, statsRes, modelsRes, journalRes] = await Promise.allSettled([
    get("profile"), get("jobs"), get("schedule"), get("stats"), get("models"), get("journal?limit=300"),
  ]);
  const profile = profileRes.status === "fulfilled" ? profileRes.value : { error: profileRes.reason.message };
  const jobs = jobsRes.status === "fulfilled" ? jobsRes.value.jobs : [];
  const sched = schedRes.status === "fulfilled" ? schedRes.value.items : [];
  const stats = statsRes.status === "fulfilled" ? statsRes.value.stats : null;
  const modelsCfg = modelsRes.status === "fulfilled" ? modelsRes.value : null;
  const entries = journalRes.status === "fulfilled" ? journalRes.value.items || [] : null;

  // ---- лента решений за сегодня: главный объект экрана ----
  const day = (entries || []).filter((r) => (r.ts || "").startsWith(today()));
  const count = (...res) => day.filter((r) => res.includes(r.result)).length;
  const tallyItem = (label, n, href, attention) => h("div", { class: attention && n ? "is-attention" : null },
    h("dt", null, label), h("dd", null, href && n ? h("a", { href }, String(n)) : String(n)));
  const tally = h("dl", { class: "tally" },
    tallyItem("Отправлено", count("sent", "form_sent"), "#/journal"),
    tallyItem("Отсеял AI", count("ai_rejected", "excluded"), "#/journal"),
    tallyItem("Ответить самому", count("chat_manual"), "#/chats?unread=1", true),
    tallyItem("Ошибки", count("error", "limit", "form_error"), "#/journal"));
  let weekLine = null;
  if (stats) {
    const last7 = stats.days.slice(-7).reduce((a, d) => a + Object.values(d.by_state).reduce((x, y) => x + y, 0), 0);
    weekLine = h("p", { class: "subtle" }, `За неделю ${last7} ${plural(last7, "отклик", "отклика", "откликов")}, всего ${(stats.found ?? stats.total).toLocaleString("ru-RU")}. `, h("a", { href: "#/negotiations?tab=stats" }, "Статистика откликов"));
  }
  const recentEntries = (entries || []).slice(0, 8);
  const ledger = entries === null
    ? callout("danger", h("p", null, `Журнал не загрузился: ${journalRes.reason.message}`))
    : recentEntries.length
      ? h("ol", { class: "ledger" }, recentEntries.map((r) => ledgerRow(r, { compact: true })))
      : empty({ iconName: "sparkle", title: "Решений пока нет",
          text: "Здесь появятся вакансии, которые разобрал AI: что подошло, что отсеяно и почему.",
          action: button("Запустить отклики", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run") }) });
  const main = h("div", { class: "overview-main" },
    modelsCfg && !modelsCfg.configured ? callout("info", h("p", null, "AI не настроен: письма будут из шаблонов, фильтр вакансий и автоответчик работать не будут, а капчу в Docker решить нечем."), h("div", null, button("Настроить модели", { size: "sm", onClick: () => ctx.navigate("models") }))) : null,
    h("div", { class: "panel-head" }, h("h2", null, "Сегодня"), h("a", { href: "#/journal" }, "Весь журнал")),
    tally,
    weekLine,
    ledger);

  // ---- аккаунт ----
  let account;
  if (profile.logged_in === false) {
    account = block("Аккаунт", null,
      h("p", { class: "muted" }, "Вход не выполнен. Без входа утилита не сможет откликаться; код из СМС или почты спросим прямо в панели."),
      h("div", null, button("Войти в hh.ru", { variant: "primary", iconName: "login", onClick: () => ctx.navigate("login") })));
  } else if (profile.error) {
    account = block("Аккаунт", null, callout("danger", h("p", null, `Не удалось получить профиль: ${profile.error}`),
      h("p", null, "Если токен устарел, войдите заново."),
      h("div", null, button("Войти заново", { size: "sm", onClick: () => ctx.navigate("login") }))));
  } else {
    const me = profile.me;
    const name = [me.first_name, me.last_name].filter(Boolean).join(" ") || "Без имени";
    const c = me.counters || {};
    const fig = (label, n) => h("div", null, h("dt", null, label), h("dd", null, String(n)));
    account = block("Аккаунт", h("span", { class: "badge badge-success" }, "Вход выполнен"),
      h("div", null, h("div", { class: "list-item-title" }, name), h("div", { class: "subtle" }, me.email || "")),
      !me.is_applicant ? callout("warning", h("p", null, "Вы вошли не как соискатель. Войдите заново под аккаунтом соискателя.")) : null,
      h("dl", { class: "side-figures" },
        fig("Резюме", c.resumes_count ?? profile.resumes.length),
        fig("Просмотры", c.new_resume_views ?? 0),
        fig("Чаты", c.unread_negotiations ?? 0)),
      profile.resumes.length ? h("ul", { class: "side-list" }, profile.resumes.map((r) => h("li", null,
        h("div", { class: "grow" },
          h("div", { class: "truncate" }, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.title) : r.title),
          h("div", { class: "subtle" }, [r.status, r.updated_at ? `обновлено ${relTime(Date.parse(r.updated_at) / 1000)}` : null].filter(Boolean).join(", "))),
        r.can_publish_or_update ? h("span", { class: "badge badge-success" }, "Можно поднять") : null))) : null);
  }

  // ---- ближайшие задачи ----
  const upcoming = sched.filter((i) => i.enabled && i.next_run_at).sort((a, b) => a.next_run_at - b.next_run_at).slice(0, 4);
  const schedBlock = block("Расписание", h("a", { href: "#/schedule" }, "Всё расписание"),
    upcoming.length
      ? h("ul", { class: "side-list" }, upcoming.map((i) => h("li", null,
          h("span", { class: "grow truncate" }, i.name),
          h("span", { class: "subtle nowrap", title: dateTime(i.next_run_at) }, relTime(i.next_run_at)))))
      : h("p", { class: "subtle" }, "Ничего не запланировано. Поднимать резюме и откликаться можно по расписанию."));

  // ---- последние запуски ----
  const recent = jobs.slice(0, 5);
  const jobsBlock = block("Запуски", h("a", { href: "#/jobs" }, "Все задачи"),
    recent.length
      ? h("ul", { class: "side-list" }, recent.map((j) => h("li", null,
          h("a", { class: "grow truncate", href: `#/jobs?id=${encodeURIComponent(j.id)}` }, j.title || j.args[0]),
          statusBadge(j.status))))
      : h("p", { class: "subtle" }, "Запусков ещё не было."),
    h("div", { class: "side-actions" },
      button("Новый запуск", { size: "sm", iconName: "play", onClick: () => ctx.navigate("run") }),
      // Действие, а не переход: запускаем подъём и показываем ход задачи
      button("Поднять резюме", { size: "sm", iconName: "up", onClick: (e) => busy(e.currentTarget, async () => {
        try { const job = await post("jobs", { args: ["update-resumes"] }); ctx.refreshRunning && ctx.refreshRunning(); toast("Поднимаем резюме"); ctx.navigate("jobs", { id: job.id }); }
        catch (err) { toast(err.message, "error"); }
      }) })));

  mount(root,
    pageHeader("Обзор", "Что утилита сделала сегодня и что ждёт вас.",
      button("Подобрать вакансии", { variant: "primary", iconName: "search", onClick: () => ctx.navigate("vacancies") })),
    h("div", { class: "overview" }, main, h("aside", { class: "overview-side", "aria-label": "Аккаунт и расписание" }, account, schedBlock, jobsBlock)));
}
