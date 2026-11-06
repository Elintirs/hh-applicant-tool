import { get, post, del } from "../api.js";
import {
  h, mount, clear, button, input, toast, busy, empty, skeleton, errorState, statusBadge,
  relTime, dateTime, duration, appendAnsi, pageHeader, confirmDialog, copyText,
} from "../ui.js";

const ACTIVE = new Set(["queued", "running"]);

function sourceLabel(job) {
  if (job.source && job.source.startsWith("schedule:")) return "По расписанию";
  return "Вручную";
}

export default async function jobs(root, ctx) {
  let list = [];
  let selectedId = ctx.params.get("id");
  let es = null;
  let pollTimer = null;
  let ansiState = { classes: [] };
  let follow = true;

  const listEl = h("div", { class: "card jobs-list", role: "list", "aria-label": "История запусков" });
  const detail = h("div", { class: "stack" });

  mount(root, pageHeader("Задачи", "История запусков и живой вывод. Если команда ждёт ввода (код из СМС, капча), ответьте в поле под логом."), skeleton(5));

  async function loadList() {
    try {
      list = (await get("jobs")).jobs;
    } catch (err) {
      mount(root, errorState(err, () => jobs(root, ctx)));
      return false;
    }
    renderList();
    return true;
  }

  function renderList() {
    if (!list.length) {
      mount(listEl, empty({ iconName: "terminal", title: "Запусков пока не было", text: "Запустите команду, и её вывод появится здесь.",
        action: button("К запуску", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run") }) }));
      return;
    }
    mount(listEl, ...list.map((j) => h("button", {
      type: "button", class: "job-row", role: "listitem", "aria-current": String(j.id === selectedId),
      onclick: () => select(j.id),
    },
      h("span", { class: "truncate list-item-title" }, j.title || j.args[0]),
      statusBadge(j.status),
      h("span", { class: "cmd truncate" }, j.args.join(" ")),
      h("span", { class: "subtle" }, `${sourceLabel(j)} · ${relTime(j.created_at)}${j.queue_position ? ` · место в очереди: ${j.queue_position}` : ""}`),
    )));
  }

  function closeStream() {
    if (es) { es.close(); es = null; }
  }

  function select(id) {
    selectedId = id;
    history.replaceState(null, "", `#/jobs?id=${id}`);
    renderList();
    renderDetail();
  }

  function renderDetail() {
    closeStream();
    const job = list.find((j) => j.id === selectedId);
    if (!job) {
      mount(detail, h("div", { class: "card" }, empty({ iconName: "list", title: "Выберите задачу", text: "Слева история запусков. Нажмите на любую, чтобы увидеть вывод." })));
      return;
    }
    ansiState = { classes: [] };
    follow = true;
    const log = h("pre", { class: "log", tabindex: "0", "aria-label": "Вывод команды", "aria-live": "off" });
    const followBtn = button("К последним строкам", { size: "sm", variant: "primary", onClick: () => { follow = true; log.scrollTop = log.scrollHeight; followBtn.hidden = true; } });
    followBtn.hidden = true;
    followBtn.classList.add("log-follow");
    log.addEventListener("scroll", () => {
      const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
      follow = atBottom;
      followBtn.hidden = atBottom;
    });

    const statusHost = h("span", null, statusBadge(job.status));
    const meta = h("span", { class: "subtle" });
    const stopBtn = button("Остановить", { size: "sm", variant: "danger", iconName: "stop" });
    const deleteBtn = button("", { size: "sm", variant: "ghost", iconName: "trash", title: "Удалить из истории" });
    const rerunBtn = button("Повторить", { size: "sm", iconName: "refresh" });

    const stdin = input({ placeholder: "Ответ для команды и Enter", "aria-label": "Ввод для команды" });
    const sendBtn = button("Отправить", { size: "sm", iconName: "send" });
    const inputRow = h("form", { class: "log-input" }, stdin, sendBtn);
    inputRow.addEventListener("submit", (e) => {
      e.preventDefault();
      busy(sendBtn, async () => {
        try {
          await post(`jobs/${job.id}/input`, { text: stdin.value });
          stdin.value = "";
        } catch (err) { toast(err.message, "error"); }
      });
    });

    stopBtn.addEventListener("click", () => busy(stopBtn, async () => {
      try { await post(`jobs/${job.id}/stop`); } catch (err) { toast(err.message, "error"); }
    }));
    deleteBtn.addEventListener("click", async () => {
      if (!(await confirmDialog({ title: "Удалить запуск из истории?", text: "Вывод тоже удалится. Это нельзя отменить.", confirm: "Удалить", danger: true }))) return;
      try {
        await del(`jobs/${job.id}`);
        selectedId = null;
        await loadList();
        renderDetail();
        toast("Запуск удалён");
      } catch (err) { toast(err.message, "error"); }
    });
    rerunBtn.addEventListener("click", () => busy(rerunBtn, async () => {
      try {
        const nj = await post("jobs", { args: job.args, title: job.title });
        ctx.refreshRunning();
        await loadList();
        select(nj.id);
      } catch (err) { toast(err.message, "error"); }
    }));

    const applyState = (j) => {
      Object.assign(job, j);
      mount(statusHost, statusBadge(j.status));
      const active = ACTIVE.has(j.status);
      stopBtn.hidden = !active;
      stopBtn.lastChild.textContent = j.status === "queued" ? "Убрать из очереди" : "Остановить";
      deleteBtn.hidden = active;
      rerunBtn.hidden = active;
      inputRow.hidden = j.status !== "running";
      const parts = [];
      if (j.started_at) parts.push(`начата ${dateTime(j.started_at)}`);
      if (j.started_at) parts.push(`длительность ${duration(j.started_at, j.finished_at)}`);
      if (j.exit_code !== null && j.exit_code !== undefined) parts.push(`код выхода ${j.exit_code}`);
      meta.textContent = parts.join(" · ") || `создана ${dateTime(j.created_at)}`;
      const row = list.find((x) => x.id === j.id);
      if (row && row.status !== j.status) { Object.assign(row, j); renderList(); }
    };

    mount(detail,
      h("section", { class: "card", "aria-labelledby": "job-title" },
        h("div", { class: "card-header" },
          h("div", { class: "stack-sm grow" },
            h("div", { class: "row" }, h("h2", { id: "job-title", class: "card-title" }, job.title || job.args[0]), statusHost),
            meta),
          h("div", { class: "row" }, rerunBtn, stopBtn, deleteBtn)),
        h("div", { class: "log-toolbar" },
          h("code", { class: "mono grow truncate", title: job.command }, job.command),
          button("", { size: "sm", variant: "ghost", iconName: "copy", title: "Копировать команду", onClick: () => copyText(job.command) }),
          button("", { size: "sm", variant: "ghost", iconName: "file", title: "Копировать вывод", onClick: () => copyText(log.textContent) })),
        h("div", { class: "log-wrap" }, log, followBtn),
        inputRow));
    applyState(job);

    // Поток вывода. EventSource сам переподключается; offset не даёт
    // получить один и тот же кусок дважды
    let offset = 0;
    const open = () => {
      es = new EventSource(`api/jobs/${job.id}/stream?offset=${offset}`);
      es.addEventListener("output", (e) => {
        const d = JSON.parse(e.data);
        offset = d.offset;
        ansiState = appendAnsi(log, d.text, ansiState);
        if (follow) log.scrollTop = log.scrollHeight;
      });
      es.addEventListener("status", (e) => applyState(JSON.parse(e.data)));
      es.addEventListener("end", () => { closeStream(); ctx.refreshRunning(); });
      es.onerror = () => {
        // Сервер закрыл поток или панель перезапускается: попробуем ещё раз
        closeStream();
        if (ACTIVE.has(job.status)) setTimeout(() => { if (selectedId === job.id && !es) open(); }, 2000);
      };
    };
    open();
  }

  if (!(await loadList())) return;
  mount(root,
    pageHeader("Задачи", "История запусков и живой вывод. Если команда ждёт ввода (код из СМС, капча), ответьте в поле под логом.",
      button("Новый запуск", { variant: "primary", iconName: "play", onClick: () => ctx.navigate("run") })),
    h("div", { class: "jobs-layout" }, listEl, detail));
  if (!selectedId && list.length) selectedId = list[0].id;
  renderList();
  renderDetail();

  // Обновляем список, чтобы видеть очередь и задачи из расписания
  pollTimer = setInterval(async () => {
    try {
      const fresh = (await get("jobs")).jobs;
      const changed = JSON.stringify(fresh.map((j) => [j.id, j.status, j.queue_position])) !== JSON.stringify(list.map((j) => [j.id, j.status, j.queue_position]));
      list = fresh;
      if (changed) renderList();
    } catch { /* ничего */ }
  }, 4000);

  return () => { closeStream(); clearInterval(pollTimer); clear(root); };
}
