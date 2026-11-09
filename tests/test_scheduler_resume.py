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
    jobs.finish(type("Job", (), {"id": "j1", "status": "done", "source": "schedule:r"})())
    ready[0] = NOW + timedelta(hours=5)
    s._tick(NOW + timedelta(hours=2))
    assert len(asked) == 2 and len(jobs.fired) == 1


def test_retries_later_when_hh_fails():
    def boom(profile):
        raise RuntimeError("Сначала войдите в аккаунт hh.ru")

    jobs = Jobs()
    s = sch.Scheduler(Store(), jobs, boom)
    s._tick(NOW)
    assert s._planned["r"] == NOW + sch.RESUME_RETRY
    assert jobs.fired == []
