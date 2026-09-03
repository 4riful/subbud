"""Domain normalization and scope filtering."""

import re

# A hostname label: 1-63 chars, alphanumeric or '-', not starting/ending with '-'.
# The full name must have at least two labels and be at most 253 chars.
_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)'
    r'(?!-)[a-z0-9-]{1,63}(?<!-)'
    r'(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$'
)

_SCHEME_RE = re.compile(r'^[a-z][a-z0-9+.\-]*://')


def normalize_domain(line):
    """
    Normalize one input line into a bare hostname.

    Returns the normalized hostname, or None if the line cannot be one.

    Rules, applied in order:
      1. Strip whitespace; ignore empty lines and '#' comments.
      2. Keep only the first whitespace-separated token (tool output often
         appends annotations, e.g. "example.com (FQDN) --> ...").
      3. Lowercase.
      4. Drop a URL scheme ("https://"), userinfo ("user:pass@"), and
         everything from the first '/', '?' or '#'.
      5. Drop a trailing port (":8443") and a trailing root dot.
      6. Drop a leading wildcard label ("*.example.com" -> "example.com").
      7. Reject anything that is not a valid multi-label hostname. Bare IPv4
         addresses pass, since they are valid bug bounty targets.
    """
    value = line.strip()
    if not value or value.startswith('#'):
        return None

    value = value.split()[0].lower()
    value = _SCHEME_RE.sub('', value)

    if '@' in value:
        value = value.rsplit('@', 1)[1]

    for separator in ('/', '?', '#'):
        value = value.split(separator, 1)[0]

    # Strip a trailing ":port"; leave IPv6-ish input alone so it fails validation.
    if ':' in value:
        head, _, tail = value.rpartition(':')
        if head and tail.isdigit():
            value = head

    value = value.rstrip('.')

    if value.startswith('*.'):
        value = value[2:]

    if not _HOSTNAME_RE.match(value):
        return None
    return value


class ScopePattern:
    """
    One scope pattern.

    - "example.com" matches the apex and every subdomain of it.
    - "*.example.com" matches subdomains only, not the apex.

    Patterns go through the same normalization as domains, so
    "HTTPS://Example.COM/" is a usable pattern.
    """

    def __init__(self, raw):
        text = raw.strip()
        self.subdomains_only = text.startswith('*.')
        if self.subdomains_only:
            text = text[2:]

        self.base = normalize_domain(text)
        if self.base is None:
            raise ValueError(f"Invalid scope pattern: {raw.strip()!r}")

    def matches(self, domain):
        if domain.endswith('.' + self.base):
            return True
        return not self.subdomains_only and domain == self.base

    def __repr__(self):
        prefix = '*.' if self.subdomains_only else ''
        return f'ScopePattern({prefix}{self.base!r})'


class ScopeFilter:
    """
    Include/exclude rules for domains.

    A domain is in scope when it matches at least one include pattern (or no
    include patterns were given at all) and matches no exclude pattern.
    Excludes always win.
    """

    def __init__(self, include=(), exclude=()):
        self.include = [ScopePattern(p) for p in include]
        self.exclude = [ScopePattern(p) for p in exclude]

    @property
    def is_empty(self):
        return not self.include and not self.exclude

    @classmethod
    def from_args(cls, include=(), exclude=(), include_files=(), exclude_files=()):
        """Build a filter from inline patterns plus pattern files."""
        includes = list(include or ())
        excludes = list(exclude or ())
        for path in include_files or ():
            includes.extend(cls.read_patterns(path))
        for path in exclude_files or ():
            excludes.extend(cls.read_patterns(path))
        return cls(includes, excludes)

    @staticmethod
    def read_patterns(path):
        """Read patterns from a file, ignoring blank lines and '#' comments."""
        patterns = []
        with open(path, 'r', errors='replace') as handle:
            for line in handle:
                text = line.strip()
                if text and not text.startswith('#'):
                    patterns.append(text)
        return patterns

    def allows(self, domain):
        if self.include and not any(p.matches(domain) for p in self.include):
            return False
        return not any(p.matches(domain) for p in self.exclude)
