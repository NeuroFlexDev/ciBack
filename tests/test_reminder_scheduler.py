from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from scripts import queue_learning_reminders as scheduler


def test_scheduler_commits_each_page_and_advances_cursor(monkeypatch):
    commits=[];cursors=[]
    @contextmanager
    def session():yield SimpleNamespace(commit=lambda:commits.append(True))
    def enqueue(db,*,after_id,limit):
        assert limit==100
        cursors.append(after_id)
        return {"queued":2,"skipped":1,"scanned":3,"next_cursor":12,"more":after_id==0}
    monkeypatch.setattr(scheduler,"queue_due_reminders",enqueue)
    assert scheduler.run(session)=={"queued":4,"skipped":2,"scanned":6}
    assert cursors==[0,12] and len(commits)==2


def test_scheduler_refuses_stuck_cursor(monkeypatch):
    @contextmanager
    def session():yield SimpleNamespace(commit=lambda:None)
    monkeypatch.setattr(scheduler,"queue_due_reminders",lambda *a,**kw:{"next_cursor":0,"more":True})
    with pytest.raises(RuntimeError,match="did not advance"):scheduler.run(session)
