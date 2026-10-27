import json
import logging

from log_config import (
    RedactingFormatter,
    configure_logging,
    correlation,
    current_correlation_id,
    log_event,
    new_correlation_id,
    redact,
    safe_correlation_id,
    secret_values,
)

SECRET = "s3cr3t-pa55word"


def record(message, *args, level=logging.INFO, exc_info=None):
    return logging.LogRecord("backend", level, __file__, 1, message, args, exc_info)


def test_password_in_url_is_masked_but_host_kept():
    text = redact(f"connect to postgresql://sippolusi:{SECRET}@postgres.railway.internal:5432/railway failed")
    assert SECRET not in text
    assert "postgresql://sippolusi:***@postgres.railway.internal:5432/railway" in text


def test_key_value_dsn_and_query_string_secrets_are_masked():
    text = redact(f"invalid dsn: host=db password={SECRET} dbname=x; url ?token={SECRET}&a=1")
    assert SECRET not in text
    assert "password=***" in text and "token=***" in text


def test_secret_env_values_are_masked_verbatim():
    env = {"AWS_SECRET_ACCESS_KEY": SECRET, "MLFLOW_TRACKING_URI": "http://model_registry:5000", "PORT": "8000"}
    assert secret_values(env) == [SECRET]
    assert redact(f"S3 said {SECRET} is wrong", secret_values(env)) == "S3 said *** is wrong"


def test_traceback_is_redacted_too():
    try:
        raise ValueError(f"bad dsn postgresql://u:{SECRET}@h/db")
    except ValueError:
        import sys

        formatted = RedactingFormatter(as_json=False).format(record("failed", exc_info=sys.exc_info()))
    assert "Traceback" in formatted and SECRET not in formatted


def test_json_lines_carry_a_railway_level():
    line = RedactingFormatter(as_json=True).format(record("disk %s", "low", level=logging.WARNING))
    payload = json.loads(line)
    assert payload["level"] == "warn" and payload["message"] == "disk low" and payload["logger"] == "backend"


def test_railway_defaults_to_json_on_stdout(capsys):
    configure_logging({"RAILWAY_ENVIRONMENT_NAME": "development", "DATABASE_URL": f"postgresql://u:{SECRET}@h/db"})
    logging.getLogger("backend").info("dsn is postgresql://u:%s@h/db", SECRET)
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["level"] == "info"
    assert SECRET not in out


def test_records_inside_a_correlation_carry_its_id():
    formatter = RedactingFormatter(as_json=True, service="backend")
    assert current_correlation_id() is None
    with correlation("req-123"):
        inside = json.loads(formatter.format(record("inside")))
        text = RedactingFormatter(as_json=False).format(record("inside"))
    outside = json.loads(formatter.format(record("outside")))
    assert inside["correlation_id"] == "req-123" and inside["service"] == "backend"
    assert "[req-123]" in text
    assert "correlation_id" not in outside and current_correlation_id() is None


def test_events_are_top_level_fields_with_secrets_masked(capsys):
    configure_logging({"LOG_FORMAT": "json", "DATABASE_URL": f"postgresql://u:{SECRET}@h/db"}, service="data_worker")
    log_event(logging.getLogger("data_worker"), "job_finished", logging.WARNING, status="partial", duration_s=1.5,
              failed_steps=None, api_token="plain-token", note=f"dsn postgresql://u:{SECRET}@h/db",
              message="must not replace the message")
    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "job_finished" and payload["level"] == "warn" and payload["service"] == "data_worker"
    assert payload["status"] == "partial" and payload["duration_s"] == 1.5
    assert "failed_steps" not in payload
    assert payload["api_token"] == "***"
    assert SECRET not in json.dumps(payload) and "plain-token" not in json.dumps(payload)
    assert payload["message"].startswith("job_finished status=partial")


def test_only_safe_incoming_ids_are_adopted():
    assert safe_correlation_id("3f1c2a9e-0b1d-4c55-9a9e-1d2c3b4a5f60") == "3f1c2a9e-0b1d-4c55-9a9e-1d2c3b4a5f60"
    assert safe_correlation_id("gw:abc.123_x") == "gw:abc.123_x"
    for unsafe in (None, "", "a b", "x\n{\"level\":\"error\"}", "a" * 129):
        assert safe_correlation_id(unsafe) is None
    assert new_correlation_id("cycle-").startswith("cycle-")
