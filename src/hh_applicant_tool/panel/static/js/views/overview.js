import { get } from "../api.js";
import {
  h, mount, button, empty, skeleton, pageHeader, callout, statusBadge, relTime, dateTime, plural,
} from "../ui.js";

function section(title, action, ...body) {
  return h("section", { class: "card" },
    h("div", { class: "card-header" }, h("h3", null, title), action || null),
    ...body);
}

export default async function overview(root, ctx) {
  mount(root, pageHeader("Обзор", "Аккаунт, ближайшие задачи и последние запуски."),
    h("div", { class: "grid-2" }, h("div", { class: "card card-body" }, skeleton(4)), h("div", { class: "card card-body" }, skeleton(4))));

  // Профиль ходит в hh.ru и бывает медленным: грузим параллельно и рисуем
  // остальное, не дожидаясь его
  const [profileRes, jobsRes, schedRes, statsRes, modelsRes] = await Promise.allSettled([
    get("profile"), get("jobs"), get("schedule"), get("stats"), get("models"),
  ]);
  const profile = profileRes.status === "fulfilled" ? profileRes.value : { error: profileRes.reason.message };
  const jobs = jobsRes.status === "fulfilled" ? jobsRes.value.jobs : [];
  const sched = schedRes.status === "fulfilled" ? schedRes.value.items : [];
  const stats = statsRes.status === "fulfilled" ? statsRes.value.stats : null;
  const modelsCfg = modelsRes.status === "fulfilled" ? modelsRes.value : null;

  // ---- аккаунт ----
  let account;
  if (profile.logged_in === false) {
    account = section("Аккаунт", null, h("div", { class: "card-body" }, empty({
      iconName: "user", title: "Вход не выполнен",
      text: "Без входа утилита не сможет откликаться. Вход идёт по коду из СМС или почты: введёте его в окне задачи.",
      action: button("Войти в hh.ru", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run", { op: "authorize" }) }),
    })));
  } else if (profile.error) {
    account = section("Аккаунт", null, h("div", { class: "card-body" }, callout("danger", h("p", null, `Не удалось получить профиль: ${profile.error}`),
      h("p", null, "Если токен устарел, войдите заново."),
      h("div", null, button("Войти заново", { size: "sm", onClick: () => ctx.navigate("run", { op: "authorize" }) })))));
  } else {
    const me = profile.me;
    const name = [me.first_name, me.last_name].filter(Boolean).join(" ") || "Без имени";
    const c = me.counters || {};
    account = section("Аккаунт", h("span", { class: "badge badge-success" }, "Вход выполнен"),
      h("div", { class: "card-body stack" },
        h("div", null, h("div", { class: "list-item-title" }, name), h("div", { class: "subtle" }, me.email || "")),
        !me.is_applicant ? callout("warning", h("p", null, "Вы вошли не как соискатель. Войдите заново под аккаунтом соискателя.")) : null,
        h("div", { class: "grid-3" },
          h("div", null, h("div", { class: "kpi-label" }, "Резюме"), h("div", { class: "kpi-value" }, String(c.resumes_count ?? profile.resumes.length))),
          h("div", null, h("div", { class: "kpi-label" }, "Новые просмотры"), h("div", { class: "kpi-value" }, String(c.new_resume_views ?? 0))),
          h("div", null, h("div", { class: "kpi-label" }, "Непрочитанные"), h("div", { class: "kpi-value" }, String(c.unread_negotiations ?? 0))))),
      profile.resumes.length ? h("ul", { class: "list" }, profile.resumes.map((r) => h("li", { class: "list-item" },
        h("div", { class: "list-item-main" },
          h("div", { class: "list-item-title truncate" }, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.title) : r.title),
          h("div", { class: "list-item-meta" }, [r.status, r.updated_at ? `обновлено ${relTime(Date.parse(r.updated_at) / 1000)}` : null].filter(Boolean).join(" · "))),
        r.can_publish_or_update ? h("span", { class: "badge badge-accent" }, "Можно поднять") : null))) : null);
  }

  // ---- ближайшие задачи ----
  const upcoming = sched.filter((i) => i.enabled && i.next_run_at).sort((a, b) => a.next_run_at - b.next_run_at).slice(0, 4);
  const schedCard = section("Ближайшие по расписанию", h("a", { href: "#/schedule" }, "Всё расписание"),
    upcoming.length
      ? h("ul", { class: "list" }, upcoming.map((i) => h("li", { class: "list-item" },
          h("div", { class: "list-item-main" }, h("div", { class: "list-item-title" }, i.name), h("div", { class: "list-item-meta mono truncate" }, i.args.join(" "))),
          h("div", { class: "subtle nowrap", title: dateTime(i.next_run_at) }, relTime(i.next_run_at)))))
      : h("div", { class: "card-body" }, empty({ iconName: "clock", title: "Ничего не запланировано", text: "Поднимать резюме и откликаться можно по расписанию.",
          action: button("Настроить", { onClick: () => ctx.navigate("schedule") }) })));

  // ---- последние запуски ----
  const recent = jobs.slice(0, 6);
  const jobsCard = section("Последние запуски", h("a", { href: "#/jobs" }, "Все задачи"),
    recent.length
      ? h("ul", { class: "list" }, recent.map((j) => h("li", { class: "list-item is-link", tabindex: "0", role: "link",
          onclick: () => ctx.navigate("jobs", { id: j.id }),
          onkeydown: (e) => { if (e.key === "Enter") ctx.navigate("jobs", { id: j.id }); } },
          h("div", { class: "list-item-main" }, h("div", { class: "list-item-title truncate" }, j.title || j.args[0]), h("div", { class: "list-item-meta" }, relTime(j.created_at))),
          statusBadge(j.status))))
      : h("div", { class: "card-body" }, empty({ iconName: "terminal", title: "Запусков ещё не было", action: button("Запустить команду", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run") }) })));

  // ---- быстрые действия ----
  const quick = section("Быстрые действия", null, h("div", { class: "card-body row" },
    button("Откликнуться (пробно)", { iconName: "play", onClick: () => ctx.navigate("run", { op: "apply-vacancies" }) }),
    button("Поднять резюме", { iconName: "refresh", onClick: () => ctx.navigate("run", { op: "update-resumes" }) }),
    button("Автоответчик", { iconName: "mail", onClick: () => ctx.navigate("run", { op: "reply-chats" }) })));

  // ---- сводка ----
  let statsLine = null;
  if (stats) {
    const last7 = stats.days.slice(-7).reduce((a, d) => a + Object.values(d.by_state).reduce((x, y) => x + y, 0), 0);
    statsLine = callout("info", h("p", null, `За неделю ${last7} ${plural(last7, "отклик", "отклика", "откликов")}, всего ${stats.total}. `, h("a", { href: "#/stats" }, "Подробнее")));
  }

  mount(root,
    pageHeader("Обзор", "Аккаунт, ближайшие задачи и последние запуски.",
      button("Новый запуск", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run") })),
    h("div", { class: "stack" },
      modelsCfg && !modelsCfg.configured ? callout("info", h("p", null, "AI не настроен: письма будут из шаблонов, фильтр вакансий и автоответчик работать не будут, а капчу в Docker решить нечем. "), h("div", null, button("Настроить модели", { size: "sm", onClick: () => ctx.navigate("models") }))) : null,
      statsLine,
      h("div", { class: "grid-2" }, account, h("div", { class: "stack" }, schedCard, quick)),
      jobsCard));
}
