import { get, put, post } from "../api.js";
import { h, mount, button, textarea, toast, skeleton, errorState, pageHeader, callout, confirmDialog, saveBar } from "../ui.js";

export default async function config(root) {
  mount(root, pageHeader("Конфиг", "config.toml целиком."), skeleton(8));
  let data;
  try { data = await get("config"); } catch (err) { mount(root, errorState(err, () => config(root))); return; }

  const ta = textarea({ class: "textarea code", rows: 28, value: data.text, "aria-label": "Содержимое config.toml" });
  ta.style.minHeight = "60vh";
  const status = h("div", { class: "row subtle", "aria-live": "polite" });
  const restoreBtn = button("Откатить к копии", { variant: "ghost", iconName: "undo" });
  restoreBtn.hidden = !data.has_backup;
  let saved = data.text;
  let timer = null;

  const bar = saveBar({
    onSave: async () => {
      try {
        data = await put("config", { text: ta.value });
        saved = data.text;
        restoreBtn.hidden = !data.has_backup;
        toast("Конфиг сохранён. Прошлая версия в config.toml.bak");
        status.textContent = "Сохранено";
        return true;
      } catch (err) { toast(err.message, "error"); return false; }
    },
    onReset: () => { ta.value = saved; validate(); },
  });

  // Проверка синтаксиса на лету: ошибку видно до сохранения
  const validate = () => {
    clearTimeout(timer);
    const dirty = ta.value !== saved;
    bar.set(dirty, "Есть несохранённые изменения");
    status.textContent = dirty ? "Изменён" : "Сохранено";
    timer = setTimeout(async () => {
      const r = await post("config/validate", { text: ta.value }).catch((err) => ({ ok: false, error: err.message }));
      ta.setAttribute("aria-invalid", String(!r.ok));
      status.classList.toggle("field-error", !r.ok);
      if (!r.ok) { status.textContent = r.error; if (dirty) bar.blockSave("Исправьте ошибку в TOML, чтобы сохранить"); }
    }, 300);
  };
  ta.addEventListener("input", validate);
  ta.addEventListener("keydown", (e) => {
    // Tab вставляет отступ, а не уводит фокус. Esc + Tab уходит дальше
    if (e.key === "Tab" && !e.shiftKey && !ta.dataset.escaped) { e.preventDefault(); ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end"); validate(); }
    ta.dataset.escaped = e.key === "Escape" ? "1" : "";
  });

  restoreBtn.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "Откатить config.toml к копии?", text: "Текущая версия станет новой копией, так что откат можно отменить таким же нажатием.", confirm: "Откатить" }))) return;
    try {
      data = await post("config/restore");
      ta.value = saved = data.text;
      validate();
      toast("Конфиг восстановлен из копии");
    } catch (err) { toast(err.message, "error"); }
  });

  mount(root,
    pageHeader("Конфиг", "config.toml целиком: все ключи, включая те, для которых нет отдельного экрана.", restoreBtn),
    h("div", { class: "stack" },
      callout("warning", h("p", null, "Здесь видны ключи API. Не показывайте экран посторонним.")),
      h("section", { class: "card" },
        h("div", { class: "card-header" }, h("code", { class: "mono truncate" }, data.path), status),
        h("div", { class: "card-body" }, ta)),
      h("p", { class: "subtle" }, "Cmd/Ctrl+S сохраняет. Tab вставляет отступ; чтобы перейти к следующему элементу, нажмите Esc, затем Tab.")),
    bar.el);
  validate();
  return () => bar.destroy();
}
