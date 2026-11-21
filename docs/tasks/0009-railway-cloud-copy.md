# مهمة 0009: نسخة سحابية للفريق على Railway

- الهدف: يدخل الطالبان المنصة من أي مكان بلا تثبيت، من نسخة ثانية على حساب المالك في Railway (طلب المالك 2026-11-21؛ [ADR 0010](../decisions/0010-cloud-copy-on-railway.md)).
- الملفات: `Dockerfile` و`.dockerignore` و`railway.json` و`backend/requirements-cloud.txt`، و`backend/config/settings.py` و`urls.py`، و`backend/accounts/` (`throttle.py` و`backends.py` و`forms.py` والنموذج `LoginThrottle` وهجرته واختباراته).
- القبول: `manage.py test accounts` ينجح؛ الصورة تُبنى وتعمل بوضع الإنتاج مع PostgreSQL، فيردّ `/healthz/` 200 بلا دخول وتُفتح صفحات الطالب بعد الدخول، ويُقفل الدخول بعد 5 محاولات فاشلة؛ النسخة المحلية تُنشر وتعمل كما كانت.
- خارج النطاق: نقل بيانات قاعدة المضيف، وتشغيل العامل ولوحات المشرف المرتبطة بالجهاز في السحابة، ونطاق خاص.
- المرجع: ADR 0010، ووثائق Railway (فحص الصحة من `healthcheck.railway.app`، وترويسات الوسيط).
- الإنهاء: الرابط السحابي يردّ 200 على `/healthz/` والمالك دخل بحسابه؛ ثم سطر في ملف التسليم.
