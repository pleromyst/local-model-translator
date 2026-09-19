import pytest

from locallens.translation_policy import (
    is_mixed_chinese_english,
    translation_target_for_source,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is an English sentence.", False),
        ("这是一句中文。", False),
        ("版本 2.0", False),
        ("Windows 11 系统设置", True),
        ("请打开 café menu", True),
        ("English\n下一行是中文", True),
        ("123 / C:\\Temp", False),
        ("", False),
    ],
)
def test_mixed_chinese_english_detection(text, expected):
    assert is_mixed_chinese_english(text) is expected


def test_mixed_language_detection_rejects_non_string_values():
    with pytest.raises(TypeError, match="string"):
        is_mixed_chinese_english(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This is a test.", "Simplified Chinese"),
        ("This is a test.\n\n", "Simplified Chinese"),
        ("这是一个测试。", "English"),
        ("这是一个测试。\n\n", "English"),
        ("Windows 系统", None),
        ("12345", "Configured Target"),
    ],
)
def test_translation_direction_is_stable_across_trailing_blank_lines(text, expected):
    assert (
        translation_target_for_source(text, fallback="Configured Target") == expected
    )
