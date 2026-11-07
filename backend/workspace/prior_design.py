"""وثائق التصميم السابق (جانب الطريق) قبل ADR 0007؛ تبقى كما هي وتُعلَّم أرشيفًا."""

PRIOR_DOCUMENTS = (
    ('blueprint/prototype-first-experiment-ar.md', 'متطلبات النموذج وتجربة إثبات الفكرة'),
    ('blueprint/prototype-first-experiment-ar.html', 'من الفكرة إلى تجربة قابلة للقياس'),
    ('blueprint/review-ar.html', 'حزمة المراجعة: من الفكرة إلى نموذج يعمل'),
    ('roadside-warning-decision-ar.md', 'قرار التحذير من جانب الطريق'),
)
# تبقى ظاهرة للمشرف وفي الأرشيف، وتُخفى عن الطالبين: حلّ محلها ADR 0007 (قرار المالك 2026-11-07).
HIDDEN_FROM_STUDENTS = (
    'roadside-warning-decision-ar.md',
    'blueprint/prototype-first-experiment-ar.md',
)
PRIOR_RECEIPTS = (
    (704, 'بروتوكول تقييم المحاكاة v3'),
    (716, 'نتائج الدفعة الأساسية'),
    (726, 'إصلاح الرصد: قبل وبعد'),
    (731, 'قيود التقييم والمرحلة التالية'),
    (737, 'نتائج بروتوكول v4'),
    (738, 'الخطة العلمية'),
)
PRIOR_NAMES = frozenset(name for name, _ in PRIOR_DOCUMENTS) | frozenset(f'__receipts__/{pk}.md' for pk, _ in PRIOR_RECEIPTS)
