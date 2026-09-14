"""Continuous native PCM playback; one cancellable timeline per conversation turn."""
import array
import base64
from collections import deque
import math
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
Gst.init(None)


class NativeAudio:
    def __init__(self, notify, sink='pulsesink'):
        self.notify = notify
        self.sink = sink
        self.pipeline = None
        self.timer = None
        self.turn = None
        self.available = all(Gst.ElementFactory.find(name) for name in
                             ('appsrc', 'audioconvert', 'audioresample', sink))

    def stop(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.pipeline is not None:
            self.bus.remove_signal_watch()
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.bus = None
        self.turn = None

    def handle(self, message):
        action = message['action']
        turn = message.get('id')
        if action == 'pcm-start':
            self.stop()
            self.turn = turn
            self.frames, self.markers = deque(), deque()
            self.samples = 0
            self.rate = None
            self.last_index = None
            return
        if self.turn is None or turn != self.turn: return
        if action == 'pcm-stop':
            self.stop()
        elif action == 'pcm-audio':
            self.push(message)
        elif action == 'pcm-sentence-end':
            self.markers.append((self.samples, message['index']))
        elif action == 'pcm-end':
            if self.pipeline:
                self.source.emit('end-of-stream')
            else:
                self.send(type='drained')
                self.stop()

    def send(self, **value):
        self.notify(dict(id=self.turn, **value))

    def push(self, message):
        rate = message['sample_rate']
        if rate != 24000: raise ValueError('Unsupported native stream sample rate')
        raw = base64.b64decode(message['pcm'], validate=True)
        if not raw or len(raw) % 4: raise ValueError('Invalid PCM chunk')
        if self.pipeline is None:
            self.rate = rate
            self.pipeline = Gst.parse_launch(
                'appsrc name=source format=time ! audioconvert ! audioresample ! '+self.sink+' sync=true')
            self.source = self.pipeline.get_by_name('source')
            self.source.set_property('caps', Gst.Caps.from_string(
                f'audio/x-raw,format=F32LE,rate={rate},channels=1,layout=interleaved'))
            self.bus = self.pipeline.get_bus()
            self.bus.add_signal_watch()
            self.bus.connect('message', self.on_bus)
            self.pipeline.set_state(Gst.State.PLAYING)
            self.timer = GLib.timeout_add(20, self.tick)
        valid, position = self.pipeline.query_position(Gst.Format.TIME)
        if valid and position * rate // Gst.SECOND > self.samples:
            # Resume after a synthesis stall on the sink's current timeline.
            self.samples = position * rate // Gst.SECOND + rate // 50
        values = array.array('f')
        values.frombytes(raw)
        frame = rate // 50
        for offset in range(0, len(values), frame):
            part = values[offset:offset+frame]
            rms = math.sqrt(sum(x*x for x in part)/len(part))
            self.frames.append((self.samples+offset, self.samples+offset+len(part), rms, message['index']))
        buffer = Gst.Buffer.new_wrapped(raw)
        buffer.pts = self.samples * Gst.SECOND // rate
        buffer.duration = len(values) * Gst.SECOND // rate
        self.samples += len(values)
        if self.source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
            raise ValueError('Native audio rejected the stream')

    def tick(self):
        if self.pipeline is None: return False
        valid, position = self.pipeline.query_position(Gst.Format.TIME)
        if not valid: return True
        sample = position * self.rate // Gst.SECOND
        while self.frames and self.frames[0][1] <= sample: self.frames.popleft()
        completed = []
        while self.markers and self.markers[0][0] <= sample:
            completed.append(self.markers.popleft()[1])
        level, index = 0, None
        if self.frames and self.frames[0][0] <= sample:
            _, _, rms, index = self.frames[0]
            level = min(1, max(0, (rms-.003)/.08))
        self.send(type='position', level=level, index=index, completed=completed)
        return True

    def on_bus(self, _bus, message):
        if self.pipeline is None or _bus != self.bus: return
        if message.type == Gst.MessageType.ERROR:
            self.send(type='error', message='Native streaming audio failed.')
            self.stop()
        elif message.type == Gst.MessageType.EOS:
            self.send(type='drained', completed=[index for _, index in self.markers])
            self.stop()
