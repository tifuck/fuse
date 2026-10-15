"""scripts/check_schema.py: promote-dev.sh refuses code whose columns fuse_live lacks.

On 2026-10-15 a promote shipped code reading user_projects.default_permissions
before its migration reached fuse_live, and every /chat/sync failed.
"""
from app.config import settings
from scripts import check_schema

SCHEMA = check_schema.expected_columns(check_schema.SCHEMA_SQL.read_text(encoding="utf-8"))


def _live(expected, *drop):
    actual = {table: set(columns) for table, columns in expected.items()}
    for name in drop:
        table, _, column = name.partition(".")
        if column:
            actual[table].discard(column)
        else:
            del actual[table]
    return actual


def test_reads_every_table_and_its_columns_from_schema_sql():
    text = check_schema.SCHEMA_SQL.read_text(encoding="utf-8")
    assert len(SCHEMA) == text.count("\nCREATE TABLE ")
    assert SCHEMA["user_projects"][0] == "id"
    assert "default_permissions" in SCHEMA["user_projects"]
    # Keys and constraints are not columns.
    assert not {"PRIMARY", "KEY", "UNIQUE", "CONSTRAINT"} & {c for cols in SCHEMA.values() for c in cols}


def test_the_2026_10_15_promote_would_have_been_refused():
    actual = _live(SCHEMA, "user_projects.default_permissions",
                   "user_agent_settings.default_chat_permissions",
                   "user_agent_settings.default_project_permissions")
    assert check_schema.missing(SCHEMA, actual) == [
        "user_agent_settings.default_chat_permissions",
        "user_agent_settings.default_project_permissions",
        "user_projects.default_permissions",
    ]


def test_missing_table_is_named_once_and_extras_are_fine():
    actual = _live(SCHEMA, "artifacts")
    actual["bug_reports"] = {"id"}
    actual["user_projects"].add("dropped_later")
    assert check_schema.missing(SCHEMA, actual) == ["artifacts"]
    assert check_schema.missing(SCHEMA, _live(SCHEMA)) == []


def test_main_exits_1_and_names_what_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(settings, "database_url", "mysql://u:p@db/fuse_example")
    monkeypatch.setattr(check_schema, "live_columns", lambda url: _live(SCHEMA, "user_projects.default_permissions"))
    assert check_schema.main([]) == 1
    err = capsys.readouterr().err
    assert "'fuse_example' is missing 1" in err
    assert "  user_projects.default_permissions\n" in err

    monkeypatch.setattr(check_schema, "live_columns", lambda url: _live(SCHEMA))
    assert check_schema.main([str(check_schema.SCHEMA_SQL)]) == 0
    assert f"has all {len(SCHEMA)} tables" in capsys.readouterr().out
