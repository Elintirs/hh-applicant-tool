from datetime import datetime, timedelta

from hh_applicant_tool.panel import scheduler as sch

NOW = datetime(2026, 11, 9, 12, 0)
ITEM = {"id": "r", "name": "Поднять резюме", "args": ["update-resumes"], "enabled": True, "mode": "resume"}


class Store:
    def __init__(self):
        self.data = {"schedule": [dict(ITEM)]}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, key, fn, default=None):
        self.data[key] = fn(self.data.get(key, default))


class Jobs:
    def __init__(self):
        self.fired, self.running = [], False

    def on_finish(self, fn):
        self.finish = fn

    def is_running(self, predicate):
        return self.running

    def submit(self, args, **kw):
        self.fired.append(kw["source"])
        self.running = True
        return type("Job", (), {"id": "j1", "status": "queued", "source": kw["source"]})()


def test_normalize_accepts_resume_mode():
    assert sch.normalize(ITEM)["mode"] == "resume"


def test_list_before_first_tick():
    s = sch.Scheduler(Store(), Jobs(), lambda p: None)
    s.jobs.running = False
    assert s.list()[0]["next_run_at"] is None


def test_fires_when_hh_allows_and_asks_again_after_finish():
    ready = [NOW + timedelta(hours=1)]
    asked = []

    def ready_at(profile):
        asked.append(profile)
        return ready[0]

    jobs = Jobs()
    s = sch.Scheduler(Store(), jobs, ready_at)

    s._tick(NOW)
    s._tick(NOW + timedelta(minutes=30))
    assert jobs.fired == [] and len(asked) == 1

    s._tick(NOW + timedelta(hours=1, minutes=1))
    assert jobs.fired == ["schedule:r"]

    # Пока задача в очереди или идёт, hh не спрашиваем и не запускаем снова
    s._tick(NOW + timedelta(hours=2))
    assert len(jobs.fired) == 1 and len(asked) == 1

    jobs.running = False
    jobs.finish(type("Job", (), {"id": "j1", "status": "done", "source": "schedule:r", "args": ["update-resumes"], "profile": "."})())
    ready[0] = NOW + timedelta(hours=5)
    s._tick(NOW + timedelta(hours=2))
    assert len(asked) == 2 and len(jobs.fired) == 1


def test_warms_up_before_ai_job_and_unloads_when_idle(monkeypatch):
    calls = []
    store = Store()
    store.data["schedule"] = [{
        "id": "a", "name": "Отклики", "args": ["apply-vacancies"], "enabled": True,
        "mode": "daily", "times": ["10:00"], "days": [],
    }]
    s = sch.Scheduler(store, Jobs(), None, lambda p, load: calls.append(load))
    monkeypatch.setattr(s, "_model_async", lambda profile, load: calls.append(load))
    morning = datetime(2026, 11, 10, 9, 0)

    s._tick(morning)
    assert calls == []
    s._tick(morning + timedelta(minutes=46))
    s._tick(morning + timedelta(minutes=50))
    assert calls == [True]  # прогрели один раз

    # Следующий запуск завтра: после задачи модель выгружаем
    s._planned["a"] = datetime.now() + timedelta(days=1)  # от текущего времени: тест не должен стареть
    s.jobs.running = False
    s._on_job_finish(type("Job", (), {"args": ["apply-vacancies"], "profile": ".", "source": "manual"})())
    assert calls == [True, False]

    # Через час ещё одна задача с моделью: не выгружаем
    s._planned["a"] = datetime.now() + timedelta(hours=1)
    s._on_job_finish(type("Job", (), {"args": ["apply-vacancies"], "profile": ".", "source": "manual"})())
    assert calls == [True, False]


def test_retries_later_when_hh_fails():
    def boom(profile):
        raise RuntimeError("Сначала войдите в аккаунт hh.ru")

    jobs = Jobs()
    s = sch.Scheduler(Store(), jobs, boom)
    s._tick(NOW)
    assert s._planned["r"] == NOW + sch.RESUME_RETRY
    assert jobs.fired == []


def test_daily_not_repeated_after_plan_reset():
    # Отклики в 10:00 уже прошли, в 10:05 сохранили расписание (план в памяти сброшен)
    ran = datetime(2026, 11, 9, 10, 0, 5).timestamp()
    item = {"id": "a", "name": "Отклики", "args": ["apply-vacancies"], "enabled": True,
            "mode": "daily", "times": ["10:00"], "days": [], "last_run_at": ran}
    store, jobs = Store(), Jobs()
    store.data["schedule"] = [sch.normalize(item)]
    s = sch.Scheduler(store, jobs)
    s._tick(datetime(2026, 11, 9, 10, 5))
    assert jobs.fired == []
    # Не запускавшийся слот в пределах 10 минут догоняется как раньше
    store.data["schedule"][0]["last_run_at"] = None
    s._planned.clear()
    s._tick(datetime(2026, 11, 9, 10, 5))
    assert jobs.fired == ["schedule:a"]
