from app.intelligence.escalation import should_escalate_to_llm
from app.intelligence.laya.runtime import LayaRuntime
from app.intelligence.laya.schemas import LayaState


def test_laya_unavailable_when_disabled():
    rt = LayaRuntime()
    rt.enabled = False
    out = rt.choose(question="q", options=["A", "B"], state=LayaState(values={}))
    assert not out.available
    assert out.escalation_probability == 1.0


def test_escalation_when_laya_unavailable():
    d = should_escalate_to_llm(
        rule_answered=False,
        laya_available=False,
        laya_confidence=None,
        task_requires_semantics=False,
    )
    assert d.use_llm
    assert d.reason == "laya_unavailable"
