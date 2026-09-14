"""Known speech mishearings are repaired before routing, and receipts list each repair."""
import unittest

import hearing


class HearingTests(unittest.TestCase):
    def test_technical_names_are_repaired(self):
        cases = {
            'How do I reload Hyperland?': 'How do I reload Hyprland?',
            'Update everything with Pac-Man': 'Update everything with pacman',
            'list my system empty timers': 'list my systemd timers',
            'is bit rifts better than ext4': 'is btrfs better than ext4',
            'what is 100 fair and height in celsius': 'what is 100 Fahrenheit in celsius',
            'push it to git hub': 'push it to GitHub',
        }
        for heard, fixed in cases.items():
            self.assertEqual(hearing.correct(heard), fixed, heard)

    def test_plain_text_is_untouched_and_corrections_are_listed(self):
        text = 'What is the capital of Mongolia?'
        self.assertEqual(hearing.correct(text), text)
        self.assertEqual(hearing.corrections(text), [])
        self.assertEqual(hearing.corrections('Hyperland on Pac-Man'), [('Hyperland', 'Hyprland'), ('Pac-Man', 'pacman')])
        self.assertEqual(hearing.correct(''), '')


if __name__ == '__main__':
    unittest.main()
