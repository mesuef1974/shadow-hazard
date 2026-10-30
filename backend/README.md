# نواة سجل الخدمات والهوية

النطاق المنفذ: جرد المهارات واستخدامها، مصادر الخدمات والأصول، مقترحات الهوية وألوانها وخطوطها، سجل جلسة ومهمة، وسجل مشكلات. لا يشمل إدارة المشروع الكاملة أو النشر أو تنشيط الحسابات.

## تشغيل محلي من جذر المشروع
```powershell
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py import_project_registry
.\.venv\Scripts\python.exe backend/manage.py record_service_usage docs/registry/runtime-events.json
.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py test registry
```

الاعتماديات مثبتة في البيئة الافتراضية للمشروع ومقفلة في requirements.txt. لإعادة إنشائها تستخدم Python 3.12 وpip install -r backend/requirements.txt داخل بيئة معزولة.

القاعدة المحلية SQLite محفوظة في .local ومُستثناة من Git. إعداد DATABASE_URL يحول Django إلى PostgreSQL. DJANGO_ENV=production يوجب SECRET_KEY وقاعدة PostgreSQL ويشغل إعدادات ملفات تعريف الارتباط الآمنة. النشر ليس جاهزًا بمجرد هذه الإعدادات: يلزم تجهيز static وخادم الإنتاج والمراقبة والنسخ الاحتياطية وفحص PostgreSQL وخطة الاستعادة.

لم ننشئ أي مستخدم مفعل؛ ستحدد آلية تنشيط الحسابات واستعادة الوصول مع المالك لاحقًا. صفحة الإدارة إن شُغلت لا تسمح بتعديل سجل الاستخدام. لا يوجد endpoint مفتوح للاستيراد.

لتصفح السجل محليًا استخدم Django shell أو الإدارة بعد إعداد حساب المشرف بأمان. المصدر الخاص للبيانات هو القاعدة المحلية، وملفات JSON لقطة استيراد قابلة لإعادة البناء؛ لا تحرر التاريخ بتعديلها بعد اعتماد الإصدارات.
