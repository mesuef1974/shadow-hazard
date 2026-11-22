# مهمة 0011: ترحيل إعداد Railway من `railway.json` إلى `.railway/railway.ts`

- الهدف: تبقى النسخة السحابية تُبنى وتُفحص كما هي بعد 2026-12-01، يوم تتوقف Railway عن قراءة `railway.json` (طلب المالك 2026-11-22؛ [ADR 0010](../decisions/0010-cloud-copy-on-railway.md)).
- الملفات: `.railway/railway.ts` و`.railway/package.json` وقفله، وحذف `railway.json`، وسطر في `.gitignore` و`.dockerignore`، وADR 0010 و`backend/README.md` و`CHANGELOG.md`.
- القبول: `railway config plan` يقول إن الإعداد مطابق بعد التطبيق؛ والنشرة المبنية من `main` بلا `railway.json` تنجح ويردّ `/healthz/` 200؛ والنسخة المحلية تُنشر كما كانت.
- خارج النطاق: إدارة المتغيرات وقاعدة PostgreSQL من الملف، وتطبيق الإعداد من GitHub Actions (يحتاج رمز مشروع يضعه المالك).
- المرجع: وثائق Railway «Infrastructure as Code»، قسم «Migrating from Config as Code».
- الإنهاء: النسختان منشورتان و`railway config plan` نظيف؛ ثم سطر في ملف التسليم.
