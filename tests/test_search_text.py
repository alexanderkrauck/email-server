"""The query expression and the index expression must not drift apart.

If they do, nothing fails: PostgreSQL just stops using the index and every search
turns into a sequential scan over every stored message body. That is exactly the
kind of regression a test suite has to catch, because production will not.
"""

import re

import pytest
from sqlalchemy.dialects import postgresql

from src.config import settings
from src.services.search_text import (
    ATTACHMENT_DOCUMENT_SQL,
    MESSAGE_DOCUMENT_SQL,
    attachment_document,
    index_expression,
    index_name,
    match_condition,
    message_document,
    search_vector,
    text_search_configs,
)


def _canonical(sql: str) -> str:
    """Compare what PostgreSQL parses, not how it was written.

    Table qualification, whitespace and redundant grouping parentheses are all
    invisible to the planner: ``a || b || c`` and ``(a || b) || c`` are the same
    left-associative tree. Removing them from both sides keeps this test about
    real drift instead of SQLAlchemy's rendering habits.
    """
    sql = sql.replace("email_logs.", "").replace("email_attachments.", "")
    return re.sub(r"\s+", " ", sql.replace("(", "").replace(")", "")).strip()


def _sql(expression) -> str:
    """Render an expression the way PostgreSQL will see it."""
    rendered = str(
        expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    rendered = rendered.replace("email_logs.", "").replace("email_attachments.", "")
    return re.sub(r"\s+", " ", rendered).strip()


@pytest.fixture
def default_configs(monkeypatch):
    monkeypatch.setattr(settings, "search_text_configs", ["simple", "english", "german"])


def test_simple_is_always_present_and_first(monkeypatch):
    """match="exact" is defined as searching the simple vector alone."""
    monkeypatch.setattr(settings, "search_text_configs", ["german"])

    assert text_search_configs() == ["simple", "german"]


def test_configs_are_deduplicated(monkeypatch):
    monkeypatch.setattr(settings, "search_text_configs", ["english", "simple", "english"])

    assert text_search_configs() == ["simple", "english"]


def test_the_message_index_expression_matches_the_query(default_configs):
    configs = text_search_configs()

    assert _canonical(_sql(search_vector(message_document()))) == _canonical(
        index_expression(MESSAGE_DOCUMENT_SQL, configs)
    )


def test_the_attachment_index_expression_matches_the_query(default_configs):
    configs = text_search_configs()

    assert _canonical(_sql(search_vector(attachment_document()))) == _canonical(
        index_expression(ATTACHMENT_DOCUMENT_SQL, configs)
    )


def test_exact_match_uses_the_simple_vector_alone(default_configs):
    """Otherwise a stemmer would widen an order number or a surname."""
    rendered = _sql(match_condition(message_document(), "Berger", match="exact"))

    assert "'english'" not in rendered
    assert "'german'" not in rendered
    assert "websearch_to_tsquery('simple', 'Berger')" in rendered


def test_stemmed_match_ors_every_configured_language(default_configs):
    rendered = _sql(match_condition(message_document(), "invoices", match="stemmed"))

    for config in ("simple", "english", "german"):
        assert f"websearch_to_tsquery('{config}', 'invoices')" in rendered


def test_index_names_record_their_languages():
    assert index_name("ix_fts", ["simple", "english"]) == "ix_fts_simple_english"


def test_index_names_stay_within_the_postgres_identifier_limit():
    name = index_name("ix_email_logs_search_fts", ["simple"] + [f"lang{n}" for n in range(20)])

    assert len(name) == 63
