import datetime as dt

from hh_applicant_tool.operations.clear_negotiations import stale_responses

NOW = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


def neg(state, days_ago, hidden=False):
    ts = (NOW - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return {"id": f"{state}{days_ago}", "state": {"id": state}, "updated_at": ts, "hidden": hidden}


def test_only_old_unanswered_responses():
    negs = [
        neg("response", 30),
        neg("response", 10),  # ещё свежий
        neg("response", 40, hidden=True),  # уже скрыт
        neg("interview", 60),  # приглашение не трогаем
        neg("discard", 60),  # отказы чистит обычный режим
    ]
    assert [n["id"] for n in stale_responses(negs, 21, NOW)] == ["response30"]
