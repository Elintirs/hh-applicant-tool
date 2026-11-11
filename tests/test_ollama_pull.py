import requests

from hh_applicant_tool.panel import ollama as ol


class Store(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def make(monkeypatch, outcomes):
    o = ol.Ollama(Store(ollama_url="http://x:1"))
    calls = []

    def once(name, state):
        calls.append(name)
        res = outcomes.pop(0)
        if isinstance(res, Exception):
            raise res

    monkeypatch.setattr(o, "_pull_once", once)
    monkeypatch.setattr(ol, "PULL_RETRY_DELAY", 0)
    o._pulls["m"] = {"name": "m", "status": "", "completed": 0, "total": 0, "error": None, "done_at": None}
    o._pull("m")
    return o._pulls["m"], calls


def test_retries_network_errors_until_done(monkeypatch):
    state, calls = make(monkeypatch, [requests.ConnectionError("EOF"), ol.OllamaError("загрузка стоит"), None])
    assert state["status"] == "Готово" and len(calls) == 3


def test_does_not_retry_missing_model(monkeypatch):
    state, calls = make(monkeypatch, [ol.OllamaError("pull model manifest: file does not exist")])
    assert state["status"] == "Ошибка" and len(calls) == 1
