import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from hh_applicant_tool.panel.server import PanelApp, make_handler


@pytest.fixture
def panel(tmp_path):
    app = PanelApp(tmp_path, token="secret-token")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/"
    srv.shutdown()


def call(base, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(base + path, method=method, headers=headers or {},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as ex:
        return ex.code, ex.headers


def test_panel_requires_token_and_csrf_header(panel):
    # Без cookie данные не отдаются
    assert call(panel, "jobs")[0] == 401
    # Изменяющий запрос без X-Panel-Request отклоняется ещё до проверки входа
    assert call(panel, "login", "POST", {"token": "secret-token"})[0] == 403
    assert call(panel, "login", "POST", {"token": "wrong"}, {"X-Panel-Request": "1"})[0] == 401
    status, headers = call(panel, "login", "POST", {"token": "secret-token"}, {"X-Panel-Request": "1"})
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    assert call(panel, "jobs", headers={"Cookie": cookie})[0] == 200
    # С cookie, но без заголовка изменять всё равно нельзя
    assert call(panel, "jobs/x/stop", "POST", {}, {"Cookie": cookie})[0] == 403


def test_no_token_panel_rejects_foreign_host(tmp_path):
    app = PanelApp(tmp_path)  # без токена: только этот компьютер
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/api/"
    try:
        assert call(base, "jobs")[0] == 200
        # Страница с домена evil.example, перепривязанного на 127.0.0.1
        assert call(base, "jobs", headers={"Host": "evil.example"})[0] == 403
    finally:
        srv.shutdown()


def test_chat_favorites_toggle(panel):
    status, headers = call(panel, "login", "POST", {"token": "secret-token"}, {"X-Panel-Request": "1"})
    h = {"Cookie": headers["Set-Cookie"].split(";")[0], "X-Panel-Request": "1"}

    def fav(cid, on):
        req = urllib.request.Request(panel + f"chats/{cid}/favorite", method="PUT", headers=h,
                                     data=json.dumps({"on": on}).encode())
        with urllib.request.urlopen(req) as r:
            return json.load(r)["ids"]

    assert fav(7, True) == [7]
    assert fav(9, True) == [7, 9]
    assert fav(7, True) == [9, 7]  # повторное добавление не дублирует
    assert fav(9, False) == [7]
    with urllib.request.urlopen(urllib.request.Request(panel + "chats/favorites", headers=h)) as r:
        assert json.load(r)["ids"] == [7]
