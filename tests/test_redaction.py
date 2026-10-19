"""Secret redaction for logs, transcripts and audit detail."""

from __future__ import annotations

import logging

from nexus_remote.security.redact import MASK, RedactingFilter, redact, redact_output


def test_redacts_key_value_secrets():
    assert "hunter2" not in redact("password=hunter2")
    assert MASK in redact("password=hunter2")
    assert "hunter2" not in redact('password: "hunter2"')
    assert "hunter2" not in redact("token = hunter2")


def test_redacts_cli_flags():
    cleaned = redact("sshpass -p hunter2 ssh deploy@host")
    assert "hunter2" not in cleaned
    cleaned = redact("tool --password hunter2 --verbose")
    assert "hunter2" not in cleaned


def test_redacts_authorization_headers():
    cleaned = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature")
    assert "eyJhbGci" not in cleaned
    assert MASK in cleaned


def test_redacts_private_key_blocks():
    text = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "AAAAB3NzaC1yc2EAAAADAQABAAABgQC\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    cleaned = redact(text)
    assert "AAAAB3NzaC1yc2E" not in cleaned
    assert MASK in cleaned


def test_redacts_cloud_and_api_key_shapes():
    assert "AKIA" not in redact("key AKIAIOSFODNN7EXAMPLE here")
    assert "sk-" not in redact("token sk-abcdefghijklmnopqrstuvwxyz123456")
    assert "ghp_" not in redact("ghp_" + "a" * 40)


def test_redacts_connection_string_passwords():
    cleaned = redact("postgres://admin:hunter2@db.internal/app")
    assert "hunter2" not in cleaned
    assert "postgres://admin:" in cleaned


def test_redact_output_masks_known_literal_secrets():
    cleaned = redact_output("value is hunter2 and again hunter2", extra_secrets=["hunter2"])
    assert "hunter2" not in cleaned
    assert cleaned.count(MASK) == 2


def test_redact_output_ignores_short_literals():
    assert redact_output("abc", ["ab"]) == "abc"


def test_logging_filter_rewrites_message(caplog):
    logger = logging.getLogger("nexus.test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("connecting with password=hunter2")
    assert "hunter2" not in caplog.text
    assert MASK in caplog.text


def test_redact_leaves_ordinary_text_untouched():
    message = "connected to 10.0.0.5:22 as deploy in 42 ms"
    assert redact(message) == message
