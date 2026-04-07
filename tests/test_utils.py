from __future__ import annotations

import pytest
import typer

from homectl.utils import hostname_to_safe_name, validate_bare_domain, validate_hostname


def test_hostname_to_safe_name() -> None:
    assert hostname_to_safe_name("test.example.com") == "test-example-com"


def test_validate_hostname_accepts_valid_hostname() -> None:
    assert validate_hostname("notes.example.com") == "notes.example.com"


def test_validate_hostname_rejects_scheme() -> None:
    with pytest.raises(typer.BadParameter):
        validate_hostname("https://example.com")


def test_validate_bare_domain_accepts_domain() -> None:
    assert validate_bare_domain("example.com") == "example.com"


def test_validate_bare_domain_rejects_subdomain() -> None:
    with pytest.raises(typer.BadParameter):
        validate_bare_domain("notes.example.com")
