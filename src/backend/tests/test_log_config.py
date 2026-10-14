import json
import logging

from log_config import RedactingFormatter, configure_logging, redact, secret_values

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
