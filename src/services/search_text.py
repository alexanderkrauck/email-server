"""Full-text expressions shared by search and by the indexes that back it.

PostgreSQL text search stems against exactly one language. A mailbox does not:
the same inbox holds German invoices and English newsletters, and no single
configuration reads both. ``simple`` — the configuration this project started
with — stems nothing at all, so ``invoice`` returned 237 messages and
``invoices`` returned none.

The fix is to index the same text under several configurations at once and take
the union. Identical lexemes collapse, so a German/English union costs far less
than three separate indexes, and a query is matched against the union with an
OR of the same configurations.

Two consequences worth knowing:

* A negated term is evaluated per configuration and OR-ed with the rest, so an
  exclusion can under-exclude a stem-only match. Over-inclusion is the safe
  direction for a tool whose job is to not lose mail.
* Stemming widens ``Berger`` to ``Berg``. Identifiers and surnames therefore
  want ``match="exact"``, which uses the ``simple`` vector alone.

The index expressions in the migrations must stay identical to what
:func:`search_vector` builds, or PostgreSQL will plan a sequential scan over
every stored body.
"""

import re

from sqlalchemy import func, literal_column

from src.config import settings
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog

# Always first, always present: it is what makes match="exact" exact.
BASE_CONFIG = "simple"

# The same two documents, once as SQLAlchemy for the query and once as SQL text
# for CREATE INDEX. tests/test_search_text.py compiles the first and asserts it
# equals the second, because if they drift PostgreSQL silently stops using the
# index and every search becomes a sequential scan over every stored body.
MESSAGE_DOCUMENT_SQL = (
    "coalesce(sender, '') || ' ' || coalesce(recipient, '') || ' ' || "
    "coalesce(subject, '') || ' ' || coalesce(body_plain, '')"
)
ATTACHMENT_DOCUMENT_SQL = "coalesce(text_content, '')"


def message_document():
    """The searchable text of a message."""
    return concatenated(
        func.coalesce(EmailLog.sender, ""),
        func.coalesce(EmailLog.recipient, ""),
        func.coalesce(EmailLog.subject, ""),
        func.coalesce(EmailLog.body_plain, ""),
    )


def attachment_document():
    """The searchable text extracted from an attachment."""
    return func.coalesce(EmailAttachment.text_content, "")


_CONFIG_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def text_search_configs() -> list[str]:
    """The configured search configurations, with ``simple`` guaranteed first."""
    configured = [name for name in settings.search_text_configs if name]
    for name in configured:
        # These names are interpolated into SQL, both here and in CREATE INDEX.
        if not _CONFIG_NAME.match(name):
            raise ValueError(f"Invalid text search configuration name: {name!r}")
    ordered = [BASE_CONFIG] + [name for name in configured if name != BASE_CONFIG]
    return list(dict.fromkeys(ordered))


def _regconfig(name: str):
    """Render a configuration name as a literal, not a bound parameter.

    An index is only matched when the configuration is a constant in the plan.
    Rendering it literally makes that true regardless of how the driver handles
    parameters, and makes the compiled SQL comparable to the CREATE INDEX text.
    """
    return literal_column(f"'{name}'")


def concatenated(*parts):
    """Join column expressions into one searchable document."""
    document = parts[0]
    for part in parts[1:]:
        document = document + " " + part
    return document


def search_vector(document, configs: list[str] | None = None):
    """The union tsvector of a document across every configured language."""
    names = configs or text_search_configs()
    vector = func.to_tsvector(_regconfig(names[0]), document)
    for name in names[1:]:
        vector = vector.op("||")(func.to_tsvector(_regconfig(name), document))
    return vector


def search_query(query_text: str, configs: list[str] | None = None):
    """The OR of one parsed query per configured language."""
    names = configs or text_search_configs()
    parsed = func.websearch_to_tsquery(_regconfig(names[0]), query_text)
    for name in names[1:]:
        parsed = parsed.op("||")(func.websearch_to_tsquery(_regconfig(name), query_text))
    return parsed


def match_condition(document, query_text: str, *, match: str):
    """Full-text match over a document, stemmed across languages or verbatim."""
    if match == "exact":
        return func.to_tsvector(_regconfig(BASE_CONFIG), document).op("@@")(
            func.websearch_to_tsquery(_regconfig(BASE_CONFIG), query_text)
        )
    return search_vector(document).op("@@")(search_query(query_text))


def index_expression(document_sql: str, configs: list[str]) -> str:
    """The SQL text of the union tsvector, for CREATE INDEX in a migration."""
    return " || ".join(f"to_tsvector('{name}', {document_sql})" for name in configs)


def index_name(prefix: str, configs: list[str]) -> str:
    """A deterministic index name that records which languages it covers."""
    # PostgreSQL truncates identifiers at 63 bytes; do it here so the name the
    # migration creates and the name a later migration drops stay identical.
    return "_".join([prefix, *configs])[:63]
