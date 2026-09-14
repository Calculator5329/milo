"""Bounded UTF-8 demo documents, with retained revisions and serialized edits.

All callers must use this module for optimistic concurrency; advisory locks do
not coordinate external editors. The configured root is trusted configuration,
never an argument accepted from a model or HTTP request.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

MAX_DOCUMENTS = 20
MAX_BYTES = 64 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,99}\.(?:md|txt)\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class DocumentError(ValueError):
    """A safe, user-facing document operation failure."""


class DocumentWorkspace:
    def __init__(self, root):
        self.root = Path(os.path.abspath(os.fspath(root)))
        # Walk from / without following even ancestor symlinks.
        with self._directory(create=True):
            pass

    @contextmanager
    def _directory(self, create=False):
        fd = os.open('/', _DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, _DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
        except OSError:
            raise DocumentError('Document storage is unavailable or unsafe.') from None
        finally:
            os.close(fd)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not _NAME.fullmatch(name) or '..' in name:
            raise DocumentError('Use a leaf filename ending in .md or .txt (up to 104 characters).')
        return name

    @staticmethod
    def _revision(revision):
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise DocumentError('A valid document revision is required.')
        return revision

    @staticmethod
    def _encode(content):
        if not isinstance(content, str):
            raise DocumentError('Document content must be text.')
        if len(content) > MAX_BYTES:
            raise DocumentError('Document exceeds the 64 KiB limit.')
        try:
            data = content.encode('utf-8')
        except UnicodeError:
            raise DocumentError('Document content must be valid UTF-8 text.') from None
        if len(data) > MAX_BYTES:
            raise DocumentError('Document exceeds the 64 KiB limit.')
        return data

    @staticmethod
    def _result(name, data, content=True):
        try:
            decoded = data.decode('utf-8')
        except UnicodeError:
            raise DocumentError('Document is not valid UTF-8 text.') from None
        result = {'name': name, 'revision': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        if content:
            result['content'] = decoded
        return result

    @staticmethod
    def _read(fd, name):
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            raise DocumentError('Document or revision does not exist.') from None
        with os.fdopen(file_fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DocumentError('Document must be a regular file without links.')
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise DocumentError('Document exceeds the 64 KiB limit.')
        return data

    @staticmethod
    def _names(fd):
        names = sorted(name for name in os.listdir(fd) if _NAME.fullmatch(name) and '..' not in name)
        if len(names) > MAX_DOCUMENTS:
            raise DocumentError('Document folder exceeds the 20 document limit.')
        return names

    @contextmanager
    def _history(self, fd, name):
        opened = []
        try:
            for part in ('.history', name):
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                fd = os.open(part, _DIRECTORY, dir_fd=fd)
                opened.append(fd)
            yield fd
        finally:
            for child in reversed(opened):
                os.close(child)

    @staticmethod
    def _write_new(fd, name, data, mode=0o600):
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
        with os.fdopen(target, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())

    def list_documents(self):
        with self._directory() as fd:
            return [self._result(name, self._read(fd, name), False) for name in self._names(fd)]

    def read(self, name):
        name = self._name(name)
        with self._directory() as fd:
            return self._result(name, self._read(fd, name))

    def create(self, name, content):
        name, data = self._name(name), self._encode(content)
        with self._directory() as fd:
            if len(self._names(fd)) >= MAX_DOCUMENTS:
                raise DocumentError('Document folder has reached the 20 document limit.')
            try:
                self._write_new(fd, name, data)
            except FileExistsError:
                raise DocumentError('Document already exists; read it before editing.') from None
            os.fsync(fd)
            return dict(self._result(name, data), prior_revision=None)

    def _change(self, name, expected_revision, transform):
        name = self._name(name)
        expected_revision = self._revision(expected_revision)
        with self._directory() as fd:
            self._names(fd)
            before = self._read(fd, name)
            previous = self._result(name, before)
            if previous['revision'] != expected_revision:
                raise DocumentError('Revision conflict: read the document again before editing.')
            with self._history(fd, name) as history:
                data = transform(before, history)
                self._result(name, data)  # Validate restored UTF-8 before retaining/writing.
                try:
                    self._write_new(history, expected_revision, before, 0o400)
                except FileExistsError:
                    if self._read(history, expected_revision) != before:
                        raise DocumentError('Stored revision failed integrity verification.')
                os.fsync(history)
            pending = '.pending-' + uuid.uuid4().hex
            self._write_new(fd, pending, data)
            os.replace(pending, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            return dict(self._result(name, data), prior_revision=expected_revision)

    def append(self, name, text, expected_revision):
        addition = self._encode(text)
        def transform(before, _history):
            data = before + addition
            if len(data) > MAX_BYTES:
                raise DocumentError('Document exceeds the 64 KiB limit.')
            return data
        return self._change(name, expected_revision, transform)

    def replace(self, name, content, expected_revision):
        data = self._encode(content)
        return self._change(name, expected_revision, lambda _before, _history: data)

    def revisions(self, name):
        name = self._name(name)
        with self._directory() as fd:
            self._read(fd, name)
            with self._history(fd, name) as history:
                results = []
                for revision in sorted(os.listdir(history)):
                    self._revision(revision)
                    result = self._result(name, self._read(history, revision), False)
                    if result['revision'] != revision:
                        raise DocumentError('Stored revision failed integrity verification.')
                    results.append(result)
                return results

    def restore(self, name, revision, expected_revision):
        revision = self._revision(revision)
        def transform(_before, history):
            data = self._read(history, revision)
            if hashlib.sha256(data).hexdigest() != revision:
                raise DocumentError('Stored revision failed integrity verification.')
            return data
        return self._change(name, expected_revision, transform)

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise DocumentError('Search requires 1 to 200 characters of text.')
        query = query.strip().casefold()
        results = []
        with self._directory() as fd:
            for name in self._names(fd):
                doc = self._result(name, self._read(fd, name))
                for number, line in enumerate(doc['content'].splitlines(), 1):
                    match = line.casefold().find(query)
                    if match >= 0:
                        start = max(0, match - 60)
                        results.append({**{k: v for k, v in doc.items() if k != 'content'},
                                        'line': number, 'excerpt': line[start:start + 240]})
                        break
        return results

    def seed(self):
        """Explicitly create only missing synthetic defaults; never overwrite."""
        fixtures = Path(__file__).parent / 'demo' / 'fixtures'
        results = []
        for name in ('project-brief.md', 'research-notes.md'):
            try:
                existing = self.read(name)
            except DocumentError as error:
                if str(error) != 'Document or revision does not exist.':
                    raise
                try:
                    try:
                        default = (fixtures / name).read_text(encoding='utf-8')
                    except (OSError, UnicodeError):
                        raise DocumentError('Synthetic demo fixtures are unavailable.') from None
                    existing = self.create(name, default)
                except DocumentError as conflict:
                    if str(conflict) != 'Document already exists; read it before editing.':
                        raise
                    existing = self.read(name)
            results.append(existing)
        return results
