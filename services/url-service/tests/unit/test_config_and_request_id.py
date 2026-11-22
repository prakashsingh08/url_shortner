import pytest
from pydantic import ValidationError

from app.api.middleware import resolve_request_id
from app.config import Settings, describe_config_errors
from tests.markers import scenario


@scenario("service-health", "Missing required configuration")
@pytest.mark.parametrize("value", [None, ""])
def test_missing_db_url_is_reported_by_name(monkeypatch, value):
    monkeypatch.delenv("DB_PRIMARY_URL", raising=False)
    if value is not None:
        monkeypatch.setenv("DB_PRIMARY_URL", value)
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert describe_config_errors(exc.value) == ["DB_PRIMARY_URL: missing"]


def test_valid_configuration_loads(monkeypatch):
    monkeypatch.setenv("DB_PRIMARY_URL", "postgresql://u:p@h:5432/db")
    s = Settings()
    assert s.app_port == 8000 and s.db_pool_size_write == 10 and not s.test_routes_enabled


def test_well_formed_incoming_request_id_is_reused():
    assert resolve_request_id("0123456789abcdef0123456789abcdef") == (
        "0123456789abcdef0123456789abcdef"
    )


@scenario("api-conventions", "Invalid incoming ID replaced")
@pytest.mark.parametrize("bad", [None, "", "<script>", "short", "x" * 65, "has space here"])
def test_malformed_request_id_is_replaced(bad):
    new = resolve_request_id(bad)
    assert new != bad
    assert 8 <= len(new) <= 64 and new.isalnum()
