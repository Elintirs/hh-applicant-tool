// Тонкая обёртка над API панели: единый формат ошибок и заголовок,
// без которого сервер отклоняет изменяющие запросы
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: { "X-Panel-Request": "1" }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`api/${path}`, opts);
  } catch {
    throw new ApiError("Панель недоступна. Проверьте, что контейнер запущен.", 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* пустой ответ */ }
  if (res.status === 401 && path !== "login") onUnauthorized();
  if (!res.ok) throw new ApiError((data && data.error) || `Ошибка ${res.status}`, res.status);
  return data;
}

export const get = (p) => api(p);
export const post = (p, body = {}) => api(p, { method: "POST", body });
export const put = (p, body = {}) => api(p, { method: "PUT", body });
export const del = (p) => api(p, { method: "DELETE" });
