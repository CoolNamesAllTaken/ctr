// Theme: dark by default (the train runs at night), light with the website's colors from the ☀ / ☾ button,
// remembered per browser. Runs in <head>, before the page draws, so there is no flash of the other theme.
(() => {
  const KEY = 'caltreat-theme';
  const COLOR = { dark: '#14171b', light: '#132a67' };
  let t = 'dark';
  try { t = localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'; } catch (e) { /* no storage: dark */ }
  const apply = () => {
    document.documentElement.dataset.theme = t;
    const m = document.querySelector('meta[name=theme-color]');
    if (m) m.content = COLOR[t];
  };
  apply();
  window.theme = {
    get: () => t,
    toggle() {
      t = t === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(KEY, t); } catch (e) { /* not remembered */ }
      apply();
    },
  };
  // The engineer page's header is plain HTML: add the button there (the settings page draws its own).
  const add = () => {
    const hd = document.querySelector('.desk > header .hd-right:last-child');
    if (!hd) return false;
    if (!document.querySelector('.themebtn')) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'iconbtn themebtn';
      b.title = 'Dark / light theme';
      b.setAttribute('aria-label', 'Dark / light theme');
      b.innerHTML = '<span class="th-d" aria-hidden="true">☀</span><span class="th-l" aria-hidden="true">☾</span>';
      b.addEventListener('click', () => window.theme.toggle());
      hd.insertBefore(b, hd.lastElementChild);
    }
    return true;
  };
  const obs = new MutationObserver(() => { if (add()) obs.disconnect(); });
  document.addEventListener('DOMContentLoaded', () => { if (!add()) obs.observe(document.body, { childList: true, subtree: true }); });
})();
