/* القائمة الجانبية: يتذكر المجموعات التي فتحها المستخدم بين الصفحات، ويجعل «/» ينقل إلى البحث. */
(() => {
  const KEY = 'sidebar-open-groups';
  const read = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; }
  };
  const write = (value) => {
    try { localStorage.setItem(KEY, JSON.stringify(value)); } catch (e) { /* التخزين محجوب: يبقى السلوك الافتراضي */ }
  };

  const wire = () => {
    const groups = [...document.querySelectorAll('#site-nav details.nav-group')];
    const saved = new Set(read());
    groups.forEach((group) => {
      const name = group.querySelector('summary').textContent.trim();
      if (saved.has(name)) group.open = true;
      group.addEventListener('toggle', () => {
        const open = new Set(read());
        if (group.open) open.add(name); else open.delete(name);
        write([...open]);
      });
    });

    document.addEventListener('keydown', (event) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
      const tag = (event.target.tagName || '').toLowerCase();
      if (['input', 'textarea', 'select'].includes(tag) || event.target.isContentEditable) return;
      event.preventDefault();
      window.location.assign('/knowledge/');
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire, {once: true});
  } else {
    wire();
  }
})();
