"""
Tests for SubBud.

Domain/scope tests run anywhere. Store tests run against every backend: the
SQLite backend uses a tmp file, the Redis backend needs a reachable server and
uses DB 15, which it flushes. Redis tests are skipped when no server answers.
Override with REDIS_HOST / REDIS_PORT / REDIS_TEST_DB.
"""

import os

import pytest

from subbud.domains import ScopeFilter, ScopePattern, normalize_domain
from subbud.main import Project
from subbud.stores import (
    DataStore,
    SqliteStore,
    meta_key,
    open_store,
    project_key,
    validate_project_name,
)

TEST_DB = int(os.getenv("REDIS_TEST_DB", "15"))


# --- domain normalization -------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("example.com", "example.com"),
        ("  Example.COM  ", "example.com"),
        ("HTTPS://C.Example.com/path?q=1", "c.example.com"),
        ("http://user:pass@a.example.com:8443/x", "a.example.com"),
        ("example.com.", "example.com"),
        ("*.example.com", "example.com"),
        ("example.com (FQDN) --> a_record", "example.com"),
        ("1.2.3.4", "1.2.3.4"),
        ("sub.deep.example.co.uk", "sub.deep.example.co.uk"),
    ],
)
def test_normalize_valid(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "# a comment",
        "localhost",           # single label
        "not a domain!",       # invalid characters after tokenizing
        "-bad.example.com",    # label starts with '-'
        "bad-.example.com",    # label ends with '-'
        "a" * 64 + ".com",     # label longer than 63
        ("a." * 130) + "com",  # name longer than 253
    ],
)
def test_normalize_invalid(raw):
    assert normalize_domain(raw) is None


# --- scope filtering ------------------------------------------------------

def test_scope_pattern_apex_and_subdomains():
    pattern = ScopePattern("Example.COM")
    assert pattern.matches("example.com")
    assert pattern.matches("a.b.example.com")
    assert not pattern.matches("notexample.com")
    assert not pattern.matches("example.com.evil.net")


def test_scope_pattern_wildcard_excludes_apex():
    pattern = ScopePattern("*.example.com")
    assert pattern.matches("a.example.com")
    assert not pattern.matches("example.com")


def test_scope_pattern_rejects_garbage():
    with pytest.raises(ValueError):
        ScopePattern("not a domain!")


def test_scope_empty_allows_everything():
    scope = ScopeFilter()
    assert scope.is_empty
    assert scope.allows("anything.example.com")


def test_scope_include_only():
    scope = ScopeFilter(include=["example.com"])
    assert scope.allows("a.example.com")
    assert not scope.allows("a.other.com")


def test_scope_exclude_wins_over_include():
    scope = ScopeFilter(include=["example.com"], exclude=["dev.example.com"])
    assert scope.allows("www.example.com")
    assert not scope.allows("dev.example.com")
    assert not scope.allows("a.dev.example.com")


def test_scope_from_files(tmp_path):
    include = tmp_path / "scope.txt"
    include.write_text("# program scope\nexample.com\n\n*.example.org\n")
    exclude = tmp_path / "out.txt"
    exclude.write_text("dev.example.com\n")

    scope = ScopeFilter.from_args(include_files=[str(include)], exclude_files=[str(exclude)])
    assert scope.allows("api.example.com")
    assert scope.allows("a.example.org")
    assert not scope.allows("example.org")       # wildcard pattern, apex not included
    assert not scope.allows("dev.example.com")
    assert not scope.allows("elsewhere.net")


# --- project names --------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "acme:meta"])
def test_validate_project_name_rejects(bad):
    with pytest.raises(ValueError):
        validate_project_name(bad)


def test_validate_project_name_accepts():
    assert validate_project_name("acme") == "acme"


# --- backends -------------------------------------------------------------

@pytest.fixture(params=["redis", "sqlite"])
def store(request, tmp_path):
    if request.param == "sqlite":
        backend = open_store(backend="sqlite", sqlite_path=str(tmp_path / "subbud.db"))
        yield backend
        backend.close()
        return

    try:
        backend = open_store(
            backend="redis",
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=TEST_DB,
        )
    except ConnectionError as exc:
        pytest.skip(f"Redis unavailable: {exc}")
    backend.r.flushdb()
    yield backend
    backend.r.flushdb()


@pytest.fixture
def redis_store(store):
    if not isinstance(store, DataStore):
        pytest.skip("Redis-specific behaviour")
    return store


def test_open_store_rejects_unknown_backend():
    with pytest.raises(ValueError):
        open_store(backend="postgres")


def test_add_count_and_delete(store):
    assert store.add_domains("proj", ["a.example.com", "b.example.com"]) == 2
    assert store.add_domains("proj", ["a.example.com"]) == 0
    assert store.get_domain_count("proj") == 2
    assert sorted(store.iter_domains("proj")) == ["a.example.com", "b.example.com"]
    assert store.delete_project("proj") == 1
    assert store.get_domain_count("proj") == 0


def test_delete_missing_project(store):
    assert store.delete_project("nope") == 0


def test_get_projects(store):
    store.add_domains("beta", ["a.example.com"])
    store.add_domains("alpha", ["b.example.com"])
    assert store.get_projects() == ["alpha", "beta"]


def test_remove_domains_reports_misses(store):
    store.add_domains("proj", ["a.example.com"])
    assert store.remove_domains("proj", ["a.example.com"]) == 1
    assert store.remove_domains("proj", ["nope.example.com"]) == 0


def test_describe_mentions_backend(store):
    assert store.name in store.describe()


# --- metadata -------------------------------------------------------------

def test_metadata_records_first_sighting(store):
    store.add_domains("proj", ["a.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")

    # A later sighting from another source must not overwrite the first one.
    store.add_domains("proj", ["a.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-02-02", "subfinder.txt")

    assert store.get_metadata("proj", "a.example.com") == {
        "first_seen": "2026-01-01",
        "source": "amass.txt",
    }


def test_metadata_absent_for_unknown_domain(store):
    store.add_domains("proj", ["a.example.com"])
    assert store.get_metadata("proj", "a.example.com") is None
    assert store.get_metadata("proj", "ghost.example.com") is None


def test_iter_domains_with_meta_fills_gaps(store):
    store.add_domains("proj", ["a.example.com", "b.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")

    rows = sorted(store.iter_domains_with_meta("proj"))
    assert rows == [
        ("a.example.com", "2026-01-01", "amass.txt"),
        ("b.example.com", None, None),
    ]


def test_metadata_removed_with_domain(store):
    store.add_domains("proj", ["a.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")
    store.remove_domains("proj", ["a.example.com"])
    assert store.get_metadata("proj", "a.example.com") is None


def test_metadata_removed_with_project(store):
    store.add_domains("proj", ["a.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")
    store.delete_project("proj")
    assert store.get_metadata("proj", "a.example.com") is None


def test_metadata_key_is_not_a_project(redis_store):
    redis_store.add_domains("proj", ["a.example.com"])
    redis_store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")
    assert redis_store.r.exists(meta_key("proj"))
    assert redis_store.get_projects() == ["proj"]


# --- Redis key layout and legacy migration --------------------------------

def test_keys_are_namespaced(redis_store):
    redis_store.add_domains("proj", ["a.example.com"])
    assert redis_store.r.exists(project_key("proj"))
    assert not redis_store.r.exists("proj")


def test_unrelated_keys_are_not_projects(redis_store):
    redis_store.add_domains("proj", ["a.example.com"])
    redis_store.r.set("some-other-app-key", "value")
    assert redis_store.get_projects() == ["proj"]


def test_legacy_migration(redis_store):
    redis_store.r.sadd("oldproj", "a.example.com")
    redis_store.r.set("not-a-set", "value")

    assert redis_store.find_legacy_projects() == ["oldproj"]
    assert redis_store.migrate_legacy_project("oldproj") is True
    assert redis_store.get_projects() == ["oldproj"]
    assert redis_store.find_legacy_projects() == []


def test_legacy_migration_does_not_overwrite(redis_store):
    redis_store.add_domains("proj", ["new.example.com"])
    redis_store.r.sadd("proj", "old.example.com")

    assert redis_store.migrate_legacy_project("proj") is False
    assert sorted(redis_store.iter_domains("proj")) == ["new.example.com"]


def test_sqlite_has_nothing_to_migrate(tmp_path):
    backend = SqliteStore(path=str(tmp_path / "s.db"))
    backend.add_domains("proj", ["a.example.com"])
    assert backend.find_legacy_projects() == []
    assert backend.migrate_legacy_project("proj") is False
    backend.close()


def test_sqlite_persists_across_sessions(tmp_path):
    path = str(tmp_path / "s.db")
    first = SqliteStore(path=path)
    first.add_domains("proj", ["a.example.com"])
    first.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")
    first.close()

    second = SqliteStore(path=path)
    assert second.get_projects() == ["proj"]
    assert second.get_metadata("proj", "a.example.com")["source"] == "amass.txt"
    second.close()


# --- Project (file ingestion and output) ----------------------------------

def test_add_from_file_normalizes_and_dedups(store, tmp_path, capsys):
    source = tmp_path / "domains.txt"
    source.write_text(
        "a.example.com\n"
        "A.EXAMPLE.COM\n"
        "https://b.example.com/path\n"
        "\n"
        "# comment\n"
        "localhost\n"
    )

    stats = Project(store, "proj").add_domains_from_file(str(source))
    out = capsys.readouterr().out

    assert sorted(store.iter_domains("proj")) == ["a.example.com", "b.example.com"]
    assert stats == {
        "total": 3, "added": 2, "duplicates": 1, "invalid": 1, "out_of_scope": 0
    }
    assert "Added 2 new domains" in out
    assert "Duplicates skipped: 1" in out
    assert "Ignored 1 lines" in out


def test_add_from_file_records_metadata(store, tmp_path):
    source = tmp_path / "amass.txt"
    source.write_text("a.example.com\n")

    Project(store, "proj").add_domains_from_file(str(source))
    record = store.get_metadata("proj", "a.example.com")
    assert record["source"] == "amass.txt"
    assert record["first_seen"]


def test_add_from_file_source_override(store, tmp_path):
    source = tmp_path / "domains.txt"
    source.write_text("a.example.com\n")

    Project(store, "proj").add_domains_from_file(str(source), source="crtsh")
    assert store.get_metadata("proj", "a.example.com")["source"] == "crtsh"


def test_add_from_file_applies_scope(store, tmp_path, capsys):
    source = tmp_path / "domains.txt"
    source.write_text(
        "www.example.com\n"
        "dev.example.com\n"
        "unrelated.other.com\n"
    )
    scope = ScopeFilter(include=["example.com"], exclude=["dev.example.com"])

    stats = Project(store, "proj").add_domains_from_file(str(source), scope=scope)

    assert sorted(store.iter_domains("proj")) == ["www.example.com"]
    assert stats["out_of_scope"] == 2
    assert "Ignored 2 domains outside the given scope" in capsys.readouterr().out


def test_add_from_file_all_out_of_scope(store, tmp_path):
    source = tmp_path / "domains.txt"
    source.write_text("a.other.com\n")
    scope = ScopeFilter(include=["example.com"])
    with pytest.raises(ValueError):
        Project(store, "proj").add_domains_from_file(str(source), scope=scope)


def test_add_from_file_raw_mode(store, tmp_path):
    source = tmp_path / "domains.txt"
    source.write_text("Not A Domain\n")
    Project(store, "proj").add_domains_from_file(str(source), normalize=False)
    assert sorted(store.iter_domains("proj")) == ["Not A Domain"]


def test_add_from_file_batches(store, tmp_path):
    source = tmp_path / "many.txt"
    source.write_text("".join(f"h{i}.example.com\n" for i in range(250)))

    stats = Project(store, "proj").add_domains_from_file(str(source), batch_size=10)
    assert stats["added"] == 250
    assert store.get_domain_count("proj") == 250
    assert store.get_metadata("proj", "h249.example.com") is not None


def test_add_from_file_rejects_missing_and_empty(store, tmp_path):
    with pytest.raises(FileNotFoundError):
        Project(store, "proj").add_domains_from_file(str(tmp_path / "nope.txt"))

    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n")
    with pytest.raises(ValueError):
        Project(store, "proj").add_domains_from_file(str(empty))


def test_save_domains_sorted(store, tmp_path):
    store.add_domains("proj", ["b.example.com", "a.example.com"])
    target = tmp_path / "out.txt"
    assert Project(store, "proj").save_domains(output=str(target), sort=True)
    assert target.read_text() == "a.example.com\nb.example.com\n"


def test_save_domains_with_meta(store, tmp_path):
    store.add_domains("proj", ["a.example.com", "b.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")

    target = tmp_path / "out.tsv"
    assert Project(store, "proj").save_domains(output=str(target), sort=True, meta=True)
    assert target.read_text() == (
        "domain\tfirst_seen\tsource\n"
        "a.example.com\t2026-01-01\tamass.txt\n"
        "b.example.com\t-\t-\n"
    )


def test_save_domains_empty_project(store, tmp_path):
    target = tmp_path / "out.txt"
    assert Project(store, "empty").save_domains(output=str(target)) is False
    assert not target.exists()


def test_print_domains_with_meta(store, capsys):
    store.add_domains("proj", ["a.example.com"])
    store.record_metadata("proj", ["a.example.com"], "2026-01-01", "amass.txt")

    assert Project(store, "proj").print_domains(meta=True)
    out = capsys.readouterr().out
    assert "domain\tfirst_seen\tsource" in out
    assert "a.example.com\t2026-01-01\tamass.txt" in out


def test_project_rejects_reserved_name(store):
    with pytest.raises(ValueError):
        Project(store, "acme:meta")
