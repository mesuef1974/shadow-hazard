const search = document.getElementById('search');
const groups = [...document.querySelectorAll('#schema details')];
const status = document.getElementById('search-status');
const empty = document.getElementById('search-empty');
const total = groups.reduce((n, group) => n + group.querySelectorAll('tbody tr').length, 0);
let expansionBeforeSearch = null;
function filterTables() {
  const query = search.value.trim().toLocaleLowerCase('ar');
  if (query && !expansionBeforeSearch) expansionBeforeSearch = groups.map(g => g.open);
  let count = 0;
  groups.forEach((group, index) => {
    const matchGroup = group.querySelector('summary').dataset.title.toLocaleLowerCase('ar').includes(query);
    let visible = 0;
    group.querySelectorAll('tbody tr').forEach(row => {
      row.hidden = !(matchGroup || row.textContent.toLocaleLowerCase('ar').includes(query));
      if (!row.hidden) visible++;
    });
    count += visible;
    group.hidden = visible === 0;
    group.querySelector('.group-count').textContent = `${visible}`;
    if (query && visible) group.open = true;
    if (!query && expansionBeforeSearch) group.open = expansionBeforeSearch[index];
  });
  if (!query) expansionBeforeSearch = null;
  status.textContent = query ? `نتائج البحث: ${count} جدولًا من أصل ${total}.` : `قاموس البيانات: ${total} جدولًا في ${groups.length} مجموعات.`;
  empty.hidden = count > 0;
}
groups.forEach(group => {
  const summary = group.querySelector('summary');
  summary.dataset.title = summary.textContent;
  const count = document.createElement('span');
  count.className = 'group-count';
  count.setAttribute('aria-hidden', 'true');
  summary.append(count);
});
search.addEventListener('input', filterTables);
document.getElementById('clear-search').addEventListener('click', () => { search.value = ''; filterTables(); search.focus(); });
document.getElementById('expand-groups').addEventListener('click', () => groups.filter(g => !g.hidden).forEach(g => { g.open = true; }));
document.getElementById('collapse-groups').addEventListener('click', () => groups.filter(g => !g.hidden).forEach(g => { g.open = false; }));
filterTables();

// Print every group, then restore the user's search and disclosure state.
let printState;
window.addEventListener('beforeprint', () => {
  if (printState) return;
  printState = groups.map(group => ({group, open:group.open, hidden:group.hidden, rows:[...group.querySelectorAll('tr')].map(row => [row,row.hidden])}));
  groups.forEach(group => {group.hidden = false; group.open = true; group.querySelectorAll('tr').forEach(row => {row.hidden = false;});});
});
window.addEventListener('afterprint', () => {
  printState?.forEach(({group,open,hidden,rows}) => {group.open=open;group.hidden=hidden;rows.forEach(([row,state]) => {row.hidden=state;});});
  printState = null;
});
document.getElementById('print-report').addEventListener('click', () => window.print());
const nav = document.querySelector('nav');
const links = [...nav.querySelectorAll('a[href^="#"]')];
const sections = links.map(link => document.querySelector(link.getAttribute('href')));
new ResizeObserver(() => document.documentElement.style.setProperty('--nav-height', `${nav.offsetHeight}px`)).observe(nav);
let scheduled = false;
function updateNavigation() {
  const boundary = getComputedStyle(nav).position === 'sticky' ? nav.offsetHeight + 40 : 40;
  let current = sections[0];
  sections.forEach(section => {if (section.getBoundingClientRect().top <= boundary) current = section;});
  links.forEach(link => {if (link.hash === `#${current.id}`) link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');});
  scheduled = false;
}
window.addEventListener('scroll', () => {if (!scheduled) {scheduled = true;requestAnimationFrame(updateNavigation);}}, {passive:true});
updateNavigation();
