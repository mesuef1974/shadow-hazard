(() => {
  const root = document.documentElement;
  root.classList.add('nav-js');

  const wire = () => {
    const button = document.querySelector('button.nav-toggle');
    const nav = document.getElementById('site-nav');
    if (!button || !nav) return;

    const set = (open) => {
      nav.classList.toggle('nav-open', open);
      button.setAttribute('aria-expanded', String(open));
    };

    button.addEventListener('click', () => {
      set(button.getAttribute('aria-expanded') !== 'true');
    });

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || button.getAttribute('aria-expanded') !== 'true') return;
      set(false);
      button.focus();
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire, {once: true});
  } else {
    wire();
  }
})();
