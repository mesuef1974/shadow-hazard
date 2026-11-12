// ناسخ المحتوى التدريبي من hamdanfsc.com/courses؛ يُشغَّل في المتصفح بعد دخول حساب الفريق.
// يمشي على الدورات ودروسها داخل إطارات مخفية من النطاق نفسه، ويجمع لكل درس فهرسه وشرائحه
// (نص الشريحة وروابط صورها ووسائطها). يحفظ كل درس في localStorage فور نسخه، فإعادة
// التشغيل تكمل من حيث توقف ولا يضيع الناتج بانتقال الصفحة.
// الاستعمال:
//   trainingCrawl()                      يبدأ أو يكمل؛ التقدم في trainingCrawlStatus()
//   trainingSend(<العنوان الذي يطبعه tools/training_crawl_receiver.py>)  يرسل الناتج إليه
// الإرسال نموذج POST ينتقل به التبويب نفسه: المتصفح يمنع fetch والإطارات من صفحة عامة إلى
// 127.0.0.1 ولا يمنع الانتقال. الناتج يبقى في localStorage حتى trainingCrawlClear().
(() => {
  const KEY = 'trainingCrawl.v1';
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const load = () => JSON.parse(localStorage.getItem(KEY) || '{"courses":[],"done":false}');
  const save = (state) => localStorage.setItem(KEY, JSON.stringify(state));
  const clean = (text) => text.replace(/\s+/g, ' ').trim();
  const until = async (test, limit = 15000) => {
    for (let waited = 0; waited < limit; waited += 100) {
      const value = test();
      if (value) return value;
      await sleep(100);
    }
    return null;
  };
  const open = async (url, ready) => {
    const frame = document.createElement('iframe');
    frame.style.cssText = 'position:fixed;left:-9999px;width:1200px;height:900px';
    frame.src = url;
    document.body.appendChild(frame);
    await until(() => frame.contentDocument && frame.contentDocument.querySelector(ready));
    await sleep(600);
    return frame;
  };
  const stepOf = (slide) => ({
    text: slide.innerText.replace(/\n{2,}/g, '\n').trim(),
    img: [...slide.querySelectorAll('img')].map((e) => e.src),
    med: [...slide.querySelectorAll('video,iframe,source,a[href]')]
      .map((e) => e.src || e.href || '').filter(Boolean),
  });
  // الشريحة تظهر فارغة لحظةً قبل تحميل محتواها؛ لا تُحسب إلا وفيها صورة أو وسيط أو نص.
  const filled = (slide) => slide
    && (slide.querySelector('img,video,iframe') || slide.innerText.trim());
  const lessonOf = async (href) => {
    // زر «التالي» آخر ما يظهر؛ انتظاره يضمن اكتمال تهيئة الدرس قبل أول نقرة.
    const frame = await open(href, 'app-slide');
    await until(() => frame.contentDocument.querySelector('button.next'), 6000);
    await until(() => filled(frame.contentDocument.querySelector('app-slide')), 6000);
    const doc = frame.contentDocument;
    const outline = [...doc.querySelectorAll('p.slideText')].map((p) => clean(p.innerText));
    const steps = [];
    for (let i = 0; i < 400; i += 1) {
      const slide = doc.querySelector('app-slide');
      if (!slide) break;
      const step = stepOf(slide);
      steps.push(step);
      const next = [...doc.querySelectorAll('button.next')]
        .find((b) => /التالي/.test(b.innerText) && !b.disabled);
      if (!next) break;
      const before = JSON.stringify(step);
      // ينتظر تبدّل الشريحة فعلًا. النقرة قبل اكتمال تهيئة الصفحة تُهمَل، فتُعاد حتى ثلاث
      // مرات؛ إن لم تتبدّل الشريحة بعدها فهي نهاية الدرس.
      let changed = false;
      for (let attempt = 0; attempt < 3 && !changed; attempt += 1) {
        const button = [...doc.querySelectorAll('button.next')]
          .find((b) => /التالي/.test(b.innerText) && !b.disabled);
        if (!button) break;
        button.click();
        changed = await until(() => {
          const now = doc.querySelector('app-slide');
          return filled(now) && JSON.stringify(stepOf(now)) !== before;
        }, 2000);
      }
      if (!changed) break;
    }
    frame.remove();
    return { outline, steps };
  };
  window.trainingCrawl = async (parallel = 5) => {
    const state = load();
    if (!state.courses.length) {
      const links = [...document.querySelectorAll('a[href*="/dawrat/"]')]
        .map((a) => a.getAttribute('href'));
      for (const url of [...new Set(links)]) {
        const frame = await open(url, 'a.lesson');
        const doc = frame.contentDocument;
        const page = doc.querySelector('.course_page') || doc.body;
        state.courses.push({
          url,
          head: page.innerText.split('الدروس')[0].replace(/\n+/g, ' | '),
          lessons: [...doc.querySelectorAll('a.lesson')].map((a) => ({
            href: a.getAttribute('href'),
            label: clean(a.innerText.replace(/menu_book|schedule|arrow_forward/g, '')),
          })),
        });
        frame.remove();
      }
      save(state);
    }
    // عدة دروس معًا (كلٌّ في إطاره) تختصر زمن النسخ؛ الحفظ بعد كل درس.
    const pending = state.courses.flatMap((course) => course.lessons)
      .filter((lesson) => !(lesson.steps && lesson.steps.length));
    const worker = async () => {
      for (let lesson = pending.shift(); lesson; lesson = pending.shift()) {
        try {
          Object.assign(lesson, await lessonOf(lesson.href));
          delete lesson.error;
        } catch (error) {
          Object.assign(lesson, { outline: [], steps: [], error: String(error) });
        }
        save(state);
      }
    };
    await Promise.all(Array.from({ length: parallel }, worker));
    state.done = true;
    save(state);
    return window.trainingCrawlStatus();
  };
  window.trainingCrawlStatus = () => {
    const lessons = load().courses.flatMap((course) => course.lessons);
    return {
      done: load().done,
      courses: load().courses.length,
      lessons: lessons.length,
      copied: lessons.filter((lesson) => lesson.steps && lesson.steps.length).length,
      slides: lessons.reduce((sum, lesson) => sum + (lesson.steps || []).length, 0),
      errors: lessons.filter((lesson) => lesson.error).length,
    };
  };
  window.trainingSend = (receiver) => {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = receiver;
    const field = document.createElement('input');
    field.type = 'hidden';
    field.name = 'payload';
    field.value = JSON.stringify(load().courses);
    form.appendChild(field);
    document.body.appendChild(form);
    form.submit();
  };
  window.trainingCrawlClear = () => localStorage.removeItem(KEY);
})();
