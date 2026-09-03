import argparse
import logging
import os
import sys
from datetime import datetime

import redis
from dotenv import load_dotenv
from tqdm import tqdm

from .domains import ScopeFilter, normalize_domain
from .stores import (
    DEFAULT_SQLITE_PATH,
    KEY_PREFIX,
    DataStore,
    SqliteStore,
    meta_key,
    open_store,
    project_key,
    validate_project_name,
)

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

__all__ = [
    'DataStore', 'SqliteStore', 'Project', 'ScopeFilter', 'normalize_domain',
    'open_store', 'project_key', 'meta_key', 'KEY_PREFIX', 'main',
]


class Project:
    def __init__(self, datastore, name):
        self.datastore = datastore
        self.name = validate_project_name(name)

    def add_domains_from_file(
        self, filename, normalize=True, batch_size=1000, scope=None, source=None
    ):
        """
        Stream `filename` into the store in batches.

        Neither the file nor the existing domain set is held in memory: the
        store reports how many domains were new, so a 10M-line file costs one
        line of memory plus one batch.

        Returns a stats dict with the counts that are also printed.
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"❌ File does not exist: {filename}")

        source = source or os.path.basename(filename)
        first_seen = datetime.now().strftime('%Y-%m-%d')

        stats = {'total': 0, 'added': 0, 'invalid': 0, 'out_of_scope': 0}
        batch = []
        file_size = os.path.getsize(filename)

        def flush():
            if not batch:
                return
            stats['added'] += self.datastore.add_domains(self.name, batch)
            self.datastore.record_metadata(self.name, batch, first_seen, source)
            batch.clear()

        with open(filename, 'r', errors='replace') as handle, tqdm(
            total=file_size, desc='Adding domains', unit='B', unit_scale=True
        ) as pbar:
            for line in handle:
                pbar.update(len(line.encode('utf-8', errors='replace')))

                if normalize:
                    domain = normalize_domain(line)
                    if domain is None:
                        text = line.strip()
                        if text and not text.startswith('#'):
                            stats['invalid'] += 1
                        continue
                else:
                    domain = line.strip()
                    if not domain:
                        continue

                if scope is not None and not scope.allows(domain):
                    stats['out_of_scope'] += 1
                    continue

                stats['total'] += 1
                batch.append(domain)
                if len(batch) >= batch_size:
                    flush()

            flush()

        if stats['total'] == 0:
            raise ValueError(f"❌ No usable domains found in '{filename}'.")

        stats['duplicates'] = stats['total'] - stats['added']
        print("✅ Domain Addition Complete")
        print(f"✨ Added {stats['added']} new domains to '{self.name}' project.")
        print(f"🔍 Duplicates skipped: {stats['duplicates']}")
        print(f"🔄 Percentage of new domains: {stats['added'] / stats['total'] * 100:.2f}%")
        if stats['invalid']:
            print(f"⚠️ Ignored {stats['invalid']} lines that are not valid hostnames.")
        if stats['out_of_scope']:
            print(f"🚫 Ignored {stats['out_of_scope']} domains outside the given scope.")
        return stats

    def iter_lines(self, sort=False, meta=False):
        """
        Yield the project's domains as output lines.

        With `meta`, each line is TSV: domain, first-seen date, source. Domains
        added before metadata existed (or with `--raw`) render as '-'.
        """
        if meta:
            rows = self.datastore.iter_domains_with_meta(self.name)
            if sort:
                rows = sorted(rows)
            for domain, first_seen, source in rows:
                yield f"{domain}\t{first_seen or '-'}\t{source or '-'}"
        else:
            domains = self.datastore.iter_domains(self.name)
            if sort:
                domains = sorted(domains)
            for domain in domains:
                yield domain

    def print_domains(self, sort=False, meta=False):
        domain_count = self.datastore.get_domain_count(self.name)
        if not domain_count:
            print(f"❌ No domains found for project '{self.name}'.")
            return False

        print(f"✅ {domain_count} domains found for the '{self.name}' project:")
        if meta:
            print("domain\tfirst_seen\tsource")
        for line in self.iter_lines(sort=sort, meta=meta):
            print(line)
        return True

    def save_domains(self, output=None, sort=False, meta=False):
        domain_count = self.datastore.get_domain_count(self.name)
        if not domain_count:
            print(f"❌ No domains found for project '{self.name}'.")
            return False

        filename = output or f"{datetime.now().strftime('%Y-%m-%d')}_{self.name}.txt"
        with open(filename, 'w') as handle:
            if meta:
                handle.write("domain\tfirst_seen\tsource\n")
            for line in self.iter_lines(sort=sort, meta=meta):
                handle.write(line + '\n')

        print(f"✅ Saved {domain_count} domains to '{filename}'")
        return True


def list_projects(datastore):
    projects = datastore.get_projects()
    if not projects:
        print("❌ No projects found.")
    else:
        print("📋 Available projects:")
        for project in projects:
            print(project)

    legacy = datastore.find_legacy_projects()
    if legacy:
        print(
            f"\n⚠️ {len(legacy)} project(s) still use the old unprefixed keys "
            f"({', '.join(legacy[:5])}{'...' if len(legacy) > 5 else ''}).\n"
            f"   Run `subbud -o migrate` to move them under '{KEY_PREFIX}'."
        )


def migrate_projects(datastore):
    legacy = datastore.find_legacy_projects()
    if not legacy:
        print("✅ Nothing to migrate: all projects already use the 'subbud:' prefix.")
        return 0

    failed = 0
    for name in legacy:
        if datastore.migrate_legacy_project(name):
            print(f"✅ Migrated '{name}' -> '{project_key(name)}'")
        else:
            failed += 1
            print(f"❌ Skipped '{name}': '{project_key(name)}' already exists.")

    print(f"\n📦 Migrated {len(legacy) - failed} of {len(legacy)} project(s).")
    return 1 if failed else 0


def build_parser():
    parser = argparse.ArgumentParser(description="Manage bug bounty targets")
    parser.add_argument('-p', '--project', help='The project name')
    parser.add_argument('-f', '--file', help='The file containing domains')
    parser.add_argument(
        '-o', '--operation', required=True,
        choices=['add', 'print', 'list', 'delete', 'save', 'migrate'],
        help='Operation to perform',
    )
    parser.add_argument('--output', help="Output file for 'save' (default: <date>_<project>.txt)")
    parser.add_argument('--sort', action='store_true', help="Sort domains for 'print' and 'save'")
    parser.add_argument(
        '--meta', action='store_true',
        help="Include first-seen date and source columns in 'print' and 'save'",
    )
    parser.add_argument(
        '--raw', action='store_true',
        help="Store lines from -f verbatim, skipping normalization and validation",
    )
    parser.add_argument(
        '--source',
        help="Label recorded as the source of domains added by this run "
             "(default: the input file name)",
    )

    scope = parser.add_argument_group('scope filtering (applies to --operation add)')
    scope.add_argument(
        '--scope', action='append', metavar='PATTERN', default=[],
        help="Only store domains matching this pattern; repeatable. "
             "'example.com' matches the apex and subdomains, '*.example.com' subdomains only",
    )
    scope.add_argument(
        '--exclude', action='append', metavar='PATTERN', default=[],
        help="Never store domains matching this pattern; repeatable. Excludes win over --scope",
    )
    scope.add_argument(
        '--scope-file', action='append', metavar='PATH', default=[],
        help="File of --scope patterns, one per line; repeatable",
    )
    scope.add_argument(
        '--exclude-file', action='append', metavar='PATH', default=[],
        help="File of --exclude patterns, one per line; repeatable",
    )

    store = parser.add_argument_group('storage backend')
    store.add_argument(
        '--store', choices=['redis', 'sqlite'],
        default=os.getenv('SUBBUD_STORE', 'redis'),
        help='Where to keep projects (default: redis)',
    )
    store.add_argument(
        '--sqlite-path', default=os.getenv('SUBBUD_SQLITE_PATH', DEFAULT_SQLITE_PATH),
        help=f'Database file for --store sqlite (default: {DEFAULT_SQLITE_PATH})',
    )
    store.add_argument('--host', default=os.getenv('REDIS_HOST', 'localhost'), help='Redis server host')
    store.add_argument('--port', type=int, default=os.getenv('REDIS_PORT', 6379), help='Redis server port')
    store.add_argument('--db', type=int, default=os.getenv('REDIS_DB', 0), help='Redis database number')
    store.add_argument('--password', default=os.getenv('REDIS_PASSWORD'), help='Redis password')
    store.add_argument(
        '--ssl', action='store_true',
        default=os.getenv('REDIS_SSL', 'false').lower() in ['true', '1'],
        help='Use SSL for Redis connection',
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # 'list' and 'migrate' work across all projects; everything else needs a target.
    if args.operation not in ('list', 'migrate') and not args.project:
        parser.error("❌ You must provide a project name for this operation.")
    if args.operation == 'add' and not args.file:
        parser.error("❌ You must provide a file with the 'add' operation.")
    if args.operation == 'migrate' and args.store != 'redis':
        parser.error("❌ 'migrate' only applies to the redis backend.")

    try:
        scope = ScopeFilter.from_args(
            include=args.scope, exclude=args.exclude,
            include_files=args.scope_file, exclude_files=args.exclude_file,
        )
    except (ValueError, OSError) as exc:
        parser.error(f"❌ {exc}")

    try:
        datastore = open_store(
            backend=args.store, host=args.host, port=args.port, db=args.db,
            use_ssl=args.ssl, password=args.password, sqlite_path=args.sqlite_path,
        )
    except ConnectionError as exc:
        print(exc)
        return 1

    try:
        if args.operation == 'list':
            list_projects(datastore)
            return 0
        if args.operation == 'migrate':
            return migrate_projects(datastore)

        project = Project(datastore, args.project)

        if args.operation == 'add':
            project.add_domains_from_file(
                args.file,
                normalize=not args.raw,
                scope=None if scope.is_empty else scope,
                source=args.source,
            )
        elif args.operation == 'print':
            return 0 if project.print_domains(sort=args.sort, meta=args.meta) else 1
        elif args.operation == 'save':
            return 0 if project.save_domains(
                output=args.output, sort=args.sort, meta=args.meta
            ) else 1
        elif args.operation == 'delete':
            if datastore.delete_project(args.project):
                print(f"✅ Project '{args.project}' deleted.")
            else:
                print(f"❌ Project '{args.project}' not found.")
                return 1
        return 0
    except (FileNotFoundError, ValueError, OSError, redis.exceptions.RedisError) as exc:
        logging.error(exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
