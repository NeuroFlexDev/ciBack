import pytest

from app.ai.token_forecast import predict


@pytest.mark.parametrize("coefficients", [[1, 2], [1, 2, 3, 4]])
def test_corrupt_forecast_coefficients_are_not_silently_truncated(coefficients):
    model = {
        "models": {
            "chat|example": {
                "input_per_byte": 0.5,
                "output_coefficients": coefficients,
            }
        }
    }
    with pytest.raises(ValueError, match="zip"):
        predict(
            model,
            role="chat",
            model_id="example",
            request_features={"prompt_bytes": 1000, "units": 1},
            max_output_tokens=100,
        )
