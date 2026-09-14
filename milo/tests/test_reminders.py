import datetime
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import reminders


NOW = datetime.datetime(2026, 9, 12, 14, 0, 0)


class FakeRunner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return subprocess.CompletedProcess(argv, 0, '', '')


class ParseTests(unittest.TestCase):
    def test_required_relative_phrases(self):
        cases = {
            'remind me in twenty minutes to stretch': ('create', 1200, 'stretch'),
            'remind me to check the oven in 2 hours': ('create', 7200, 'check the oven'),
            'in an hour remind me to call Sam': ('create', 3600, 'call Sam'),
            'set a timer for ten minutes': ('timer', 600, 'timer'),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                parsed = reminders.parse(text, NOW)
                self.assertEqual((parsed['kind'], parsed['delay_seconds'], parsed['label']), expected)
                self.assertEqual(
                    parsed['when'],
                    (NOW + datetime.timedelta(seconds=expected[1])).isoformat(timespec='seconds'),
                )

    def test_number_words_to_hundreds(self):
        parsed = reminders.parse('remind me in one hundred twenty five minutes to leave', NOW)
        self.assertEqual(parsed['delay_seconds'], 7500)

    def test_required_absolute_phrases(self):
        parsed = reminders.parse('remind me at 3 pm to call the bank', NOW)
        self.assertEqual(parsed['kind'], 'create')
        self.assertEqual(parsed['label'], 'call the bank')
        self.assertEqual(parsed['when'], '2026-09-12T15:00:00')
        self.assertEqual(parsed['delay_seconds'], None)

        parsed = reminders.parse('at 7:30 tomorrow remind me to water the plants', NOW)
        self.assertEqual(parsed['when'], '2026-09-13T07:30:00')
        self.assertEqual(parsed['label'], 'water the plants')

    def test_list_cancel_and_rejections(self):
        self.assertEqual(reminders.parse('what reminders do I have', NOW), {'kind': 'list'})
        self.assertEqual(reminders.parse('list my reminders', NOW), {'kind': 'list'})
        self.assertEqual(reminders.parse('cancel the stretch reminder', NOW), {'kind': 'cancel', 'label': 'stretch'})
        self.assertEqual(reminders.parse('cancel the stretch reminder.', NOW), {'kind': 'cancel', 'label': 'stretch'})
        self.assertEqual(reminders.parse('cancel the timer', NOW), {'kind': 'cancel', 'label': 'timer'})
        self.assertEqual(reminders.parse('cancel all reminders', NOW), {'kind': 'cancel', 'label': 'all'})
        self.assertIsNone(reminders.parse('remind me of the capital of France', NOW))
        self.assertIsNone(reminders.parse('what is a timer', NOW))


class StoreAndSystemdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / 'reminders.json'
        self.log = Path(self.temp.name) / 'reminders.log'
        self.env = mock.patch.dict(os.environ, {
            'MILO_REMINDERS': str(self.store),
            'MILO_REMINDERS_LOG': str(self.log),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_store_round_trip(self):
        rows = [{'id': 'ab12cd', 'label': 'stretch', 'when': '2026-09-12T14:20:00',
                 'unit': 'milo-reminder-ab12cd.timer', 'created': '2026-09-12T14:00:00'}]
        reminders.save(rows)
        self.assertEqual(reminders.load(), rows)
        self.assertEqual(json.loads(self.store.read_text()), rows)

    @mock.patch('reminders.secrets.token_hex', return_value='ab12cd')
    def test_create_delay_uses_exact_systemd_argv_and_stores_receipt(self, _token):
        runner = FakeRunner()
        entry = reminders.parse('remind me in twenty minutes to stretch', NOW)
        created = reminders.create(entry, runner=runner)
        script = str(Path(reminders.__file__).resolve())
        self.assertEqual(runner.calls, [([
            'systemd-run', '--user', '--on-active=1200', '--unit=milo-reminder-ab12cd',
            '--description=Milo reminder: stretch', '--collect', '/usr/bin/python3',
            script, '--fire', 'ab12cd',
        ], {'capture_output': True, 'text': True})])
        self.assertEqual(created['unit'], 'milo-reminder-ab12cd.timer')
        self.assertEqual(reminders.load()[0]['id'], 'ab12cd')

    @mock.patch('reminders.secrets.token_hex', return_value='c0ffee')
    def test_create_calendar_uses_when(self, _token):
        runner = FakeRunner()
        entry = reminders.parse('remind me at 3 pm to call the bank', NOW)
        reminders.create(entry, runner=runner)
        self.assertIn('--on-calendar=2026-09-12 15:00:00', runner.calls[0][0])

    def test_list_reconciles_missing_units(self):
        reminders.save([
            {'id': 'ab12cd', 'label': 'stretch', 'when': 'x', 'unit': 'milo-reminder-ab12cd.timer', 'created': 'x'},
            {'id': 'c0ffee', 'label': 'bank', 'when': 'y', 'unit': 'milo-reminder-c0ffee.timer', 'created': 'y'},
        ])
        output = 'Sat 2026-09-12 15:00:00 CDT 10min left n/a n/a milo-reminder-ab12cd.timer milo-reminder-ab12cd.service\n'
        runner = FakeRunner([subprocess.CompletedProcess([], 0, output, '')])
        self.assertEqual([entry['id'] for entry in reminders.list(runner=runner)], ['ab12cd'])
        self.assertEqual([entry['id'] for entry in reminders.load()], ['ab12cd'])
        self.assertEqual(runner.calls[0][0], [
            'systemctl', '--user', 'list-timers', '--all', '--no-legend', 'milo-reminder-*',
        ])

    def test_cancel_stops_exact_timer_and_removes_entry(self):
        reminders.save([{'id': 'ab12cd', 'label': 'stretch', 'when': 'x',
                         'unit': 'milo-reminder-ab12cd.timer', 'created': 'x'}])
        runner = FakeRunner()
        removed = reminders.cancel('ab12cd', runner=runner)
        self.assertEqual(removed['label'], 'stretch')
        self.assertEqual(runner.calls[0][0], [
            'systemctl', '--user', 'stop', 'milo-reminder-ab12cd.timer',
        ])
        self.assertEqual(reminders.load(), [])

    def test_fire_speaks_then_removes_and_logs(self):
        reminders.save([{'id': 'ab12cd', 'label': 'stretch', 'when': 'x',
                         'unit': 'milo-reminder-ab12cd.timer', 'created': 'x'}])
        runner = FakeRunner()
        fired = reminders.fire('ab12cd', runner=runner)
        self.assertEqual(fired['delivery'], 'gdbus')
        self.assertEqual(runner.calls[0][0], [
            'gdbus', 'call', '--session', '--dest', 'org.milo.Companion',
            '--object-path', '/org/milo/Companion', '--method',
            'org.milo.Companion.Say', 'Reminder: stretch.',
        ])
        self.assertEqual(reminders.load(), [])
        row = json.loads(self.log.read_text().strip())
        self.assertEqual((row['id'], row['label'], row['delivery']), ('ab12cd', 'stretch', 'gdbus'))

    def test_fire_falls_back_to_notification(self):
        reminders.save([{'id': 'ab12cd', 'label': 'stretch', 'when': 'x',
                         'unit': 'milo-reminder-ab12cd.timer', 'created': 'x'}])
        runner = FakeRunner([
            subprocess.CompletedProcess([], 1, '', 'not on bus'),
            subprocess.CompletedProcess([], 0, '', ''),
        ])
        fired = reminders.fire('ab12cd', runner=runner)
        self.assertEqual(fired['delivery'], 'notify-send')
        self.assertEqual(runner.calls[1][0], ['notify-send', 'Milo reminder', 'Reminder: stretch.'])


class HandleTests(unittest.TestCase):
    @mock.patch('reminders.create')
    def test_create_has_spoken_receipt(self, create):
        create.return_value = {'id': 'ab12cd', 'unit': 'milo-reminder-ab12cd.timer'}
        result = reminders.handle('remind me in twenty minutes to stretch', NOW)
        self.assertEqual(result, {
            'spoken': 'Okay, in twenty minutes: stretch.',
            'receipt': 'milo-reminder-ab12cd.timer',
            'action': 'create',
        })

    @mock.patch('reminders.list')
    def test_list_spoken_text_and_receipts(self, list_reminders):
        list_reminders.return_value = [
            {'id': 'ab12cd', 'unit': 'milo-reminder-ab12cd.timer', 'label': 'stretch',
             'when': '2026-09-12T14:20:00'},
        ]
        result = reminders.handle('list my reminders', NOW)
        self.assertEqual(result['spoken'], 'You have one reminder: stretch, at 2:20 PM.')
        self.assertEqual(result['receipt'], ['milo-reminder-ab12cd.timer'])
        self.assertEqual(result['action'], 'list')

    @mock.patch('reminders.cancel')
    @mock.patch('reminders.list')
    def test_cancel_by_label_has_receipt(self, list_reminders, cancel):
        list_reminders.return_value = [
            {'id': 'ab12cd', 'unit': 'milo-reminder-ab12cd.timer', 'label': 'stretch', 'when': 'x'},
        ]
        cancel.return_value = list_reminders.return_value[0]
        result = reminders.handle('cancel the stretch reminder', NOW)
        self.assertEqual(result, {
            'spoken': 'Cancelled the stretch reminder.',
            'receipt': 'milo-reminder-ab12cd.timer',
            'action': 'cancel',
        })
        cancel.assert_called_once_with('ab12cd')

    @mock.patch('reminders.create', side_effect=RuntimeError('systemd failed'))
    def test_failure_does_not_claim_success(self, _create):
        result = reminders.handle('set a timer for ten minutes', NOW)
        self.assertEqual(result['spoken'], 'I could not set that timer.')
        self.assertIn('error', result)
        self.assertNotIn('receipt', result)


class CliTests(unittest.TestCase):
    @mock.patch('reminders.handle')
    def test_text_cli_prints_result(self, handle):
        handle.return_value = {'spoken': 'Okay.', 'receipt': 'milo-reminder-ab12cd.timer', 'action': 'create'}
        output = StringIO()
        with redirect_stdout(output):
            status = reminders.main(['remind me in 1 minute to test'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), handle.return_value)

    @mock.patch('reminders.cancel')
    def test_cancel_cli_uses_id(self, cancel):
        cancel.return_value = {'id': 'ab12cd', 'unit': 'milo-reminder-ab12cd.timer'}
        with redirect_stdout(StringIO()):
            status = reminders.main(['--cancel', 'ab12cd'])
        self.assertEqual(status, 0)
        cancel.assert_called_once_with('ab12cd')



class SpokenTimeGrammarTests(unittest.TestCase):
    """Phrasings the 2026-09-13 bank showed reaching the chat model instead of a receipt."""

    def test_label_before_clock_and_wake_up(self):
        got = reminders.parse('Remind me to take the trash out at 7 pm.', NOW)
        self.assertEqual((got['kind'], got['when'], got['label']), ('create', '2026-09-12T19:00:00', 'take the trash out'))
        got = reminders.parse('Wake me up at 6 am.', NOW)
        self.assertEqual((got['when'], got['label']), ('2026-09-13T06:00:00', 'wake up'))
        got = reminders.parse('Remind me to water the plants tomorrow at 8', NOW)
        self.assertEqual((got['when'], got['label']), ('2026-09-13T08:00:00', 'water the plants'))

    def test_days_and_parts_of_day(self):
        got = reminders.parse('Remind me tomorrow morning to call the dentist.', NOW)
        self.assertEqual((got['when'], got['label']), ('2026-09-13T09:00:00', 'call the dentist'))
        got = reminders.parse('Set a reminder for Friday to pay rent.', NOW)  # NOW is a Saturday
        self.assertEqual((got['when'], got['label']), ('2026-09-18T09:00:00', 'pay rent'))
        got = reminders.parse('Remind me to call mom on Sunday', NOW)
        self.assertEqual((got['when'], got['label']), ('2026-09-13T09:00:00', 'call mom'))
        self.assertIsNone(reminders.parse('What time is it?', NOW))
        self.assertIsNone(reminders.parse('remind me to breathe', NOW))


if __name__ == '__main__':
    unittest.main()
