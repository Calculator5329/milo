const MAX_BYTES = 64 * 1024;
const DOCUMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,99}\.(?:md|txt)$/;

function normalizedName(name) {
  if (typeof name !== 'string') throw new Error('Choose a Markdown or text file.');
  const match = name.match(/\.(md|txt)$/i);
  const normalized = match ? name.slice(0, -match[0].length) + match[0].toLowerCase() : '';
  if (!normalized || normalized.includes('..') || !DOCUMENT_NAME.test(normalized)) {
    throw new Error('Use a leaf filename ending in .md or .txt (up to 104 characters).');
  }
  return normalized;
}

function boundedMessage(error, fallback) {
  const message = error instanceof Error && error.message ? error.message : fallback;
  return String(message).slice(0, 240);
}

function encodedBytes(content) {
  if (typeof content !== 'string') throw new Error('The current document is not text.');
  const bytes = new TextEncoder().encode(content).byteLength;
  if (bytes > MAX_BYTES) throw new Error('Document exceeds the 64 KiB limit.');
  return bytes;
}

/**
 * Add explicit file import/export controls to a supplied mount point.
 *
 * onImport receives {name, content} only after the user selects a validated
 * file. getCurrentDocument returns {name, content, label}; label should say
 * "saved document" or "current editor". The returned refresh() updates the
 * download label after the parent changes editor state.
 */
export function mountFileTransfer({mount, onImport, getCurrentDocument}) {
  if (!mount || typeof mount.append !== 'function') throw new TypeError('A mount element is required.');
  if (typeof onImport !== 'function' || typeof getCurrentDocument !== 'function') {
    throw new TypeError('Import and current-document callbacks are required.');
  }

  const importButton = document.createElement('button');
  importButton.type = 'button';
  importButton.className = 'file-transfer-import';
  importButton.textContent = 'Import';
  importButton.setAttribute('aria-label', 'Import one Markdown or text file');

  const downloadButton = document.createElement('button');
  downloadButton.type = 'button';
  downloadButton.className = 'file-transfer-download';
  downloadButton.textContent = 'Download';
  downloadButton.setAttribute('aria-label', 'Download the current document');
  downloadButton.disabled = true;

  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.md,.txt,text/markdown,text/plain';
  input.hidden = true;
  input.setAttribute('aria-hidden', 'true');
  input.tabIndex = -1;

  const status = document.createElement('span');
  status.className = 'file-transfer-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  mount.classList.add('file-transfer');
  mount.append(importButton, downloadButton, input, status);

  async function refresh() {
    try {
      const current = await getCurrentDocument();
      const available = current && typeof current.content === 'string' && typeof current.name === 'string';
      downloadButton.disabled = !available;
      const label = available && current.label ? String(current.label).slice(0, 80) : 'current document';
      downloadButton.textContent = available ? 'Download ' + label : 'Download';
      downloadButton.setAttribute(
        'aria-label',
        available ? 'Download ' + label + ' as a file' : 'Download the current document',
      );
    } catch {
      downloadButton.disabled = true;
      downloadButton.textContent = 'Download';
    }
  }

  importButton.addEventListener('click', () => {
    status.textContent = '';
    input.click();
  });

  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    importButton.disabled = true;
    status.textContent = 'Reading selected file…';
    try {
      const name = normalizedName(file.name);
      if (file.size > MAX_BYTES) throw new Error('Document exceeds the 64 KiB limit.');
      const bytes = await file.arrayBuffer();
      if (bytes.byteLength > MAX_BYTES) throw new Error('Document exceeds the 64 KiB limit.');
      let content;
      try {
        content = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
      } catch {
        throw new Error('Document must be valid UTF-8 text.');
      }
      encodedBytes(content);
      await onImport({name, content});
      status.textContent = 'Imported ' + name + '.';
      await refresh();
    } catch (error) {
      status.textContent = boundedMessage(error, 'The selected file could not be imported.');
    } finally {
      importButton.disabled = false;
    }
  });

  downloadButton.addEventListener('click', async () => {
    status.textContent = '';
    try {
      const current = await getCurrentDocument();
      if (!current) throw new Error('Open a document before downloading.');
      const name = normalizedName(current.name);
      encodedBytes(current.content);
      const type = name.endsWith('.md') ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8';
      const url = URL.createObjectURL(new Blob([current.content], {type}));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = name;
      anchor.hidden = true;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      const label = current.label ? String(current.label).slice(0, 80) : 'current document';
      status.textContent = 'Downloaded ' + label + '.';
    } catch (error) {
      status.textContent = boundedMessage(error, 'The document could not be downloaded.');
    }
  });

  queueMicrotask(refresh);
  return Object.freeze({refresh});
}
