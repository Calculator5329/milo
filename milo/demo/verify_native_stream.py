#!/usr/bin/env python3
"""Real GStreamer clock, ordering and cancellation check, using a silent sink."""
import array
import base64
from native_audio import NativeAudio, GLib

rows=[]
loop=GLib.MainLoop()
player=NativeAudio(rows.append,sink='fakesink')
player.handle({'action':'pcm-start','id':'old'})
raw=base64.b64encode(array.array('f',[.1]*2400).tobytes()).decode()
for index in range(2):
 player.handle({'action':'pcm-audio','id':'old','sample_rate':24000,'index':index,'pcm':raw})
 player.handle({'action':'pcm-sentence-end','id':'old','index':index})
player.handle({'action':'pcm-end','id':'old'})
GLib.timeout_add(400,lambda: (loop.quit(),False)[1])
loop.run()
assert any(r['type']=='position' and r['index']==0 and r['level']>0 for r in rows),rows
assert any(r['type']=='position' and r['index']==1 for r in rows),rows
assert any(r['type']=='drained' for r in rows),rows
assert player.pipeline is None
player.handle({'action':'pcm-start','id':'new'})
player.handle({'action':'pcm-audio','id':'new','sample_rate':24000,'index':0,'pcm':raw})
player.handle({'action':'pcm-stop','id':'old'})
assert player.pipeline is not None,'Stale Stop cancelled current turn'
player.handle({'action':'pcm-stop','id':'new'})
assert player.pipeline is None and player.timer is None
count=len(rows)
GLib.timeout_add(150,lambda:(loop.quit(),False)[1]);loop.run()
assert len(rows)==count,'Cancelled native player emitted late events'
print('PASS: real native clock, PCM envelope, sentence order, EOS, stale Stop isolation, immediate Stop cleanup')
