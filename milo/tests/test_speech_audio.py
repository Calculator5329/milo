"""Exercise real Rubber Band pitch/tempo separation on synthetic audio."""
import array
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_audio import process, settings, stream


RUBBERBAND_REASON = 'Voice adjustment needs FFmpeg with Rubber Band support.'


def rubberband_available():
    """Probe the exact optional ffmpeg filter required by the transform tests."""
    if os.environ.get('MILO_TEST_FORCE_NO_RUBBERBAND') == '1':
        return False
    try:
        probe = subprocess.run(
            ['ffmpeg', '-hide_banner', '-filters'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and re.search(r'\brubberband\b', probe.stdout + probe.stderr) is not None


requires_rubberband = unittest.skipUnless(rubberband_available(), RUBBERBAND_REASON)


class SpeechAudioTests(unittest.TestCase):
    @requires_rubberband
    def test_pitch_changes_frequency_without_changing_pace(self):
        rate=24000
        signal=array.array('f',(0.2*math.sin(2*math.pi*220*i/rate) for i in range(rate*2)))
        out=array.array('f');out.frombytes(process(signal.tobytes(),rate,{'pitch':6,'rate':1}))
        middle=out[rate//4:-rate//4]
        crossings=sum(a<=0<b for a,b in zip(middle,middle[1:]))
        frequency=crossings/(len(middle)/rate)
        self.assertAlmostEqual(len(out)/rate,2,delta=.08)
        self.assertAlmostEqual(frequency,220*2**.5,delta=6)

    @requires_rubberband
    def test_pace_changes_duration_without_changing_pitch(self):
        rate=24000
        signal=array.array('f',(0.2*math.sin(2*math.pi*220*i/rate) for i in range(rate*2)))
        out=array.array('f');out.frombytes(process(signal.tobytes(),rate,{'pitch':0,'rate':2}))
        middle=out[rate//5:-rate//5]
        frequency=sum(a<=0<b for a,b in zip(middle,middle[1:]))/(len(middle)/rate)
        self.assertAlmostEqual(len(out)/rate,2/2,delta=.09)
        self.assertAlmostEqual(frequency,220,delta=5)

    @requires_rubberband
    def test_stream_emits_before_input_finishes_and_close_stops_consumption(self):
        consumed=[]
        def chunks():
            for i in range(200):
                consumed.append(i)
                yield array.array('f',[.1]*2400).tobytes()
        output=stream(chunks(),24000,{'pitch':3,'rate':1.65})
        self.assertGreater(len(next(output)),0)
        self.assertLess(len(consumed),200)
        count=len(consumed)
        output.close()
        self.assertEqual(len(consumed),count)

    @requires_rubberband
    def test_cancelled_transform_does_not_consume_input(self):
        def chunks():
            self.fail('Cancelled transform consumed input')
            yield b''
        self.assertEqual(list(stream(chunks(),24000,{'pitch':3},lambda:True)),[])
    def test_neutral_is_exact_and_ranges_reject_invalid_values(self):
        raw=array.array('f',[.1,0,-.1]).tobytes()
        self.assertEqual(process(raw,24000,{}),raw)
        for bad in ({'rate':True},{'rate':float('nan')},{'pitch':float('inf')},{'pitch':9},{'rate':.5}):
            with self.assertRaises(ValueError): settings(bad)

if __name__=='__main__':unittest.main()
