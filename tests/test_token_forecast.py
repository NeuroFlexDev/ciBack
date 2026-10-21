from app.ai.token_forecast import fit, predict, cross_validate, estimate_course


def sample(course, index=0, **overrides):
    return {"course_key": course, "call_id": index, "role": "lesson_writer", "model": "test/model",
            "status": "completed", "attempt": 1, "usage_reported": True, "max_output_tokens": 6000,
            "features": {"prompt_bytes": 3000, "units": 1, "has_qa_feedback": False},
            "input_tokens": 1000, "output_tokens": 500, **overrides}


def test_training_excludes_retries_failed_calls_revisions_and_unknown_usage():
    rows = [sample("a"), sample("a", 1, attempt=2), sample("a", 2, status="failed"),
            sample("a", 3, usage_reported=False), sample("a", 4, features={"prompt_bytes":3000,"units":1,"is_revision":True})]
    model = fit(rows)
    assert model["samples"] == 1 and model["excluded_samples"] == 4
    result = predict(model, role="lesson_writer", model_id="test/model",
                     request_features={"prompt_bytes":6000,"units":2}, max_output_tokens=6000)
    assert result["input_tokens"] == 2000 and result["output_tokens"] == 1000
    assert not result["includes_retries"] and result["provisional"]


def test_unknown_model_forecast_is_explicitly_uncalibrated_and_does_not_change_limits():
    result = predict({}, role="chat", model_id="new", request_features={"prompt_bytes":3000,"units":1}, max_output_tokens=2500)
    assert result["method"] == "uncalibrated_prior" and result["samples"] == 0
    assert result["output_tokens"] < 2500 and result["output_range"][1] <= 2500


def test_validation_holds_out_entire_course():
    rows = [sample("a", i) for i in range(4)] + [sample("b", i) for i in range(4)]
    rows += [sample("held", i, output_tokens=2000) for i in range(4)]
    report = cross_validate(rows)
    held = [r for r in report["predictions"] if r["course_key"] == "held"]
    assert len(held) == 4
    assert all(r["predicted_output"] < 600 for r in held)
    assert report["method"] == "leave_one_course_out"


def test_course_estimate_accounts_for_lessons_and_ingestion_batches():
    small = estimate_course(source_characters=5000, lessons=2)
    larger = estimate_course(source_characters=25000, lessons=4)
    assert larger["input_tokens"] > small["input_tokens"]
    assert larger["output_tokens"] > small["output_tokens"]
    assert larger["stages"][0]["calls"] == 3
    assert larger["stages"][3]["calls"] == 4
    assert larger["stages"][4]["calls"] == 2
    assert larger["cost_rub"] is not None and not larger["includes_retries"]
