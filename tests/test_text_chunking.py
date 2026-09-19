import pytest

from locallens.text_chunking import split_text, translatable_parts


def assert_lossless_and_bounded(text, max_chars):
    chunks = split_text(text, max_chars)
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= max_chars for chunk in chunks)
    return chunks


def test_short_text_is_not_split():
    assert split_text("Short text.", 100) == ["Short text."]
    assert split_text("", 100) == []


def test_multiple_paragraphs_split_at_blank_line():
    text = "First paragraph.\n\nSecond paragraph."

    chunks = assert_lossless_and_bounded(text, 20)

    assert chunks == ["First paragraph.\n\n", "Second paragraph."]


def test_long_paragraph_prefers_sentence_boundaries():
    text = "First sentence. Second sentence. Third."

    chunks = assert_lossless_and_bounded(text, 20)

    assert chunks == ["First sentence. ", "Second sentence. ", "Third."]


def test_chinese_sentences_split_without_spaces():
    text = "这是第一句。这是第二句。这是第三句。"

    chunks = assert_lossless_and_bounded(text, 12)

    assert chunks == ["这是第一句。这是第二句。", "这是第三句。"]


def test_single_oversized_paragraph_falls_back_to_tokens_then_characters():
    tokenized = "alpha beta gamma delta"
    unbroken = "x" * 25

    token_chunks = assert_lossless_and_bounded(tokenized, 10)
    character_chunks = assert_lossless_and_bounded(unbroken, 10)

    assert all(word in "".join(token_chunks) for word in tokenized.split())
    assert [len(chunk) for chunk in character_chunks] == [10, 10, 5]


def test_mixed_chinese_english_text_is_split_losslessly():
    text = "第一段 includes English words.\n\nSecond paragraph 包含中文。"

    assert_lossless_and_bounded(text, 24)


def test_url_and_file_path_are_kept_whole_when_each_fits():
    url = "https://example.com/a/very-long-path?q=1"
    path = r"C:\Users\sunny\Documents\LocalLens\main.py"
    text = f"Read {url} now.\nOpen {path} next."

    chunks = assert_lossless_and_bounded(text, 48)

    assert any(url in chunk for chunk in chunks)
    assert any(path in chunk for chunk in chunks)


def test_code_lines_are_not_split_when_each_line_fits():
    lines = [
        "```python\n",
        "value = translate(source_text)\n",
        "print(value)\n",
        "```",
    ]
    text = "".join(lines)

    chunks = assert_lossless_and_bounded(text, 36)

    assert all(any(line in chunk for chunk in chunks) for line in lines)


def test_outer_whitespace_is_separated_for_lossless_reassembly():
    assert translatable_parts("  source text\n\n") == (
        "  ",
        "source text",
        "\n\n",
    )
    assert translatable_parts(" \n ") == (" \n ", "", "")


@pytest.mark.parametrize("max_chars", [0, -1, True, 1.5])
def test_invalid_chunk_size_is_rejected(max_chars):
    with pytest.raises(ValueError, match="positive integer"):
        split_text("text", max_chars)  # type: ignore[arg-type]


def test_non_string_text_is_rejected():
    with pytest.raises(TypeError, match="string"):
        split_text(None, 10)  # type: ignore[arg-type]
