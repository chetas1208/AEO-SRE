from app.changeguard.decision import Decision
from app.control_policy.learner import ControlLinUCB, CONTROL_ACTIONS
from app.control_policy.mask import compute_mask


def test_mask_blocks_only_invalid_digest():
    m = compute_mask([], digest_valid=False)
    assert [a.value for a in m.eligible] == [Decision.BLOCK.value]


def test_linucb_respects_eligible_only():
    learner = ControlLinUCB(dim=10)
    vec = [0.0] * 10
    rec = learner.recommend(vec, eligible=(Decision.DELAY.value, Decision.REQUIRE_REVIEW.value))
    assert rec.action in (Decision.DELAY.value, Decision.REQUIRE_REVIEW.value)
    for s in rec.scores:
        if s.action in CONTROL_ACTIONS and s.action not in (Decision.DELAY.value, Decision.REQUIRE_REVIEW.value):
            assert not s.eligible
