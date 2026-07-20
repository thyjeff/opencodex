from opencodex_proxy.tui import create_custom_model, merge_fetched_models, model_id


def test_create_custom_model_marks_the_model_and_uses_the_default_context() -> None:
    model = create_custom_model("private-model")

    assert model_id(model) == "private-model"
    assert model["custom"] is True
    assert model["enabled"] is True
    assert model["context_length"] == 128000


def test_fetch_merge_retains_custom_models_missing_from_the_provider_response() -> None:
    custom = create_custom_model("private-model", 32000)
    discovered = {"id": "public-model", "enabled": True}

    merged = merge_fetched_models([custom], [discovered])

    assert [model_id(model) for model in merged] == ["public-model", "private-model"]


def test_fetch_merge_does_not_duplicate_custom_models_discovered_by_the_provider() -> None:
    custom = create_custom_model("shared-model")
    discovered = {"id": "shared-model", "enabled": True}

    assert merge_fetched_models([custom], [discovered]) == [discovered]
