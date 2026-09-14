import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from local_search import EXCERPT_CHARS, LocalLibraryClient
from multihop import candidates, merge, needs_hop


def match(path, title, excerpt, book='Reference'):
    return {
        'path': '/content/test/' + path,
        'title': title,
        'excerpt': excerpt,
        'book': book,
        'uri': 'kiwix://test/' + path,
    }


class FakeKiwix:
    base_url = 'http://127.0.0.1:8891'

    def __init__(self, results, failure=None):
        self.results = results
        self.failure = failure
        self.calls = []

    def search(self, query, limit=10, books=None):
        self.calls.append((query, books))
        if self.failure and len(self.calls) == 2:
            raise self.failure
        return {
            'matches': self.results.get(query, []),
            'books': ['wikipedia_en_all'],
        }

    def read(self, path):
        return {'content': 'Expanded article text. ' * 20, 'uri': 'kiwix://' + path}


FIRST = [
    match(
        'first-a',
        'Plant notes',
        'Researchers describe the Calvin Cycle as the central mechanism in this account. '
        'The Calvin Cycle is discussed alongside several related observations.',
    ),
    match(
        'first-b',
        'Research notes',
        'A later experiment also attributes the result to the Calvin Cycle. '
        'This summary gives background but does not address the original practical question.',
    ),
]
SECOND = [
    match(
        'calvin',
        'Calvin cycle',
        'The Calvin cycle is a set of light-independent reactions used by plants to convert '
        'carbon dioxide into sugars during photosynthesis.',
    ),
    match(
        'carbon',
        'Carbon fixation',
        'Carbon fixation converts inorganic carbon into organic compounds and forms part of '
        'the process described by the Calvin cycle.',
    ),
]


class MultiHopLookupTests(unittest.TestCase):
    def test_answered_first_hop_does_not_hop(self):
        first = [
            match('photo', 'Photosynthesis', 'Photosynthesis converts light energy into chemical energy in plants. ' * 2),
            match('plant', 'Plant metabolism', 'A photosynthesis pathway uses chlorophyll to capture light energy. ' * 2),
        ]
        adapter = FakeKiwix({'How does photosynthesis work?': first})

        receipt = LocalLibraryClient(adapter=adapter).search(
            'How does photosynthesis work?', threading.Event()
        )

        self.assertEqual(adapter.calls, [('How does photosynthesis work?', None)])
        self.assertEqual(len(receipt['hops']), 1)
        self.assertEqual(receipt['hop_reason'], None)

    def test_unfamiliar_candidate_hops_once_and_merges_within_budget(self):
        query = 'What powers the garden?'
        adapter = FakeKiwix({query: FIRST, 'Calvin Cycle': SECOND})

        receipt = LocalLibraryClient(adapter=adapter).search(query, threading.Event())

        self.assertEqual(adapter.calls, [(query, None), ('Calvin Cycle', ['wikipedia_en_all'])])
        self.assertEqual(len(receipt['hops']), 2)
        self.assertEqual(receipt['sources'][0]['title'], 'Plant notes')
        hop_two = [source for source in receipt['sources'] if source.get('hop') == 2]
        self.assertTrue(hop_two)
        self.assertTrue(all(source['via'] == 'Calvin Cycle' for source in hop_two))
        self.assertLessEqual(
            sum(len(source['excerpt']) for source in receipt['sources']),
            4 * EXCERPT_CHARS,
        )

    def test_second_hop_failure_keeps_first_hop(self):
        query = 'What powers the garden?'
        adapter = FakeKiwix({query: FIRST}, failure=RuntimeError('second search failed'))

        receipt = LocalLibraryClient(adapter=adapter).search(query, threading.Event())

        self.assertEqual([source['title'] for source in receipt['sources']], ['Plant notes', 'Research notes'])
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(receipt['hops'][-1]['found'], 0)

    def test_deadline_stops_the_second_hop(self):
        query = 'What powers the garden?'
        adapter = FakeKiwix({query: FIRST, 'Calvin Cycle': SECOND})

        class CollectedClient(LocalLibraryClient):
            def _collect(self, matches, cancelled, started):
                return [
                    {'id': index + 1, 'title': item['title'], 'url': self.adapter.base_url + item['path'],
                     'excerpt': item['excerpt'], 'origin': 'library', 'book': item['book'],
                     'citation': item['uri']}
                    for index, item in enumerate(matches)
                ]

        receipt = CollectedClient(adapter=adapter, deadline=0).search(query, threading.Event())

        self.assertEqual(adapter.calls, [(query, None)])
        self.assertEqual(len(receipt['sources']), 2)
        self.assertEqual(len(receipt['hops']), 1)

    def test_second_hop_cannot_extend_the_whole_deadline(self):
        query = 'What powers the garden?'
        release = threading.Event()

        class BlockingKiwix(FakeKiwix):
            def search(self, query, limit=10, books=None):
                if self.calls:
                    self.calls.append((query, books))
                    release.wait(0.5)
                    return {'matches': SECOND, 'books': ['wikipedia_en_all']}
                return super().search(query, limit, books)

        adapter = BlockingKiwix({query: FIRST})
        started = time.monotonic()
        try:
            receipt = LocalLibraryClient(adapter=adapter, deadline=0.03).search(
                query, threading.Event()
            )
        finally:
            release.set()

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual([source['title'] for source in receipt['sources']], ['Plant notes', 'Research notes'])

    def test_disambiguation_page_hops(self):
        query = 'Mercury'
        first = [
            match(
                'mercury',
                'Mercury',
                'Mercury may refer to: Roman Messenger, a figure in mythology. Mercury Planet, '
                'the smallest planet. Mercury Element, a chemical element.',
            ),
            match(
                'name',
                'Name index',
                'Mercury appears in an index that points readers toward the Roman Messenger topic.',
            ),
        ]
        adapter = FakeKiwix({query: first, 'Roman Messenger': SECOND})

        receipt = LocalLibraryClient(adapter=adapter).search(query, threading.Event())

        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.calls[1][0], 'Roman Messenger')
        self.assertIn('disambiguation', receipt['hop_reason'])


class MultiHopPureFunctionTests(unittest.TestCase):
    def test_candidate_ranking_uses_recurrence_then_earliest_mention(self):
        sources = [
            {'title': 'Navigation notes', 'excerpt': 'The "North Star" guides travelers. The Azure Engine is a compact device.'},
            {'title': 'Machine notes', 'excerpt': 'Tests of the Azure Engine continue. It is also known as "Blue Motor".'},
        ]

        found = candidates('How does the device work?', sources, limit=6)

        self.assertEqual(found[0], 'Azure Engine')
        self.assertLess(found.index('North Star'), found.index('Blue Motor'))

    def test_merge_keeps_both_hops_and_shares_a_small_budget(self):
        first = [{'title': 'First ' + str(index), 'excerpt': 'a' * 100} for index in range(4)]
        second = [{'title': 'Second', 'excerpt': 'b' * 100}]

        found = merge(first, second, 120, via='New Term')

        self.assertEqual(found[0]['title'], 'First 0')
        self.assertIn('Second', [source['title'] for source in found])
        self.assertLessEqual(sum(len(source['excerpt']) for source in found), 120)
        self.assertEqual(next(source for source in found if source['title'] == 'Second')['hop'], 2)

    def test_needs_hop_when_query_terms_are_missing(self):
        sources = [
            {'title': 'One', 'excerpt': 'The unrelated account discusses a blue mechanism.'},
            {'title': 'Two', 'excerpt': 'Another unrelated passage describes a green mechanism.'},
        ]
        self.assertTrue(needs_hop('Where was Ada Lovelace born?', sources))


if __name__ == '__main__':
    unittest.main()


class NamespaceFilterTests(unittest.TestCase):
    def test_category_and_template_pages_are_dropped(self):
        import local_search
        for title in ('Category:Years in Mongolia', 'Template:Infobox', 'File:Map.png', 'Wikipedia:Notability', 'Portal: History'):
            self.assertTrue(local_search.NAMESPACE_TITLE.match(title), title)
        for title in ('Mongolia', 'Battle of Hastings', 'Category theory', 'File systems'):
            self.assertFalse(local_search.NAMESPACE_TITLE.match(title), title)


class TitleSettlesTests(unittest.TestCase):
    def test_an_exact_title_match_never_hops_even_behind_a_disambiguation_page(self):
        from multihop import hop_reason, merge
        sources = [
            {'id': 1, 'title': 'Battle of Hastings (disambiguation)', 'excerpt': 'Battle of Hastings may refer to: the 1066 battle; a play by Richard Cumberland; a song.'},
            {'id': 2, 'title': 'Battle of Hastings', 'excerpt': 'The Battle of Hastings was fought on 14 October 1066 between the Norman-French army of William and the English army.'},
        ]
        self.assertIsNone(hop_reason('Battle of Hastings', sources))
        merged = merge(sources, [{'id': 9, 'title': 'Richard Cumberland', 'excerpt': 'x' * 300}], 2800, via='Richard Cumberland')
        self.assertEqual([m['title'] for m in merged][:2], ['Battle of Hastings (disambiguation)', 'Battle of Hastings'])
        self.assertEqual(merged[2]['hop'], 2)
