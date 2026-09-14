# Milo conversation policy

The local conversation path now has three explicit answer shapes instead of one prompt trying to serve every question:

- `conversational`: one to three short sentences for ordinary discussion.
- `precise`: conclusion first, followed by the calculation, constraint, or compact procedure needed to trust it.
- `brainstorm`: at most three meaningfully different ideas, then a recommendation and its main tradeoff.

All three share the same behavior floor: answer directly in one spoken paragraph, use earlier completed conversation when a reference is clear, ask briefly when it is ambiguous, and state uncertainty when the available context cannot support a claim. Reference data stays visibly untrusted, action claims require successful receipts, and Milo cannot invent access. The system prompt is deliberately compact so it does not crowd out the current question.

## Integration contract

`conversation.py` owns model-facing conversation policy only. The runtime still owns document access, web search, actions, history retention, cancellation, and receipts.

```python
from conversation import build_messages, ollama_model_options, split_spoken_sentence

context_summary = {}
messages = build_messages(
    current_text,
    model=model,
    num_ctx=4096,
    mode=turn.options.get("mode", "conversational"),
    history=heard_history,
    web_sources=cleaned_sources,
    selected_document={
        "name": document_name,
        "content": bounded_excerpt,
        "revision": revision,
        "truncated": was_truncated,
    } if document_name else None,
    action_receipts=runtime_receipts,
    context_summary=context_summary,
)

payload = {
    "model": model,
    "messages": messages,
    "stream": True,
    **ollama_model_options(model, mode, num_ctx=4096),
}
```

History contains only user turns and assistant sentences that completed playback. Search results, selected-document text, and action records go into the separate `REFERENCE_DATA` message produced by `build_messages`; callers should not put them into the system prompt or ordinary history. The system policy tells the model those records are data, never instructions. It also tells Milo to describe a preview or result naturally without exposing internal transport language to the user.

`build_messages` now owns the aggregate context envelope. It always keeps the compact system policy and the complete current question. It then prefers the newest completed history message, latest successful action receipts, the selected-document excerpt, ranked web sources, and older history. Documents and individual source fields may be shortened to fit; their JSON records set `truncated: true`, while omitted source and receipt counts are explicit. Retained history remains in its original chronological order. If the system policy and current question alone do not fit, the function rejects the question instead of silently cutting it.

Pass an empty dictionary as `context_summary` when the caller must disclose what reached the model. After successful assembly it contains `history_kept`, `history_omitted`, an isolated `references` snapshot, `conservative_units`, and `input_allowance`. This is exact builder state rather than parsed `REFERENCE_DATA` text, so ordinary history cannot spoof the UI disclosure. The units describe the conservative byte envelope below and must not be labeled as actual tokens.

The caller must pass the same `model` and `num_ctx` to `build_messages` that it passes to `ollama_model_options`. The current demo call needs `model=turn.options['model']`; otherwise the safe default is `gpt-oss:20b` and optional Phi-4 or Qwen turns receive an unnecessarily strict GPT OSS allowance.

An action receipt must come from the runtime that attempted the action. Do not create one from model narration. A successful receipt can support “the note was saved”; a preview, failure, or missing receipt cannot. The current demo performs document actions silently, so receipts mostly matter for future conversational follow-ups about what happened.

`split_spoken_sentence` is the streaming seam. It skips `1.`/`2.`/`3.` list markers as sentence boundaries and passes each completed unit through `normalize_spoken_answer`. The normalizer removes Markdown, raw links, canned opening filler, and visual list markers without truncating content. This fallback matters because GPT OSS sometimes emits a formatted list even when the prompt requests plain prose.

The default system prompt is portable and does not contain an owner name. A host may pass an explicitly configured `owner_name`; it should not infer one from document content or conversation text.

GPT OSS keeps `think: low`, `num_ctx: 4096`, and at least 1,600 output tokens. That larger generation allowance is reasoning headroom, not a request for a long spoken answer: an earlier 700-token configuration exhausted its budget before emitting visible content. Faster non-thinking models use each profile's smaller answer budget directly.

Milo's isolated runtime does not currently contain `tiktoken`, `openai-harmony`, Transformers, or another tokenizer package. OpenAI's [GPT OSS reference tokenizer](https://github.com/openai/gpt-oss/blob/main/gpt_oss/tokenizer.py) identifies `o200k_harmony`, and the [Harmony format](https://github.com/openai/harmony/blob/main/docs/format.md) defines the chat framing tokens. Context assembly therefore uses an explicit UTF-8 byte envelope plus fixed per-message and assistant-frame reserves, then subtracts the configured output allowance. For GPT OSS, byte-pair encoding makes content bytes a conservative upper bound on content tokens. For Phi-4 and Qwen this is an intentionally strict fallback measurement, not an exact token count or a claim about their tokenizer families. No tokenizer or model dependency was added.

The local `DemoEngine` uses these seams now. Its API backend deliberately keeps a separate current-question-only provider policy, so local profile changes do not silently alter cloud context or data-sharing behavior.

## Bounded local-model probe

The retained evidence is [`evidence/conversation-comparison.json`](evidence/conversation-comparison.json). It contains 14 sequential GPT OSS 20B requests at concurrency 1 and `num_ctx: 4096`: six existing-prompt/candidate pairs and two candidate-v2 spot checks. Every prompt, answer, first-content time, total time, output-token count, and thinking-character count is recorded.

This was a behavior probe, not an intelligence or quality benchmark. In the six paired samples, baseline median first content was 844.5 ms and candidate-v1 was 998.5 ms; baseline median total time was 1714.5 ms and candidate-v1 was 1988.5 ms. One sequential sample per case cannot establish a latency regression, and these figures exclude transcription, TTS, browser scheduling, and speaker hardware.

The probe did useful work by exposing failures. Candidate v1's precise answer said the tasks totaled 30 minutes, then suggested postponing the prerequisite booking until after the report. The revised precise prompt explicitly checks that arithmetic and dependencies support the recommendation. Its repeat answered that the 25-minute window fits the booking and report, but not the email. Both baseline and candidate correctly rejected an instruction embedded in the selected launch document, identified the unrun rollback drill from the document data, and said that a preview did not update the file. Those are observations from these answer texts, not general correctness measurements.

GPT OSS still formatted the brainstorm answer as a numbered Markdown list after the stronger plain-prose instruction. The deterministic speech splitter/normalizer is therefore part of the integration rather than treating prompt compliance as guaranteed. The policy can reduce recurring failure patterns; it cannot make local-model reasoning or grounding a gate.

## Installed Pocket TTS controls and useful voice options

The installed package is Pocket TTS 3.1.0. Its local `TTSModel.load_model` exposes generation temperature, sampler decode steps, noise clamp, EOS threshold, and optional quantization. `generate_audio` and `generate_audio_stream` expose chunk token size and frames after EOS. The voice state carries speaker character and prosody. There is no speaking-rate or robot-character parameter on either generation method.

Those controls should not be mislabeled in the UI. Temperature changes generation variation, sampler steps trade computation for decode behavior, frames after EOS changes the tail, and token size changes text chunking. None is a direct words-per-minute control. Changing them per audition would also confound the voice comparison, so keep Pocket TTS at its model-recommended defaults while choosing a speaker.

The shipped demo applies these browser playback treatments without downloading new voices:

- `Natural`: near-full-band voice playback (20 Hz high-pass, a low-pass up to 20 kHz, and no presence boost) at rate 1.0.
- `Clear`: 75 Hz high-pass, 9 kHz low-pass and a 2 dB presence boost at 2.2 kHz. Whether this helps intelligibility is an audition question, not a measured improvement.
- `Small speaker`: 180 Hz high-pass, 4.8 kHz low-pass and a 1 dB presence boost at 2.2 kHz. This is a character choice and can remove warmth and consonant detail. The original conversation app has its separate, earlier filter settings.
- `Pace`: playback choices of 0.96, 1.0, and 1.04. Web Audio's basic playback rate also shifts pitch, so the range should stay narrow. Wider rate control needs a pitch-preserving time-stretch implementation and is not justified for this demo.

The next useful voice comparison is the same short dialogue and one real answer across Natural, Clear, and Small speaker at rate 1.0, followed by the chosen sound at the two narrow rate variants. Voice fit and intelligibility remain owner judgments. No additional voice download, model change, or paid speech service is needed for that audition.

## Voice Lab fourth-tab integration

`voice-lab.js` exports `mountVoiceLab(root, client)`. It renders the five installed voices, the three conversation profiles, three playback colors, three narrow playback rates, and one fixed-text audition. Only allowlisted preferences are restored. Local storage provides a startup cache; `preferences.js` synchronizes the canonical retained settings record across notebook and native windows. Changes update `client.voice`, `client.mode`, `client.setSoundProfile(profile)`, and `client.setPlaybackRate(rate)`. The audition calls `client.submit('', {audition: true})`; Stop calls `client.stop()`. No microphone path is used.

The integrated demo server includes these entries in its fixed asset map:

```python
'/voice-lab.js': ('voice-lab.js', 'text/javascript'),
'/voice-lab.css': ('voice-lab.css', 'text/css'),
```

The integrated `index.html` includes the stylesheet, fourth tab, and mount point:

```html
<link rel="stylesheet" href="/voice-lab.css">
<button data-tab="voice" aria-selected="false">04 · Voice Lab</button>
<section id="voice" class="tab-panel" hidden>
  <div class="section-head"><div><h2>Which Milo can you live with?</h2><p>Same words, same local speech model. Choose by listening.</p></div><span class="chip">HUMAN AUDITION</span></div>
  <div id="voiceLab"></div>
</section>
```

Forward the existing client events to the module, then mount it after the client exists:

```javascript
import {mountVoiceLab} from '/voice-lab.js';
let voiceLab;
const client = new MiloClient(event => {
  voiceLab?.handleEvent(event);
  // existing workbench event handling
});
voiceLab = mountVoiceLab(document.querySelector('#voiceLab'), client);
```

`MiloClient.setSoundProfile` should accept only `natural`, `clear`, or `small`; `setPlaybackRate` should accept only `0.96`, `1`, or `1.04`. Apply the rate to each `AudioBufferSourceNode` before `start()`, and schedule the next chunk at `start + buffer.duration / playbackRate`; using the unadjusted duration would overlap slow playback and leave gaps after fast playback. Build the filter graph once and switch gains or connections, so changing a preference does not stack filters. The Voice Lab emits a bubbling `milo-preference-change` event with the allowlisted values so the existing notebook voice/mode controls can mirror them.

## Limits carried into review

- The prompt and normalizer improve response shape; neither verifies factual truth.
- The selected document is only a bounded excerpt and may be shortened again by aggregate context assembly. Its `truncated` field records that condition.
- Search snippets can be incomplete or wrong and are not full-page evidence.
- The byte envelope is conservative capacity control, not a reported token measurement. Passing matching `model` and `num_ctx` values at both conversation seams is required.
- The 14 model turns are retained examples, not a score or population estimate.
- Pocket TTS voice preference, physical audibility, and real-room intelligibility were not measured in this pass.


## Final autonomous-window behavior samples

`verify_conversation_quality.py` retained eight synthetic questions covering a feasible schedule, a contextual theme follow-up, unsaved/saved receipts, a quoted instruction inside a document, an undecided launch date, brainstorming, and a question containing action words. These use the existing local Ollama endpoint and exclude microphone, TTS and physical playback. Expected evidence is a human-review rubric, not an automatic score. The three answer sets remain under `evidence/conversation-final*.json` with policy notes; imperfect answers were preserved.

The first scheduling answer correctly said the work totaled 30 minutes but omitted the requested feasible alternative. Intermediate prompt wording sometimes called a 30-minute schedule feasible before acknowledging the 25-minute limit. The final precise rule asks for a feasible subset without relaxing constraints. In its final spot-check, GPT OSS proposed booking then report for 20 minutes and deferred the email. The generic brainstorm rule avoids unverified product suggestions; an intermediate answer had invented a relevant-seeming guest-mode app recommendation.

Stronger comparison-grounding wording did not consistently prevent unsupported display/eye-strain claims, so the ineffective extra rule was removed rather than treated as a guard. The final GPT OSS spot-check used the supplied bright-room contrast rationale without the earlier eye-strain claim. That one answer is not proof of a general fix. The broader samples correctly distinguished unsaved proposals from successful receipts and rejected the quoted instruction in the launch document, while some responses remained longer or more expansive than requested.

A two-question Phi-4 comparison is retained in `evidence/conversation-final-model-spotcheck.json`. Its theme follow-up was concise, but its schedule response explicitly claimed 5 + 10 + 15 could finish in 25 minutes. GPT OSS remains the default; neither this one comparison nor the earlier samples establish general intelligence or quality rankings. Latencies include sequential model-load conditions and must not be treated as a controlled speed comparison.
