"""Apply independent pitch, pace and sound color to one local PCM sentence."""
import math
import subprocess
import selectors
import os
import time


def settings(values):
    result = {}
    for name, default, lower, upper in (('rate', 1, .65, 2), ('pitch', 0, -8, 8)):
        value = values.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError('Unsupported voice '+name+'.')
        result[name] = round(value, 2)
    sound = values.get('sound', 'natural')
    if sound == 'small-speaker': sound = 'small'
    if sound not in ('natural', 'clear', 'small'): raise ValueError('Unsupported voice sound.')
    result['sound'] = sound
    return result


def filter_chain(options):
    value = settings(options)
    filters = []
    if value['pitch'] or value['rate'] != 1:
        filters.append(f"rubberband=tempo={value['rate']}:pitch={2 ** (value['pitch']/12):.8f}")
    if value['sound'] == 'clear':
        filters.extend(('highpass=f=75', 'lowpass=f=9000', 'equalizer=f=2200:t=q:w=.7:g=2'))
    elif value['sound'] == 'small':
        filters.extend(('highpass=f=180', 'lowpass=f=4800', 'equalizer=f=2200:t=q:w=.7:g=1'))
    return filters


def stream(chunks, sample_rate, options, cancelled=lambda: False):
    """Adjust incremental mono F32LE audio, closing the child on Stop or failure."""
    filters = filter_chain(options)
    if not 8000 <= sample_rate <= 96000:
        raise ValueError('Unsupported audio sample rate.')
    if not filters:
        for chunk in chunks:
            if cancelled(): return
            yield chunk
        return
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-probesize', '32',
               '-analyzeduration', '0', '-f', 'f32le', '-ar', str(sample_rate),
               '-ac', '1', '-i', 'pipe:0', '-af', ','.join(filters),
               '-flush_packets', '1', '-f', 'f32le', '-ar', str(sample_rate),
               '-ac', '1', 'pipe:1']
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, bufsize=0)
    except OSError:
        raise ValueError('Voice adjustment needs FFmpeg with Rubber Band support.') from None
    pending = b''
    remainder = b''
    total = 0
    deadline = time.monotonic() + 30
    source = iter(chunks)
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in (child.stdin, child.stdout): os.set_blocking(pipe.fileno(), False)
            selector.register(child.stdout, selectors.EVENT_READ)
            selector.register(child.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                if cancelled(): return
                if time.monotonic() > deadline:
                    raise ValueError('Voice adjustment timed out.')
                for key, _ in selector.select(.05):
                    if key.fileobj is child.stdout:
                        data = os.read(child.stdout.fileno(), 24000)
                        if not data:
                            selector.unregister(child.stdout)
                            continue
                        data = remainder + data
                        size = len(data) // 4 * 4
                        remainder = data[size:]
                        if size: yield data[:size]
                    else:
                        if not pending:
                            try: pending = next(source)
                            except StopIteration:
                                selector.unregister(child.stdin)
                                child.stdin.close()
                                continue
                            total += len(pending)
                            if total > sample_rate * 4 * 90 or len(pending) % 4:
                                raise ValueError('Voice sentence exceeds the processing limit.')
                        if pending:
                            written = os.write(child.stdin.fileno(), pending)
                            pending = pending[written:]
            if child.wait(timeout=2) or remainder:
                raise ValueError('Voice adjustment failed. Try neutral pitch and pace.')
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Voice adjustment failed. Try neutral pitch and pace.') from None
    finally:
        if child.poll() is None: child.kill()
        child.wait()
        child.stdin.close()
        child.stdout.close()
        close = getattr(source, 'close', None)
        if close: close()


def process(pcm, sample_rate, options):
    """Offline convenience wrapper over the same streaming transform."""
    return b''.join(stream((pcm,), sample_rate, options))
