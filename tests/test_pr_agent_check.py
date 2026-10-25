from pr_agent.check import unverified_numbers

TOOL_RESULTS = [
    {
        "current": {"head_total": "398.29", "changed_total": "398.29", "delta": "0.00"},
        "alternatives": [
            {"changes": [], "result": {"changed_total": "167.46", "delta": "-230.83"}},
        ],
    }
]


def test_unverified_numbers_is_empty_when_all_numbers_come_from_the_tool() -> None:
    # Arrange
    text = "Downscaling saves 230.83 USD, to 167.46 USD a month (now 398.29)."

    # Act
    found = unverified_numbers(text, TOOL_RESULTS)

    # Assert
    assert found == []


def test_unverified_numbers_lists_numbers_missing_from_tool_results() -> None:
    # Arrange
    text = "The third option costs 199.14 USD, a delta of -199.14 USD; the first saves 230.83."

    # Act
    found = unverified_numbers(text, TOOL_RESULTS)

    # Assert
    assert found == ["199.14", "-199.14"]


def test_unverified_numbers_ignores_sign_and_thousands_separator() -> None:
    # Arrange
    results = [{"current": {"head_total": "1234.50"}, "alternatives": []}]

    # Act
    found = unverified_numbers("Now 1,234.50 USD, or +1234.50 or -1,234.50.", results)

    # Assert
    assert found == []


from pr_agent.check import annotate  # noqa: E402


def test_annotate_appends_note_and_keeps_text() -> None:
    # Arrange
    text = "The third option costs 199.14 USD."

    # Act
    annotated = annotate(text, ["199.14"])

    # Assert
    assert annotated.startswith(text)
    assert "Numbers not found in the tool results: 199.14" in annotated


def test_unverified_numbers_catches_dollar_and_usd_amounts() -> None:
    # Arrange
    results = [{"current": {"head_total": "32.00"}, "alternatives": []}]
    text = "A NAT gateway is about $32/month; the total is ~$112/month, or 45 USD more."

    # Act
    found = unverified_numbers(text, results)

    # Assert
    assert found == ["~$112", "45 USD"]


from pr_agent.check import MAX_AGENT_TEXT, sanitize  # noqa: E402


def test_sanitize_disables_links_images_html_urls_and_mentions() -> None:
    # Arrange
    text = (
        "See [the estimate](https://evil.example/login) and ![x](https://evil.example/p.png).\n"
        "<details>hidden</details> Visit www.evil.example or http://evil.example now,"
        " @maintainer.\n"
        "[ref]: https://evil.example"
    )

    # Act
    cleaned = sanitize(text)

    # Assert
    assert "evil.example" not in cleaned
    assert "<details>" not in cleaned
    # メンションはコードの扱いにして通知を飛ばさない
    assert "`@maintainer`" in cleaned
    assert " @maintainer" not in cleaned
    assert "the estimate" in cleaned


def test_sanitize_truncates_long_text() -> None:
    # Act
    cleaned = sanitize("a" * (MAX_AGENT_TEXT + 100))

    # Assert
    assert len(cleaned) <= MAX_AGENT_TEXT + len("\n\n(truncated)")
    assert cleaned.endswith("(truncated)")


def test_sanitize_removes_markdown_tables_written_by_the_agent() -> None:
    # Arrange
    table = "| Resource | Before |\n| --- | --- |\n| Web | $307.77 |\n"
    text = f"Alternative 1 is cheaper.\n\n{table}\nDone."

    # Act
    cleaned = sanitize(text)

    # Assert
    assert "|" not in cleaned
    assert "Alternative 1 is cheaper." in cleaned
    assert "Done." in cleaned


def test_unverified_numbers_checks_four_decimal_amounts() -> None:
    results = [{"current": {"changed_total": "0.0154"}, "alternatives": []}]
    text = "It costs 0.0154 per month now and about 0.0150 with 1024 MB."

    assert unverified_numbers(text, results) == ["0.0150"]


def test_unverified_numbers_ignores_numbers_with_units_that_are_not_money() -> None:
    text = "It takes about 0.57 minutes, uses 12.50 MB and leaves 23.40% free. It costs 0.0215."
    results = [{"current": {"changed_total": "0.0215"}}]

    assert unverified_numbers(text, results) == []


def test_unverified_numbers_ignores_hours() -> None:
    assert unverified_numbers("It takes 0.57 hours.", []) == []
