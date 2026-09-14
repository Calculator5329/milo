"""Diagnostic filesystem probes for the demo document capability."""
import concurrent.futures
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_documents import DocumentError, DocumentWorkspace, MAX_BYTES, MAX_DOCUMENTS


class DocumentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        cache = Path.home() / '.cache' / 'tmp'
        cache.mkdir(parents=True, exist_ok=True)
        # Retain probe artifacts instead of permanently deleting prior content.
        self.directory = Path(tempfile.mkdtemp(prefix='milo-documents-', dir=cache))
        self.root = self.directory / 'documents'
        self.workspace = DocumentWorkspace(self.root)

    def test_round_trip_history_restore_and_search(self):
        first = self.workspace.create('brief.md', '# Brief\nSeeds 🌱\n')
        self.assertIsNone(first['prior_revision'])
        second = self.workspace.append('brief.md', 'Next: plant.\n', first['revision'])
        third = self.workspace.replace('brief.md', 'Revised plan', second['revision'])
        self.assertEqual(third['prior_revision'], second['revision'])
        self.assertEqual(self.workspace.read('brief.md'), {k: v for k, v in third.items() if k != 'prior_revision'})
        restored = self.workspace.restore('brief.md', first['revision'], third['revision'])
        self.assertEqual(restored['content'], first['content'])
        self.assertEqual(restored['revision'], first['revision'])
        self.assertEqual({r['revision'] for r in self.workspace.revisions('brief.md')},
                         {first['revision'], second['revision'], third['revision']})
        self.assertEqual(self.workspace.search('SEEDS')[0]['line'], 2)
        self.assertEqual(self.workspace.search('missing'), [])
        self.assertEqual(self.workspace.list_documents()[0]['bytes'], len(first['content'].encode()))
        for revision in (first, second, third):
            snapshot = self.root / '.history' / 'brief.md' / revision['revision']
            self.assertEqual(snapshot.read_text(), revision['content'])
            self.assertEqual(snapshot.stat().st_mode & 0o222, 0)

    def test_conflict_never_changes_document(self):
        original = self.workspace.create('notes.txt', 'one')
        updated = self.workspace.append('notes.txt', ' two', original['revision'])
        for action in (
            lambda: self.workspace.append('notes.txt', ' bad', original['revision']),
            lambda: self.workspace.replace('notes.txt', 'bad', original['revision']),
            lambda: self.workspace.restore('notes.txt', original['revision'], original['revision']),
        ):
            with self.assertRaisesRegex(DocumentError, 'Revision conflict'):
                action()
        self.assertEqual(self.workspace.read('notes.txt')['content'], updated['content'])
        with self.assertRaisesRegex(DocumentError, 'already exists'):
            self.workspace.create('notes.txt', 'bad')

    def test_concurrent_writers_only_one_revision_wins(self):
        original = self.workspace.create('notes.txt', 'one')
        def write(value):
            try:
                DocumentWorkspace(self.root).append('notes.txt', value, original['revision'])
                return 'written'
            except DocumentError as error:
                return str(error)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, ('two', 'three')))
        self.assertEqual(outcomes.count('written'), 1)
        self.assertEqual(sum('Revision conflict' in result for result in outcomes), 1)

    def test_names_and_revision_arguments_reject_escapes(self):
        for name in ('../outside.md', '/outside.txt', 'a/b.md', 'a\\b.md', '.hidden.md',
                     'a..md', 'app.py', 'a.MD', '', None, 'a\x00.md', 'a' * 105 + '.md'):
            with self.subTest(name=name), self.assertRaises(DocumentError):
                self.workspace.create(name, 'bad')
        self.workspace.create('fine.md', 'fine')
        for revision in ('../outside', '', None, 'z' * 64):
            with self.assertRaises(DocumentError):
                self.workspace.restore('fine.md', revision, '0' * 64)

    def test_symlinks_hardlinks_and_nonregular_files_rejected(self):
        outside = self.directory / 'outside.txt'
        outside.write_text('protected')
        (self.root / 'link.txt').symlink_to(outside)
        with self.assertRaises(DocumentError):
            self.workspace.read('link.txt')
        with self.assertRaises(DocumentError):
            self.workspace.list_documents()
        with self.assertRaises(DocumentError):
            self.workspace.create('link.txt', 'bad')
        os.link(outside, self.root / 'hard.txt')
        with self.assertRaises(DocumentError):
            self.workspace.read('hard.txt')
        os.mkfifo(self.root / 'pipe.txt')
        with self.assertRaises(DocumentError):
            self.workspace.read('pipe.txt')
        (self.root / 'folder.md').mkdir()
        with self.assertRaises(DocumentError):
            self.workspace.read('folder.md')
        self.assertEqual(outside.read_text(), 'protected')
        alias = self.directory / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(DocumentError):
            DocumentWorkspace(alias)
        with self.assertRaises(DocumentError):
            DocumentWorkspace(alias / 'child')
        self.assertFalse((self.root / 'child').exists())

    def test_history_symlink_and_corrupt_snapshot_fail_closed(self):
        original = self.workspace.create('notes.md', 'before')
        (self.root / '.history').symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(DocumentError):
            self.workspace.replace('notes.md', 'after', original['revision'])
        self.assertEqual(self.workspace.read('notes.md')['content'], 'before')
        other = DocumentWorkspace(self.directory / 'other')
        first = other.create('notes.md', 'first')
        second = other.replace('notes.md', 'second', first['revision'])
        snapshot = other.root / '.history' / 'notes.md' / first['revision']
        snapshot.chmod(0o600)
        snapshot.write_text('corrupt')
        with self.assertRaisesRegex(DocumentError, 'integrity'):
            other.restore('notes.md', first['revision'], second['revision'])
        with self.assertRaisesRegex(DocumentError, 'integrity'):
            other.revisions('notes.md')
        self.assertEqual(other.read('notes.md')['content'], 'second')

    def test_bounds_utf8_and_missing_inputs(self):
        exact = self.workspace.create('exact.md', 'é' * (MAX_BYTES // 2))
        self.assertEqual(exact['bytes'], MAX_BYTES)
        for action in (
            lambda: self.workspace.create('large.md', 'x' * (MAX_BYTES + 1)),
            lambda: self.workspace.append('exact.md', 'x', exact['revision']),
            lambda: self.workspace.replace('exact.md', 'x' * (MAX_BYTES + 1), exact['revision']),
            lambda: self.workspace.create('bad.md', '\ud800'),
            lambda: self.workspace.create('bad.md', None),
            lambda: self.workspace.read('missing.md'),
            lambda: self.workspace.restore('exact.md', '0' * 64, exact['revision']),
        ):
            with self.assertRaises(DocumentError):
                action()
        for query in ('', ' ', None, 'a' * 201):
            with self.assertRaises(DocumentError):
                self.workspace.search(query)
        (self.root / 'raw.txt').write_bytes(b'\xff')
        with self.assertRaises(DocumentError):
            self.workspace.read('raw.txt')
        (self.root / 'oversized.txt').write_bytes(b'x' * (MAX_BYTES + 1))
        with self.assertRaises(DocumentError):
            self.workspace.read('oversized.txt')

    def test_folder_limit_and_seed_preserves_existing(self):
        seeded = self.workspace.seed()
        self.assertEqual(len(seeded), 2)
        self.workspace.replace('project-brief.md', 'my changes', seeded[0]['revision'])
        self.workspace.seed()
        self.assertEqual(self.workspace.read('project-brief.md')['content'], 'my changes')
        for index in range(MAX_DOCUMENTS - 2):
            self.workspace.create(f'{index}.txt', '')
        self.assertEqual(len(self.workspace.list_documents()), MAX_DOCUMENTS)
        with self.assertRaisesRegex(DocumentError, '20 document'):
            self.workspace.create('extra.md', '')
        (self.root / 'external.md').write_text('external')
        with self.assertRaisesRegex(DocumentError, '20 document'):
            self.workspace.list_documents()


if __name__ == '__main__':
    unittest.main()
