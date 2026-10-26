import pytest

from settings import ConfigError, load_settings

SECRET = "s3cr3t-pa55word"
DSN = f"postgresql://sippolusi:{SECRET}@postgres.railway.internal:5432/railway"


def problems(env):
    with pytest.raises(ConfigError) as info:
        load_settings(env)
    return str(info.value)


def test_defaults_without_any_variable_are_local_development():
    settings = load_settings({})
    assert (settings.environment, settings.port, settings.database_url) == ("development", 8000, None)


def test_railway_environment_name_is_used_when_environment_is_unset():
    assert load_settings({"RAILWAY_ENVIRONMENT_NAME": "development"}).environment == "development"
    assert load_settings({"RAILWAY_ENVIRONMENT_NAME": "production", "DATABASE_URL": DSN}).environment == "production"
    # Any other Railway environment (e.g. a PR preview) is a shared deployment.
    assert load_settings({"RAILWAY_ENVIRONMENT_NAME": "pr-41", "DATABASE_URL": DSN}).environment == "staging"


def test_existing_env_examples_value_dev_is_accepted():
    assert load_settings({"ENVIRONMENT": "DEV"}).environment == "development"


def test_database_url_is_required_outside_development():
    message = problems({"ENVIRONMENT": "staging"})
    assert "DATABASE_URL is required in staging" in message
    assert "${{database.DATABASE_URL}}" in message


@pytest.mark.parametrize(
    "dsn, expected",
    [
        (f"mysql://u:{SECRET}@h:3306/db", "scheme 'mysql'"),
        (f"postgresql://u:{SECRET}@h:5432", "missing the host or database name"),
        (f"postgresql://u:{SECRET}@h:port/db", "could not be parsed"),
    ],
)
def test_invalid_database_url_is_explained_without_echoing_it(dsn, expected):
    message = problems({"DATABASE_URL": dsn})
    assert expected in message
    assert SECRET not in message


def test_all_problems_are_reported_at_once():
    message = problems({"ENVIRONMENT": "qa", "PORT": "eighty", "LOG_LEVEL": "loud"})
    assert "ENVIRONMENT='qa'" in message and "PORT='eighty'" in message and "LOG_LEVEL='LOUD'" in message


def test_summary_names_the_database_without_credentials():
    summary = load_settings({"DATABASE_URL": DSN, "PORT": "8080"}).summary()
    assert "postgres.railway.internal:5432/railway" in summary
    assert SECRET not in summary and "sippolusi" not in summary
