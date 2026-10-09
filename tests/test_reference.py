from pathlib import Path

from hill.reference import reference


def test_palaces_reference_is_current_and_all_documented():
    text, gaps = reference()
    assert gaps == []
    assert (Path(__file__).parents[1] / "docs" / "reference.md").read_text() == text, \
        "run: uv run python -m hill.reference > docs/reference.md"
