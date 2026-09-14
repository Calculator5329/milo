#!/usr/bin/env python3
"""Opt-in real voice/native output probe. Plays one reply and records actual pipeline events."""
import argparse
import json
import os
import threading
import time
import urllib.request
import uuid
from pathlib import Path
os.environ.setdefault('GST_PLUGIN_PATH',str(Path.home()/'.cache/tmp/milo-native-gst/plugins'))
from native_audio import NativeAudio, GLib

parser=argparse.ArgumentParser()
parser.add_argument('--output',required=True)
parser.add_argument('--baseline',action='store_true')
parser.add_argument('--audition',action='store_true')
args=parser.parse_args()
base='http://127.0.0.1:8776'
turn=str(uuid.uuid4())
start=time.monotonic()
result={'kind':'native sink position; not physical listening','baseline':args.baseline,'audition':args.audition,
        'question':'Why do leaves change color in autumn?','pitch':3,'rate':1.65,'voice':'peter_yearsley'}
loop=GLib.MainLoop()
def ms(): return round((time.monotonic()-start)*1000)
def notify(value):
 if value['type']=='position' and value['index'] is not None:
  result.setdefault('first_native_position_ms',ms())
 if value['type']=='error':result['error']=value['message'];loop.quit()
 if value['type']=='drained':result['drained_ms']=ms();loop.quit()
player=NativeAudio(notify)
def deliver(value):
 player.handle(value)
 return False

def request():
 try:
  if not args.baseline: GLib.idle_add(deliver,{'action':'pcm-start','id':turn})
  payload={'audition':args.audition,'id':turn,'text':result['question'],'voice':'peter_yearsley','model':'gpt-oss:20b','mode':'conversational','pitch':3,'rate':1.65,'sound':'natural'}
  if args.audition: payload.pop('text')
  req=urllib.request.Request(base+'/api/demo/turn',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Origin':base})
  with urllib.request.urlopen(req,timeout=90) as response:
   for line in response:
    row=json.loads(line)
    if row['type']=='audio':
     result.setdefault('first_audio_ms',ms())
     if not args.baseline:GLib.idle_add(deliver,dict(row,action='pcm-audio'))
    if row['type']=='sentence_end':
     result.setdefault('first_sentence_end_ms',ms())
     if not args.baseline:GLib.idle_add(deliver,dict(row,action='pcm-sentence-end'))
    if row['type']=='done':
     result['server_done_ms']=ms();result['server_metrics']=row.get('metrics')
     if args.baseline:GLib.idle_add(loop.quit)
     else:GLib.idle_add(deliver,{'action':'pcm-end','id':turn})
    if row['type']=='error':raise RuntimeError(row['message'])
 except Exception as error:
  result['error']=str(error);GLib.idle_add(loop.quit)
threading.Thread(target=request,daemon=True).start()
def timeout():result['error']='probe timed out';loop.quit();return False
GLib.timeout_add_seconds(100,timeout)
loop.run()
player.stop()
Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
raise SystemExit(bool(result.get('error')))
