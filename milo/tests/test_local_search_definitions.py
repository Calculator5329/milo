import threading, unittest
from local_search import LocalLibraryClient, definition_term
from library.adapters import AdapterError


class FakeAdapter:
    base_url = 'http://kiwix.test'

    def __init__(self):
        self.calls = []

    def _get(self, path):
        return '<entry><name>wiktionary_en_all</name><link href="/content/wiktionary_en_all_nopic_2026-08"/></entry>'

    def read(self, path):
        if path.endswith('/ephemeral'):
            return {'path': path, 'uri': 'kiwix://x/ephemeral', 'content': 'English Adjective: lasting for a short time. ' * 4}
        raise AdapterError('missing')

    def search(self, query, limit=10, books=None):
        self.calls.append((query, books))
        if books:
            return {'matches': [
                {'path': '/content/wiktionary_en_all_nopic_2026-08/wanderlusting', 'title': 'wanderlusting', 'excerpt': 'w' * 100, 'book': 'Wiktionary'},
                {'path': '/content/wiktionary_en_all_nopic_2026-08/Wanderlust', 'title': 'Wanderlust', 'excerpt': 'A strong desire to travel. ' * 5, 'book': 'Wiktionary'},
            ]}
        return {'matches': [{'path': '/content/wikibooks/x', 'title': 'German phrasebook', 'excerpt': 'p' * 100, 'book': 'Wikibooks'}]}


class DefinitionLookupTests(unittest.TestCase):
    def test_definition_terms(self):
        self.assertEqual(definition_term('what the word wanderlust means'), 'wanderlust')
        self.assertEqual(definition_term('What does "serendipity" mean?'), 'serendipity')
        self.assertEqual(definition_term('the definition of ennui'), 'ennui')
        self.assertEqual(definition_term('define schadenfreude'), 'schadenfreude')
        self.assertIsNone(definition_term('how btrfs snapshots work'))
        self.assertIsNone(definition_term('what does the git remote command do'))

    def test_dictionary_is_asked_first_and_exact_title_leads(self):
        adapter = FakeAdapter()
        client = LocalLibraryClient(adapter=adapter)
        found = client.search('what the word wanderlust means', threading.Event())
        self.assertEqual(adapter.calls, [('wanderlust', ['wiktionary_en_all_nopic_2026-08'])])
        self.assertEqual(found['sources'][0]['title'], 'Wanderlust')

    def test_direct_entry_read_leads_when_it_exists(self):
        adapter = FakeAdapter()
        found = LocalLibraryClient(adapter=adapter).search('define ephemeral', threading.Event())
        self.assertEqual(found['sources'][0]['title'], 'ephemeral')
        self.assertIn('lasting for a short time', found['sources'][0]['excerpt'])
        self.assertEqual(found['sources'][0]['citation'], 'kiwix://x/ephemeral')

    def test_other_questions_search_the_whole_corpus(self):
        adapter = FakeAdapter()
        found = LocalLibraryClient(adapter=adapter).search('how btrfs snapshots work', threading.Event())
        self.assertEqual(adapter.calls, [('how btrfs snapshots work', None)])
        self.assertEqual(found['sources'][0]['title'], 'German phrasebook')


if __name__ == '__main__':
    unittest.main()
