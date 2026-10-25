import logging

import pytest

from budgetguard import redact


@pytest.mark.parametrize(("line", "secret"), [
    ("Authorization: Bearer ghs_abc123", "ghs_abc123"),
    ("headers={'authorization': 'Bearer dashtoken', 'accept': 'x'}", "dashtoken"),
    ("X-GitHub-User-Token=gho_secret", "gho_secret"),
    ("x-hub-signature-256: sha256=deadbeef", "deadbeef"),
    ('"X-BG-Fault-Token": "fault-tok"', "fault-tok"),
    ("Cookie: better-auth.session=xyz; other=1", "xyz"),
    ('127.0.0.1 - "GET /validate?t=demo-token HTTP/1.1" 200', "demo-token"),
    ("HTTP Request: GET https://github.com/login/oauth?code=oauthcode&state=s", "oauthcode"),
    ("GET /x?a=1&token=tok-9 200", "tok-9"),
])
def test_redacts_credentials(line: str, secret: str) -> None:
    out = redact.redact(line)
    assert secret not in out and redact.MASK in out


def test_leaves_ordinary_text_alone() -> None:
    line = "decision 4812 orders-service block OVER_BUDGET price 1.26% /v1/decisions?run_id=777"
    assert redact.redact(line) == line


def test_factory_redacts_formatted_args(caplog: pytest.LogCaptureFixture) -> None:
    redact.install()
    redact.install()                                         # idempotent
    with caplog.at_level(logging.INFO):
        logging.getLogger("uvicorn.access").info('%s - "%s %s"', "10.0.0.1", "GET", "/x?token=s3cret")
    assert "s3cret" not in caplog.text and "token=[REDACTED]" in caplog.text
