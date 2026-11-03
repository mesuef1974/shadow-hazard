(() => {
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const preferenceKeys = ['shadow-hazard-theme', 'theme'];
  const modes = new Set(['light', 'dark', 'auto']);

  const read = (key) => {
    try {
      const value = window.localStorage.getItem(key);
      return modes.has(value) ? value : null;
    } catch {
      return null;
    }
  };

  const write = (mode) => {
    try {
      for (const key of preferenceKeys) {
        if (window.localStorage.getItem(key) !== mode) {
          window.localStorage.setItem(key, mode);
        }
      }
    } catch {
      // The current page still changes theme when storage is unavailable.
    }
  };

  const currentMode = () => modes.has(root.dataset.theme)
    ? root.dataset.theme
    : (media.matches ? 'dark' : 'light');
  const isDark = () => currentMode() === 'dark'
    || (currentMode() === 'auto' && media.matches);

  const refreshControls = () => {
    const dark = isDark();
    const nextLabel = dark ? 'الوضع النهاري' : 'الوضع الليلي';
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.content = dark ? '#101d2b' : '#F4F7FB';
    for (const button of document.querySelectorAll('button.theme-toggle')) {
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', `التبديل إلى ${nextLabel}`);
      if (button.id === 'theme-toggle') {
        button.dataset.currentTheme = dark ? 'dark' : 'light';
        const label = button.querySelector('[data-theme-label]');
        if (label) label.textContent = nextLabel;
      } else {
        let label = button.querySelector('.shared-theme-label');
        if (!label) {
          label = document.createElement('span');
          label.className = 'shared-theme-label';
          button.append(label);
        }
        label.textContent = nextLabel;
      }
    }
  };

  const setMode = (mode, persist = true) => {
    if (!modes.has(mode)) return;
    root.dataset.theme = mode;
    if (persist) write(mode);
    refreshControls();
  };

  const initialMode = read(preferenceKeys[0]) || read(preferenceKeys[1]) || 'auto';
  setMode(initialMode);

  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    const button = target?.closest('button.theme-toggle');
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setMode(isDark() ? 'light' : 'dark');
  }, true);

  window.addEventListener('storage', (event) => {
    if (!preferenceKeys.includes(event.key)) return;
    const mode = modes.has(event.newValue) ? event.newValue : 'auto';
    setMode(mode, false);
    write(mode);
  });

  media.addEventListener?.('change', () => {
    if (currentMode() === 'auto') refreshControls();
  });

  new MutationObserver(() => {
    const mode = root.dataset.theme;
    if (modes.has(mode)) write(mode);
  }).observe(root, {attributes: true, attributeFilter: ['data-theme']});

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refreshControls, {once: true});
  } else {
    refreshControls();
  }
})();
