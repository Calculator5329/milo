#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const demoPath = path.join(__dirname, "demo.js");
const source = fs.readFileSync(demoPath, "utf8");
const applyMatch = source.match(/\$\('#applyDraft'\)\.onclick=(async\(\)=>\{[^\n]+\});/);
const dismissMatch = source.match(/\$\('#dismissDraft'\)\.onclick=(\(\)=>\{[^\n]+\});/);
assert(applyMatch, "Could not find the Apply draft handler in demo.js");
assert(dismissMatch, "Could not find the Dismiss draft handler in demo.js");

const draftA = {id: "draft-a", name: "a.md", revision: "rev-a", content: "generated A"};
const draftB = {id: "draft-b", name: "b.md", revision: "rev-b", content: "generated B"};
const elements = {
  "#applyDraft": {disabled: false, onclick: null},
  "#dismissDraft": {disabled: false, onclick: null},
  "#draftText": {value: "edited A"},
  "#draftReceipt": {textContent: ""},
  "#draftReview": {hidden: false},
  "#showLastDraft": {hidden: true},
};
const messages = [];
let requestBody;
let shownDocument;
let releaseResponse;
const responseGate = new Promise(resolve => { releaseResponse = resolve; });

const sandbox = {
  applyingDraft: false,
  draftProposal: draftA,
  $: selector => elements[selector],
  api: async (_url, body) => {
    requestBody = JSON.parse(JSON.stringify(body));
    await responseGate;
    return {document: {name: "a.md", revision: "saved-a", content: "edited A", bytes: 8}};
  },
  showDocument: document => { shownDocument = document; },
  refreshFiles: () => {},
  message: (text, kind) => { messages.push({text, kind}); },
};

vm.runInNewContext(`${applyMatch[0]}\n${dismissMatch[0]}`, sandbox, {filename: demoPath});
const applying = elements["#applyDraft"].onclick();

assert.equal(requestBody.name, "a.md");
assert.equal(requestBody.draft_id, "draft-a");
assert.equal(elements["#applyDraft"].disabled, true);
assert.equal(elements["#dismissDraft"].disabled, true);
const controlsWereDisabledWhilePending =
  elements["#applyDraft"].disabled && elements["#dismissDraft"].disabled;

// A programmatic dismiss during the pending save must be ignored too.
elements["#dismissDraft"].onclick();
assert.equal(elements["#draftReview"].hidden, false);
assert.equal(elements["#showLastDraft"].hidden, true);
assert.equal(messages.length, 0);

// Reproduce a newer draft completing while A's document response is deferred.
sandbox.draftProposal = draftB;
elements["#draftReview"].hidden = false;
elements["#showLastDraft"].hidden = true;
releaseResponse();

applying.then(() => {
  assert.equal(shownDocument.name, "a.md");
  assert.equal(sandbox.draftProposal.id, "draft-b");
  assert.equal(elements["#draftReview"].hidden, false);
  assert.equal(elements["#showLastDraft"].hidden, true);
  assert.equal(elements["#applyDraft"].disabled, false);
  assert.equal(elements["#dismissDraft"].disabled, false);
  assert(messages.some(item => item.text === "Saved draft a.md quietly."));
  assert(!messages.some(item => item.text === "Saved draft b.md quietly."));

  process.stdout.write(JSON.stringify({
    passed: true,
    probe: "deferred apply A while newer draft B becomes current",
    actual_handlers_extracted: ["demo.js #applyDraft onclick", "demo.js #dismissDraft onclick"],
    request_saved: "a.md",
    saved_notice: "Saved draft a.md quietly.",
    newer_draft_id_after_response: sandbox.draftProposal.id,
    newer_draft_remains_reviewable: !elements["#draftReview"].hidden,
    controls_disabled_while_pending: controlsWereDisabledWhilePending,
    pending_dismiss_ignored: true,
    controls_reenabled_after_response: true,
    external_requests: 0,
  }, null, 2) + "\n");
}).catch(error => {
  process.stderr.write(error.stack + "\n");
  process.exitCode = 1;
});
