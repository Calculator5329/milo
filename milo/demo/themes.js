const THEME_STORAGE_KEY = 'milo-demo-theme';

export const MILO_THEMES = Object.freeze([
  {
    id: 'workshop',
    name: 'Workshop',
    note: 'Moss, canvas, and warm brass',
    swatches: ['#152019', '#a8cbae', '#decda1'],
    robot: {
      shell: '#e5d1a9', shellLight: '#f3e5c7', shellShade: '#b9a786',
      edge: '#393a32', joint: '#54574e', screen: '#182321',
      face: '#a4c7a0', copper: '#c48f5c', ground: '#0a100c', eyeGlow: '#d3e6b8',
    },
  },
  {
    id: 'blueprint',
    name: 'Drafting desk',
    note: 'Cobalt ink on technical paper',
    swatches: ['#e7edf0', '#164f68', '#d15c3c'],
    robot: {
      shell: '#d9e1e2', shellLight: '#f4f6f2', shellShade: '#a7b7bd',
      edge: '#123d4d', joint: '#416776', screen: '#113746',
      face: '#b8e8ea', copper: '#d15c3c', ground: '#123d4d', eyeGlow: '#efffff',
    },
  },
  {
    id: 'hifi',
    name: 'Hi-fi console',
    note: 'Black lacquer and signal blue',
    swatches: ['#0c0d0c', '#4fb3ff', '#d75432'],
    robot: {
      shell: '#343632', shellLight: '#585b54', shellShade: '#1e201e',
      edge: '#090a09', joint: '#121412', screen: '#070b0e',
      face: '#4fb3ff', copper: '#d75432', ground: '#000000', eyeGlow: '#c6e6ff',
    },
  },
  {
    id: 'library',
    name: 'Reading room',
    note: 'Oxblood leather and old paper',
    swatches: ['#241512', '#c6a46a', '#8d3e38'],
    robot: {
      shell: '#cbb78d', shellLight: '#eadfc5', shellShade: '#957f61',
      edge: '#3b221b', joint: '#594039', screen: '#2e1715',
      face: '#e2ba72', copper: '#8d3e38', ground: '#1d0d0b', eyeGlow: '#ffe0a3',
    },
  },
  {
    id: 'moonroom',
    name: 'Moonroom',
    note: 'Silver light and deep blue glass',
    swatches: ['#0c1520', '#acd8e8', '#f0ede4'],
    robot: {
      shell: '#aab8c2', shellLight: '#e4eaec', shellShade: '#71818e',
      edge: '#17222c', joint: '#344551', screen: '#0a1a25',
      face: '#acd8e8', copper: '#d8d1bd', ground: '#03080d', eyeGlow: '#e7fbff',
    },
  },
]);

const themeById = new Map(MILO_THEMES.map(theme => [theme.id, theme]));

function readStoredTheme() {
  try { return window.localStorage.getItem(THEME_STORAGE_KEY); } catch { return null; }
}

function storeTheme(id) {
  try { window.localStorage.setItem(THEME_STORAGE_KEY, id); } catch {
    // Storage can be unavailable in privacy modes; the active page still changes.
  }
}

function initialTheme() {
  const requested = new URL(window.location.href).searchParams.get('theme');
  if (themeById.has(requested)) return requested;
  const saved = readStoredTheme();
  return themeById.has(saved) ? saved : MILO_THEMES[0].id;
}

function swatches(theme) {
  const row = document.createElement('span');
  row.className = 'milo-theme-swatches';
  row.setAttribute('aria-hidden', 'true');
  for (const color of theme.swatches) {
    const swatch = document.createElement('i');
    swatch.style.setProperty('--swatch', color);
    row.append(swatch);
  }
  return row;
}

function themePicker(onSelect) {
  const details = document.createElement('details');
  details.className = 'milo-theme-picker';

  const summary = document.createElement('summary');
  summary.innerHTML = '<span>Look</span><strong></strong>';
  details.append(summary);

  const panel = document.createElement('div');
  panel.className = 'milo-theme-menu';
  panel.setAttribute('role', 'radiogroup');
  panel.setAttribute('aria-label', 'Milo visual theme');
  panel.insertAdjacentHTML(
    'afterbegin',
    '<p><span>Choose a room</span><small>Same Milo. Different atmosphere.</small></p>',
  );

  for (const theme of MILO_THEMES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.themeChoice = theme.id;
    button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', 'false');
    button.tabIndex = -1;
    button.append(swatches(theme));

    const copy = document.createElement('span');
    copy.className = 'milo-theme-copy';
    const name = document.createElement('strong');
    name.textContent = theme.name;
    const note = document.createElement('small');
    note.textContent = theme.note;
    copy.append(name, note);
    button.append(copy);
    button.addEventListener('click', () => {
      onSelect(theme.id);
      details.open = false;
      summary.focus();
    });
    panel.append(button);
  }

  panel.addEventListener('keydown', event => {
    const buttons = [...panel.querySelectorAll('[role="radio"]')];
    const index = buttons.indexOf(document.activeElement);
    let next = index;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % buttons.length;
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index - 1 + buttons.length) % buttons.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = buttons.length - 1;
    else return;
    event.preventDefault();
    buttons[next].focus();
  });

  details.append(panel);
  return details;
}

function robotVariables(palette) {
  return Object.entries(palette)
    .map(([name, value]) => '--' + name + ':' + value)
    .join(';');
}

async function loadRobotSource(source) {
  const response = await fetch(source, { cache: 'force-cache' });
  if (!response.ok) throw new Error('Robot asset returned ' + response.status);
  return response.text();
}

export function initMiloThemes({
  mount = document.querySelector('body > header') ?? document.querySelector('#panel'),
  robotSelector = 'img[src$="/robot.svg"], img[data-milo-robot], [data-milo-inline]',
  robotAsset = '/robot.svg',
  syncUrl = true,
} = {}) {
  if (!mount || document.querySelector('.milo-theme-picker')) return null;

  let current = initialTheme();
  let robotSource = null;

  const status = document.createElement('span');
  status.className = 'milo-theme-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const updatePicker = picker => {
    const theme = themeById.get(current);
    picker.querySelector('summary strong').textContent = theme.name;
    for (const button of picker.querySelectorAll('[data-theme-choice]')) {
      const selected = button.dataset.themeChoice === current;
      button.setAttribute('aria-checked', String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
  };

  const refreshRobotArt = async () => {
    try {
      robotSource ??= await loadRobotSource(robotAsset);
      const theme = themeById.get(current);
      const themedSource = robotSource.replace(
        '</svg>',
        '<style>:root{' + robotVariables(theme.robot) + '}</style></svg>',
      );
      const nextUrl = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(themedSource);
      for (const image of document.querySelectorAll(robotSelector)) {
        if (image.hasAttribute('data-milo-inline')) {
          image.innerHTML = themedSource.replaceAll(':root{', '#robot svg{').replace('</svg>', '<ellipse class="talk-mouth" cx="249" cy="197" rx="15" ry="9" fill="var(--face)" stroke="none"/></svg>');
        } else {
          image.dataset.miloRobot = current;
          image.src = nextUrl;
        }
      }
    } catch {
      // The page remains usable with its original robot when an asset cannot be loaded.
    }
  };

  let picker;
  const setTheme = (id, { announce = true } = {}) => {
    if (!themeById.has(id)) return false;
    current = id;
    const theme = themeById.get(id);
    document.documentElement.dataset.miloTheme = id;
    document.documentElement.style.colorScheme = id === 'blueprint' ? 'light' : 'dark';
    storeTheme(id);
    if (syncUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set('theme', id);
      window.history.replaceState(null, '', url);
    }
    updatePicker(picker);
    void refreshRobotArt();
    if (announce) status.textContent = theme.name + ' theme selected. ' + theme.note + '.';
    if (announce) window.dispatchEvent(new CustomEvent('milo-theme-change', { detail: { id, theme } }));
    return true;
  };

  picker = themePicker(id => setTheme(id));
  const compact = mount.id === 'panel';
  picker.classList.toggle('milo-theme-picker--compact', compact);
  picker.addEventListener('toggle', () => {
    if (picker.open) picker.querySelector('[aria-checked="true"]')?.focus();
  });

  const insertionPoint = compact ? mount.querySelector('.scope') : mount.querySelector('.back');
  if (insertionPoint) mount.insertBefore(picker, insertionPoint);
  else mount.append(picker);
  mount.append(status);
  setTheme(current, { announce: false });

  const observer = new MutationObserver(records => {
    const hasNewRobot = records.some(record => [...record.addedNodes].some(node =>
      node.nodeType === Node.ELEMENT_NODE &&
      (node.matches?.(robotSelector) || node.querySelector?.(robotSelector))
    ));
    if (hasNewRobot) void refreshRobotArt();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  const closeOnOutsideClick = event => {
    if (picker.open && !picker.contains(event.target)) picker.open = false;
  };
  document.addEventListener('pointerdown', closeOnOutsideClick);

  return {
    get current() { return current; },
    setTheme,
    refreshRobotArt,
    destroy() {
      observer.disconnect();
      document.removeEventListener('pointerdown', closeOnOutsideClick);
      picker.remove();
      status.remove();
    },
  };
}

function autoInit() {
  window.miloThemes ??= initMiloThemes();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', autoInit, { once: true });
} else {
  autoInit();
}
