// ناسخ المحتوى التدريبي من hamdanfsc.com/courses؛ يُشغَّل في المتصفح بعد دخول حساب الفريق.
// يمشي على الدورات ودروسها داخل إطارات مخفية من النطاق نفسه، ويجمع لكل درس فهرسه وشرائحه
// (نص الشريحة وروابط صورها ووسائطها)، ثم يرسل الناتج إلى tools/training_crawl_receiver.py.
// الاستعمال: شغّل المستقبِل، ثم نفّذ في الصفحة: await trainingCrawl('http://127.0.0.1:8733/')
window.trainingCrawl = async (receiver, onProgress = () => {}) => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const open = async (url, settle = 3000) => {
    const frame = document.createElement('iframe');
    frame.style.cssText = 'position:fixed;left:-9999px;width:1200px;height:900px';
    frame.src = url;
    document.body.appendChild(frame);
    await new Promise((resolve) => { frame.onload = resolve; setTimeout(resolve, 15000); });
    await sleep(settle);
    return frame;
  };
  const clean = (text) => text.replace(/\s+/g, ' ').trim();
  const lessonOf = async (href) => {
    const frame = await open(href);
    const doc = frame.contentDocument;
    const outline = [...doc.querySelectorAll('p.slideText')].map((p) => clean(p.innerText));
    const steps = [];
    let previous = '';
    for (let i = 0; i < 300; i += 1) {
      const slide = doc.querySelector('app-slide');
      if (!slide) break;
      const step = {
        text: slide.innerText.replace(/\n{2,}/g, '\n').trim(),
        img: [...slide.querySelectorAll('img')].map((e) => e.src),
        med: [...slide.querySelectorAll('video,iframe,source,a[href]')]
          .map((e) => e.src || e.href || '').filter(Boolean),
      };
      const key = JSON.stringify(step);
      if (key === previous) break; // الشريحة لم تتغير: نهاية الدرس
      previous = key;
      steps.push(step);
      const next = [...doc.querySelectorAll('button.next')]
        .find((b) => /التالي/.test(b.innerText) && !b.disabled);
      if (!next) break;
      next.click();
      await sleep(450);
    }
    frame.remove();
    return { outline, steps };
  };
  const courseLinks = [...document.querySelectorAll('a[href*="/dawrat/"]')]
    .map((a) => a.getAttribute('href'));
  const out = [];
  for (const url of [...new Set(courseLinks)]) {
    const frame = await open(url);
    const doc = frame.contentDocument;
    const page = doc.querySelector('.course_page') || doc.body;
    const head = page.innerText.split('الدروس')[0].replace(/\n+/g, ' | ');
    const lessons = [...doc.querySelectorAll('a.lesson')].map((a) => ({
      href: a.getAttribute('href'),
      label: clean(a.innerText.replace(/menu_book|schedule|arrow_forward/g, '')),
    }));
    frame.remove();
    const course = { url, head, lessons: [] };
    out.push(course);
    for (const lesson of lessons) {
      let body = { outline: [], steps: [] };
      try { body = await lessonOf(lesson.href); } catch (error) { body.error = String(error); }
      course.lessons.push({ ...lesson, ...body });
      onProgress(out);
    }
  }
  if (receiver) {
    await fetch(receiver, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(out),
    });
  }
  return out.length;
};
