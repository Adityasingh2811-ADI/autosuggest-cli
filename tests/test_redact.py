"""Tests for secret redaction."""

import pytest

from autosuggest.redact import redact


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("mysql -u root -p hunter2", "mysql -u root -p ***"),
        ("login --password=hunter2", "login --password=***"),
        ("api --token abcd1234efgh", "api --token ***"),
        ("svc --api-key=XYZ", "svc --api-key=***"),
        ('curl -H "Authorization: Bearer ghp_aaaaaaaaaaaaaaaaaaaaaa"',
         'curl -H "Authorization: Bearer ***"'),
        ("export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "export AWS_SECRET_ACCESS_KEY=***"),
        ("git clone https://user:pass@github.com/x.git",
         "git clone https://user:***@github.com/x.git"),
        ("echo sk-ABCDEFGHIJKLMNOPQRSTUV", "echo ***"),
        ("aws AKIAABCDEFGHIJKLMNOP", "aws ***"),
    ],
)
def test_mask_cases(raw, expected):
    assert redact(raw) == expected


def test_plain_command_untouched():
    assert redact("git status") == "git status"
    assert redact("make -j8") == "make -j8"


def test_denylist_drops_whole_command():
    assert redact("p4 -u me -P secrettoken sync") == ""
    assert redact("curl -u admin:pw https://x") == ""


def test_empty_is_noop():
    assert redact("") == ""
