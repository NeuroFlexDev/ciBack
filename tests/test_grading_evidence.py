from copy import deepcopy
import pytest
from app.routes.ai_grading import validate_candidate


def test_rubric_evidence_is_bound_to_actual_student_response():
    rubric = {"max_points": 4, "criteria": [{"id": "a", "max_points": 4}]}
    answer = {"text": "Step one: verify the source."}
    proposal = {"points": 2, "feedback": "Partial explanation", "criteria_scores": [{"id": "a", "points": 2,
        "rationale": "Verification is named but its method is missing.", "evidence": ["verify the source"]}]}
    assert validate_candidate(proposal, rubric, answer)["points"] == 2
    for evidence in (["Invented full method"], [], ["Step two from the expected answer"]):
        bad = deepcopy(proposal)
        bad["criteria_scores"][0]["evidence"] = evidence
        with pytest.raises(ValueError):
            validate_candidate(bad, rubric, answer)
    zero = deepcopy(proposal)
    zero["points"] = zero["criteria_scores"][0]["points"] = 0
    zero["criteria_scores"][0]["evidence"] = []
    assert validate_candidate(zero, rubric, answer)["points"] == 0


def test_missing_or_duplicate_rubric_cannot_be_scored():
    candidate = {"points": 0, "feedback": "Insufficient", "criteria_scores": []}
    with pytest.raises(ValueError, match="approved rubric"):
        validate_candidate(candidate, {"max_points": 4, "criteria": []}, {})
    criterion = {"id": "a", "points": 0, "rationale": "No evidence", "evidence": []}
    candidate["criteria_scores"] = [criterion, criterion]
    with pytest.raises(ValueError):
        validate_candidate(candidate, {"max_points": 4, "criteria": [{"id": "a", "max_points": 2}, {"id": "b", "max_points": 2}]}, {})
