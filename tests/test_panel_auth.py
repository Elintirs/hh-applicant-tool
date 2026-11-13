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
