from raft.redaction import contains_pii, redact_text, verify_literal_quote


def test_redaction_is_stable_and_removes_configured_pii() -> None:
    result = redact_text("Email maya@example.com, then call +31 6 1234 5678. maya@example.com knows.")
    assert result.text.count("[EMAIL_1]") == 2
    assert "[PHONE_1]" in result.text
    assert not contains_pii(result.text)


def test_quote_must_be_literal_substring() -> None:
    source = "The assistant suggested the Harbor Shell."
    assert verify_literal_quote("suggested the Harbor Shell", source)
    assert not verify_literal_quote("recommended the Harbor Shell", source)


def test_credentials_with_internal_separators_are_caught() -> None:
    # A key like this is the common shape; a pattern that stops at the first
    # hyphen silently ships the secret into every model prompt and quote.
    for secret in ("sk-live-9f2b71c4ad55e0", "token_abc123def456ghi", "secret-Zm9vYmFyYmF6cXV4"):
        result = redact_text(f"the value was {secret} yesterday")
        assert secret not in result.text
        assert "[SECRET_1]" in result.text


def test_ordinary_hyphenated_words_are_left_alone() -> None:
    for phrase in ("a well-known long-hyphenated-phrase", "the migration-guide-for-clients page"):
        assert redact_text(phrase).text == phrase


def test_quote_carrying_pii_is_rejected_even_if_literal() -> None:
    source = "Contact me on maya@example.com about the order."
    assert not verify_literal_quote("me on maya@example.com about", source)
