"""Recovery smoke test for the configured test DB; never calls an AI provider."""
import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langgraph.graph import StateGraph, START, END
from app.ai.checkpoints import checkpoint_store
from app.core.config import settings

if settings.ENV != "test":
    raise SystemExit("Run only against an isolated database with ENV=test")
calls = []
fail = True

def first(state):
    calls.append("first")
    return {"value": 41}

def second(state):
    if fail:
        raise RuntimeError("simulated worker restart")
    calls.append("second")
    return {"value": state["value"] + 1}

builder = StateGraph(dict)
builder.add_node("first", first)
builder.add_node("second", second)
builder.add_edge(START, "first")
builder.add_edge("first", "second")
builder.add_edge("second", END)
config = {"configurable": {"thread_id": "persistence-test-" + uuid.uuid4().hex}}
with checkpoint_store() as saver:
    graph = builder.compile(checkpointer=saver)
    try:
        graph.invoke({}, config, durability="sync")
    except RuntimeError as exc:
        assert str(exc) == "simulated worker restart"
    assert graph.get_state(config).values == {"value": 41}
fail = False
with checkpoint_store() as saver:
    graph = builder.compile(checkpointer=saver)
    assert graph.invoke(None, config, durability="sync") == {"value": 42}
    assert calls == ["first", "second"], calls
    assert not graph.get_state({"configurable": {"thread_id": uuid.uuid4().hex}}).values
print("Checkpoint recovery and thread isolation: passed")
