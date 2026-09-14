"""Copyable model text becomes a thought event while Milo speaks a short cue."""
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from server import Turn  # noqa: E402
from thoughts import extract, tidy, wants_exact  # noqa: E402
try:
    from tests.test_tools_wiring import engine_without_init
except ModuleNotFoundError:
    from test_tools_wiring import engine_without_init


class ExtractTests(unittest.TestCase):
    def test_fenced_block_becomes_code_thought(self):
        spoken, thoughts = extract("Use this:\n```python\nprint('hello')\n```\nIt is short.")
        self.assertEqual(thoughts, [{'text': "print('hello')", 'kind': 'code'}])
        self.assertIn('the code above me', spoken)
        self.assertNotIn("print('hello')", spoken)
        self.assertTrue(spoken.endswith('It is short.'))

    def test_inline_command_becomes_command_thought(self):
        spoken, thoughts = extract('Run `systemctl --user list-timers` to see them.')
        self.assertEqual(thoughts, [{'text': 'systemctl --user list-timers', 'kind': 'command'}])
        self.assertEqual(spoken, 'Run the command above me to see them.')

    def test_inline_exact_text_becomes_text_thought(self):
        spoken, thoughts = extract('The exact phrase is `to be or not to be`.')
        self.assertEqual(thoughts, [{'text': 'to be or not to be', 'kind': 'text'}])
        self.assertEqual(spoken, 'The exact phrase is the text above me.')
        # A lone word is spoken as itself (see BareTokenTests).
        self.assertEqual(extract('The exact spelling is `accommodate`.'), ('The exact spelling is accommodate.', []))

    def test_inline_expression_becomes_code_thought(self):
        spoken, thoughts = extract('Use `print(value)` for that.')
        self.assertEqual(thoughts, [{'text': 'print(value)', 'kind': 'code'}])
        self.assertEqual(spoken, 'Use the code above me for that.')

    def test_bare_url_becomes_link_and_is_never_spoken(self):
        url = 'https://wiki.archlinux.org/title/Systemd/Timers'
        spoken, thoughts = extract('Open ' + url + ' for the details.')
        self.assertEqual(thoughts, [{'text': url, 'kind': 'link'}])
        self.assertEqual(spoken, 'Open the link above me for the details.')
        self.assertNotIn('http', spoken)
        self.assertNotIn('archlinux', spoken)

    def test_schemeless_domain_becomes_link_and_is_never_spoken(self):
        spoken, thoughts = extract('Here is the link: wiki.archlinux.org/title/Systemd/Timers for the details.')
        self.assertEqual(thoughts, [{'text': 'wiki.archlinux.org/title/Systemd/Timers', 'kind': 'link'}])
        self.assertEqual(spoken, 'Here is the link above me for the details.')
        spoken, thoughts = extract('Try archlinux.org. It ends with a sentence.')
        self.assertEqual(thoughts, [{'text': 'archlinux.org', 'kind': 'link'}])
        self.assertEqual(spoken, 'Try the link above me. It ends with a sentence.')
        self.assertEqual(extract('Version 3.14 of python is out and e.g. it works.'),
                         ('Version 3.14 of python is out and e.g. it works.', []))
        self.assertEqual(extract('Mail me at ethan@example.com today.')[1], [])

    def test_spaced_domain_is_repaired_into_a_link(self):
        spoken, thoughts = extract('Here is the page: archlinux. org/title/Systemd/Timers.')
        self.assertEqual(thoughts, [{'text': 'archlinux.org/title/Systemd/Timers', 'kind': 'link'}])
        self.assertEqual(spoken, 'Here is the page: the link above me.')
        self.assertEqual(extract('Yes. Org charts matter.')[1], [])

    def test_desktop_tools_count_as_commands(self):
        spoken, thoughts = extract('Try `hyprctl reload` to apply it.')
        self.assertEqual(thoughts, [{'text': 'hyprctl reload', 'kind': 'command'}])
        self.assertEqual(spoken, 'Try the command above me to apply it.')

    def test_stream_holds_a_spaced_link_until_it_completes(self):
        from thoughts import extract_stream
        for partial in ('Here is the link to that page: archlinux.',
                        'Here is the link to that page: archlinux. ',
                        'Here is the link to that page: archlinux. org',
                        'Here is the link to that page: archlinux. org/title/Sys'):
            self.assertEqual(extract_stream(partial), (partial, []), partial)
        self.assertEqual(extract_stream('That is a plain sentence. '), ('That is a plain sentence. ', []))
        spoken, thoughts = extract_stream('Here is the link to that page: archlinux. org/title/Systemd/Timers. Enjoy.')
        self.assertEqual(thoughts, [{'text': 'archlinux.org/title/Systemd/Timers', 'kind': 'link'}])
        self.assertEqual(spoken, 'Here is the link above me. Enjoy.')

    def test_announcing_phrase_is_not_doubled(self):
        spoken, _ = extract('You can use the command `systemctl --user list-timers` to see them.')
        self.assertEqual(spoken, 'You can use the command above me to see them.')
        spoken, _ = extract('Run this command: `git status` first.')
        self.assertEqual(spoken, 'Run the command above me first.')
        spoken, _ = extract('The code `x = 1` sets it.')
        self.assertEqual(spoken, 'The code above me sets it.')

    def test_announced_shell_command_line_becomes_thought(self):
        for announcement in ('Use this command:', 'The command is:'):
            with self.subTest(announcement=announcement):
                spoken, thoughts = extract(announcement + '\ngit reset --soft HEAD~1\nThat keeps the changes.')
                self.assertEqual(thoughts, [{'text': 'git reset --soft HEAD~1', 'kind': 'command'}])
                self.assertIn('the command above me', spoken)
                self.assertNotIn('git reset', spoken)

    def test_unannounced_tool_line_and_plain_text_stay_unchanged(self):
        for text in ('git is a version control tool.', 'Nothing copyable here.'):
            self.assertEqual(extract(text), (text, []))


class WantsExactTests(unittest.TestCase):
    def test_copyable_requests_are_detected(self):
        positives = (
            "What's the command to list my systemd user timers?",
            'Give me the command to undo the commit.',
            'How do I do that in the terminal?',
            'What is the URL?',
            'Send me the link.',
            'How do you spell accommodate?',
            'I need the exact spelling.',
            'Make it easy to copy and paste.',
            'Show me the code for parsing JSON.',
            'Give me a one-liner.',
            'What regex matches it?',
            'Send a snippet.',
            'How do I reset this in git?',
            'Which pacman command updates packages?',
        )
        for question in positives:
            with self.subTest(question=question):
                self.assertTrue(wants_exact(question))

    def test_discussion_and_idiom_are_not_copyable_requests(self):
        negatives = (
            'What is a command line?',
            'Spell it out for me.',
            'What does exact mean?',
            'Tell me about Python.',
            'How does git work?',
        )
        for question in negatives:
            with self.subTest(question=question):
                self.assertFalse(wants_exact(question))


class FakeResponse:
    def __init__(self, text):
        self.rows = [json.dumps({'message': {'content': text}, 'done': True,
                                             'done_reason': 'stop', 'eval_count': 12}).encode()]

    def __enter__(self):
        return self.rows

    def __exit__(self, *_args):
        return False


class ServerThoughtTests(unittest.TestCase):
    def test_model_command_is_a_thought_and_not_spoken(self):
        engine = engine_without_init()
        turn = Turn('thought'); engine.register(turn); events = []
        reply = 'Use `systemctl --user list-timers`. The exact command is above me.'
        with mock.patch.object(server, 'json_request', return_value=FakeResponse(reply)), \
                mock.patch.object(server.memory_notes, 'recall', return_value=''):
            engine.stream(turn, "What's the command to list my systemd user timers?", None, [], events.append)
        thought = next(event for event in events if event['type'] == 'thought')
        spoken = ' '.join(event['text'] for event in events if event['type'] == 'sentence')
        self.assertEqual(thought, {'type': 'thought', 'index': 0,
                                   'text': 'systemctl --user list-timers', 'kind': 'command'})
        self.assertIn('above me', spoken)
        self.assertNotIn('systemctl', spoken)
        self.assertEqual(turn.metrics['thoughts'], 1)
        self.assertLess(
            next(i for i, event in enumerate(events) if event['type'] == 'thought'),
            next(i for i, event in enumerate(events) if event['type'] == 'audio'),
        )

    def test_silent_turn_keeps_text_events_and_skips_the_voice(self):
        engine = engine_without_init()
        turn = Turn('silent', {**server.validate_options({}), 'silent': True}); engine.register(turn); events = []
        reply = 'Use `systemctl --user list-timers`. The exact command is above me.'
        with mock.patch.object(server, 'json_request', return_value=FakeResponse(reply)), \
                mock.patch.object(server.memory_notes, 'recall', return_value=''), \
                mock.patch.object(engine, 'audio_chunks', side_effect=AssertionError('voice ran')):
            engine.stream(turn, "What's the command to list my systemd user timers?", None, [], events.append)
        kinds = [event['type'] for event in events]
        self.assertIn('thought', kinds)
        self.assertIn('sentence', kinds)
        self.assertIn('done', kinds)
        self.assertNotIn('audio', kinds)
        self.assertIn('first_sentence_ms', turn.metrics)
        self.assertNotIn('first_audio_ms', turn.metrics)

    def test_open_backtick_link_is_never_cut_by_the_splitter(self):
        engine = engine_without_init()
        turn = Turn('link'); engine.register(turn)
        chunks = ('Here is the link: `wiki.archlinux.', 'org/title/Systemd_timer`.', ' Enjoy the read.')
        buffer, first = '', True
        emitted = []
        original = turn.put
        turn.put = lambda item: emitted.append(item)
        for chunk in chunks:
            buffer += chunk
            buffer, first = engine.queue_generated_text(turn, buffer, first=first)
        buffer, first = engine.queue_generated_text(turn, buffer, final=True, first=first)
        turn.put = original
        sentences = [text for kind, text in emitted if kind == 'sentence']
        found = [value for kind, value in emitted if kind == 'thought']
        self.assertEqual(found, [{'text': 'wiki.archlinux.org/title/Systemd_timer', 'kind': 'link'}])
        self.assertNotIn('archlinux', ' '.join(sentences))
        self.assertEqual(sentences[0], 'Here is the link above me.')

    def test_plain_text_uses_the_existing_splitter_without_changes(self):
        engine = engine_without_init()
        turn = Turn('plain')
        original = 'This is a plain answer. It has no copyable text.'
        expected, remainder = [], original
        first = True
        while True:
            sentence, remainder = engine.sentence_split(remainder, final=True, first=first)
            if not sentence:
                break
            first = False
            expected.append(('sentence', sentence))
        leftover, _first = engine.queue_generated_text(turn, original, final=True, first=True)
        actual = []
        while not turn.sentences.empty():
            actual.append(turn.sentences.get_nowait())
        self.assertEqual(leftover, remainder)
        self.assertEqual(actual, expected)

    def test_prompt_teaches_exact_text_without_marking_ordinary_words(self):
        prompt = server.system_prompt()
        self.assertIn('inside backticks', prompt)
        self.assertIn('never put backticks around ordinary words', prompt)



class BareTokenTests(unittest.TestCase):
    """A single word or number in backticks is spoken, never replaced by 'the text above me'."""

    def test_words_numbers_and_names_are_spoken_inline(self):
        self.assertEqual(extract('The adult human body has `206` bones.'), ('The adult human body has 206 bones.', []))
        spoken, thoughts = extract("It is spelled `accommodate`, with two c's.")
        self.assertEqual((spoken, thoughts), ("It is spelled accommodate, with two c's.", []))
        self.assertEqual(extract('The repo is `local-ai-lab` on GitHub.')[1], [])
        self.assertEqual(extract('Bitcoin is at `$115,230.50` USD.'), ('Bitcoin is at $115,230.50 USD.', []))

    def test_spelled_letters_are_read_out(self):
        self.assertEqual(extract('Spelled `a-c-c-o-m-m-o-d-a-t-e`.')[0], 'Spelled A, C, C, O, M, M, O, D, A, T, E.')

    def test_commands_beside_bare_tokens_keep_their_offsets(self):
        spoken, thoughts = extract('Run `hyprctl reload` after editing `hyprland.conf`, then `206`.')
        self.assertEqual(spoken, 'Run the command above me after editing hyprland.conf, then 206.')
        self.assertEqual(thoughts, [{'text': 'hyprctl reload', 'kind': 'command'}])

    def test_measurements_paths_and_tool_names_are_spoken(self):
        self.assertEqual(extract('Water boils at `211.95 °F` at sea level.'), ('Water boils at 211.95 °F at sea level.', []))
        self.assertEqual(extract('Install it with `pacman` or `paru`.'), ('Install it with pacman or paru.', []))
        self.assertEqual(extract('See `media-vault/CLAUDE.md` for the ruling.'), ('See media-vault/CLAUDE.md for the ruling.', []))
        spoken, thoughts = extract('Then use `btrfs subvolume snapshot` command on it.')
        self.assertEqual((spoken, thoughts[0]['kind']), ('Then use the command above me on it.', 'command'))

    def test_streamed_trailing_noun_is_tidied_at_the_sentence(self):
        engine = engine_without_init()
        turn = Turn('stream', server.validate_options({}))
        buffer, first = engine.queue_generated_text(turn, 'Use the `pacman -Ss`', final=False, first=True)
        buffer, first = engine.queue_generated_text(turn, buffer + ' command followed by the package name. Then wait.', final=True, first=first)
        spoken = []
        while not turn.sentences.empty():
            kind, value = turn.sentences.get_nowait()
            if kind == 'sentence':
                spoken.append(value)
        self.assertEqual(spoken[0], 'Use the command above me followed by the package name.')
        self.assertEqual(extract('To extract a `.tar.gz` file, run `tar -xzf` on it.')[0], 'To extract a .tar.gz file, run the command above me on it.')

    def test_flagged_or_piped_spans_are_commands_even_for_unlisted_tools(self):
        for span in ('ss -tulpn', 'ps aux | grep ollama', 'tail -f /var/log/pacman.log', 'scp file.txt user@host:~/', 'ncdu /'):
            self.assertEqual(extract(f'Run `{span}` now.')[1], [{'text': span, 'kind': 'command'}], span)
        self.assertEqual(extract('It is the `to be or not to be` line.')[1][0]['kind'], 'text')

    def test_article_and_trailing_noun_collapse(self):
        self.assertEqual(extract('Reload with the `hyprctl reload` command.')[0], 'Reload with the command above me.')
        self.assertEqual(extract('Add the `--user` flag first.')[0], 'Add the flag above me first.')


if __name__ == '__main__':
    unittest.main()


class RoundTwoSpeechTests(unittest.TestCase):
    def test_second_command_is_numbered(self):
        spoken, found = extract('Use `docker ps` to list them. For all containers use `docker ps -a` instead.')
        self.assertEqual(spoken, 'Use the command above me to list them. For all containers use the second command above me instead.')
        self.assertEqual([t['text'] for t in found], ['docker ps', 'docker ps -a'])
        spoken, _ = extract('Then run the `docker rm x` command.', seen={'command': 2})
        self.assertEqual(spoken, 'Then run the third command above me.')
        self.assertEqual(tidy('Use the the second command above me command now'), 'Use the second command above me now')

    def test_stitched_link_fragment_becomes_a_sentence(self):
        self.assertEqual(tidy('Here is the link to the article: the link above me', terminal=True), 'Here is the link above me.')
        self.assertEqual(tidy('Use the command above me'), 'Use the command above me')

    def test_math_markup_is_verbalised(self):
        self.assertEqual(tidy('Suppose $\\sqrt{2}$ is rational, so $2 = a^2/b^2$.'),
                         'Suppose the square root of 2 is rational, so 2 = a squared/b squared.')
        self.assertEqual(tidy('It costs $5 and the $12 one is better.'), 'It costs $5 and the $12 one is better.')
