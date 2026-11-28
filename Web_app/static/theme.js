/* Load before CSS to restore appearance without a flash of the opposite theme. */
'use strict';
(() => {
  const key = 'sud.theme';
  const valid = value => value === 'light' || value === 'dark';
  let saved;
  try { saved = localStorage.getItem(key); } catch { /* Storage can be disabled. */ }
  const apply = (name, persist = true) => {
    name = valid(name) ? name : 'dark';
    document.documentElement.dataset.theme = name;
    if (persist) {
      try { localStorage.setItem(key, name); } catch { /* Keep switching available in this tab. */ }
    }
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.textContent = name === 'dark' ? '☀ Светлая тема' : '☾ Тёмная тема';
      button.setAttribute('aria-pressed', String(name === 'dark'));
      button.title = 'Переключить на ' + (name === 'dark' ? 'светлую' : 'тёмную') + ' тему';
      button.setAttribute('aria-label', button.title);
    });
    window.dispatchEvent(new CustomEvent('sud-themechange', {detail: name}));
  };
  window.SUDTheme = {
    get name() { return document.documentElement.dataset.theme; },
    set: apply,
    toggle() { apply(this.name === 'dark' ? 'light' : 'dark'); }
  };
  apply(saved, false);
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.addEventListener('click', () => window.SUDTheme.toggle());
    });
    apply(window.SUDTheme.name, false);
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(event.newValue, false);
  });
})();
