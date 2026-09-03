"""
Storage backends for SubBud.

Two interchangeable implementations sit behind the same interface:

  * :class:`DataStore`   - Redis, the default.
  * :class:`SqliteStore` - a single local file, for users who do not want to
    run a Redis server.

Both store a set of domains per project plus optional per-domain metadata
(when the domain was first seen, and which input it came from).
"""

import json
import os
import sqlite3
from itertools import islice

import redis

# All SubBud data lives under this prefix so SubBud never touches, lists or
# deletes keys that belong to another application sharing the same Redis DB.
KEY_PREFIX = 'subbud:'

# Suffix of the companion hash holding per-domain metadata.
META_SUFFIX = ':meta'

# Domains per round trip when reading or writing in bulk.
CHUNK_SIZE = 500

DEFAULT_SQLITE_PATH = os.path.join('~', '.subbud', 'subbud.db')


def project_key(project):
    """Return the Redis key that stores the domain set for `project`."""
    return f'{KEY_PREFIX}{project}'


def meta_key(project):
    """Return the Redis key that stores per-domain metadata for `project`."""
    return f'{KEY_PREFIX}{project}{META_SUFFIX}'


def validate_project_name(project):
    """
    Reject project names that would collide with SubBud's own key layout.

    Raises ValueError; returns the name unchanged when it is usable.
    """
    if not project or not project.strip():
        raise ValueError("❌ Project name cannot be empty.")
    if project.endswith(META_SUFFIX):
        raise ValueError(f"❌ Project names cannot end in '{META_SUFFIX}'.")
    return project


def _chunked(iterable, size):
    iterator = iter(iterable)
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


class DataStore:
    """Redis-backed store. Keys are ``subbud:<project>``."""

    name = 'redis'

    def __init__(self, host='localhost', port=6379, db=0, use_ssl=False, password=None):
        self.host = host
        self.port = port
        self.db = db
        self.use_ssl = use_ssl
        self.password = password
        self._connect()

    def _connect(self):
        kwargs = {
            'host': self.host,
            'port': self.port,
            'db': self.db,
            'password': self.password,
            'decode_responses': True,
            'socket_connect_timeout': 5,
        }
        if self.use_ssl:
            kwargs.update(ssl=True, ssl_cert_reqs=None)

        self.r = redis.Redis(**kwargs)
        try:
            self.r.ping()
        except redis.exceptions.RedisError as exc:
            raise ConnectionError(
                f"❌ Cannot reach Redis at {self.host}:{self.port} (db {self.db}): {exc}"
            ) from exc

    def describe(self):
        return (
            f"redis host={self.host} port={self.port} db={self.db} "
            f"ssl={'on' if self.use_ssl else 'off'}"
        )

    # --- domains ---------------------------------------------------------

    def add_domains(self, project, domains):
        """Add domains to a project. Returns how many were not already present."""
        if not domains:
            return 0
        return self.r.sadd(project_key(project), *domains)

    def remove_domains(self, project, domains):
        """Remove domains and their metadata. Returns how many were removed."""
        if not domains:
            return 0
        pipeline = self.r.pipeline()
        pipeline.srem(project_key(project), *domains)
        pipeline.hdel(meta_key(project), *domains)
        return pipeline.execute()[0]

    def iter_domains(self, project):
        """Iterate a project's domains via SSCAN, without materializing the set."""
        return self.r.sscan_iter(project_key(project))

    def get_domain_count(self, project):
        """Number of domains in a project, without fetching them."""
        return self.r.scard(project_key(project))

    def get_projects(self):
        """Sorted project names, found via SCAN over the SubBud prefix."""
        offset = len(KEY_PREFIX)
        return sorted(
            key[offset:]
            for key in self.r.scan_iter(match=f'{KEY_PREFIX}*')
            if not key.endswith(META_SUFFIX)
        )

    def delete_project(self, project):
        """Delete a project's domains and metadata. Returns 1 if it existed."""
        pipeline = self.r.pipeline()
        pipeline.delete(project_key(project))
        pipeline.delete(meta_key(project))
        return pipeline.execute()[0]

    # --- metadata --------------------------------------------------------

    def record_metadata(self, project, domains, first_seen, source):
        """
        Record first-seen date and source for domains that have no metadata yet.

        Existing entries are left alone, so metadata always describes where a
        domain was seen *first*.
        """
        if not domains:
            return 0
        payload = json.dumps({'first_seen': first_seen, 'source': source})
        key = meta_key(project)
        written = 0
        for chunk in _chunked(domains, CHUNK_SIZE):
            pipeline = self.r.pipeline()
            for domain in chunk:
                pipeline.hsetnx(key, domain, payload)
            written += sum(pipeline.execute())
        return written

    def get_metadata(self, project, domain):
        """Return {'first_seen': ..., 'source': ...} for a domain, or None."""
        raw = self.r.hget(meta_key(project), domain)
        return json.loads(raw) if raw else None

    def iter_domains_with_meta(self, project):
        """
        Yield (domain, first_seen, source) for every domain, streaming in
        chunks so metadata is never fully materialized. Missing values are None.
        """
        key = meta_key(project)
        for chunk in _chunked(self.iter_domains(project), CHUNK_SIZE):
            pipeline = self.r.pipeline()
            for domain in chunk:
                pipeline.hget(key, domain)
            for domain, raw in zip(chunk, pipeline.execute()):
                record = json.loads(raw) if raw else {}
                yield domain, record.get('first_seen'), record.get('source')

    # --- legacy key migration (Redis only) -------------------------------

    def find_legacy_projects(self):
        """
        Sorted list of unprefixed set keys, i.e. projects written by SubBud
        <= 0.0.2 before keys were namespaced. Only sets are reported, so
        unrelated keys in a shared DB are never mistaken for projects.
        """
        legacy = []
        for key in self.r.scan_iter(match='*'):
            if key.startswith(KEY_PREFIX) or key.startswith('_'):
                continue
            if self.r.type(key) == 'set':
                legacy.append(key)
        return sorted(legacy)

    def migrate_legacy_project(self, name):
        """
        Rename a legacy key to the prefixed one. Returns True on success,
        False if the target already exists (nothing is overwritten).
        """
        return bool(self.r.renamenx(name, project_key(name)))


class SqliteStore:
    """
    SQLite-backed store, for running SubBud without a Redis server.

    Everything lives in one table, so domains and their metadata are written
    and deleted together.
    """

    name = 'sqlite'

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS domains (
        project    TEXT NOT NULL,
        domain     TEXT NOT NULL,
        first_seen TEXT,
        source     TEXT,
        PRIMARY KEY (project, domain)
    );
    """

    def __init__(self, path=DEFAULT_SQLITE_PATH):
        self.path = os.path.expanduser(path)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()

    def describe(self):
        return f"sqlite path={self.path}"

    def close(self):
        self.conn.close()

    # --- domains ---------------------------------------------------------

    def add_domains(self, project, domains):
        if not domains:
            return 0
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO domains (project, domain) VALUES (?, ?)",
            [(project, domain) for domain in domains],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def remove_domains(self, project, domains):
        if not domains:
            return 0
        before = self.conn.total_changes
        self.conn.executemany(
            "DELETE FROM domains WHERE project = ? AND domain = ?",
            [(project, domain) for domain in domains],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def iter_domains(self, project):
        cursor = self.conn.execute(
            "SELECT domain FROM domains WHERE project = ?", (project,)
        )
        for (domain,) in cursor:
            yield domain

    def get_domain_count(self, project):
        (count,) = self.conn.execute(
            "SELECT COUNT(*) FROM domains WHERE project = ?", (project,)
        ).fetchone()
        return count

    def get_projects(self):
        return [
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT project FROM domains ORDER BY project"
            )
        ]

    def delete_project(self, project):
        cursor = self.conn.execute("DELETE FROM domains WHERE project = ?", (project,))
        self.conn.commit()
        return 1 if cursor.rowcount else 0

    # --- metadata --------------------------------------------------------

    def record_metadata(self, project, domains, first_seen, source):
        if not domains:
            return 0
        before = self.conn.total_changes
        self.conn.executemany(
            "UPDATE domains SET first_seen = ?, source = ? "
            "WHERE project = ? AND domain = ? AND first_seen IS NULL",
            [(first_seen, source, project, domain) for domain in domains],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def get_metadata(self, project, domain):
        row = self.conn.execute(
            "SELECT first_seen, source FROM domains WHERE project = ? AND domain = ?",
            (project, domain),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return {'first_seen': row[0], 'source': row[1]}

    def iter_domains_with_meta(self, project):
        cursor = self.conn.execute(
            "SELECT domain, first_seen, source FROM domains WHERE project = ?",
            (project,),
        )
        for domain, first_seen, source in cursor:
            yield domain, first_seen, source

    # --- legacy key migration (no-op: only Redis ever had legacy keys) ----

    def find_legacy_projects(self):
        return []

    def migrate_legacy_project(self, name):
        return False


def open_store(
    backend='redis', host='localhost', port=6379, db=0,
    use_ssl=False, password=None, sqlite_path=DEFAULT_SQLITE_PATH,
):
    """
    Build the requested backend.

    There is deliberately no automatic fallback from Redis to SQLite: silently
    switching backends would split a project's domains across two stores.
    """
    if backend == 'sqlite':
        return SqliteStore(path=sqlite_path)
    if backend == 'redis':
        return DataStore(host=host, port=port, db=db, use_ssl=use_ssl, password=password)
    raise ValueError(f"Unknown storage backend: {backend!r}")
