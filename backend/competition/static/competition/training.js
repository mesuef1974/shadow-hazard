// صفحة اللقاء: رابط توقيت (#segment-N) يفتح لوحة التفريغ المطوية ثم يمرّر إلى المقطع.
(() => {
  const reveal = () => {
    if (!location.hash.startsWith('#segment-')) return;
    const target = document.getElementById(location.hash.slice(1));
    const panel = document.getElementById('transcript');
    if (!target || !panel) return;
    panel.open = true;
    target.scrollIntoView({ block: 'center' });
  };
  addEventListener('hashchange', reveal);
  reveal();
})();
