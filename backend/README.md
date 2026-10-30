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

## إعداد حساب المشرف mesuef
من جذر المشروع، ينشئ المالك الحساب ويكتب كلمة السر بنفسه:
```powershell
.\.venv\Scripts\python.exe backend/manage.py createsuperuser --username mesuef --email ""
```
للحساب الموجود لاحقًا: `python backend/manage.py changepassword mesuef` داخل البيئة الافتراضية.
لم ينشئ الوكيل الحساب أو كلمة السر. قيد الحسابات يسمح بـ mesuef للمشرف وsaud وmubarak للطالبين فقط.

لتشغيل واجهة الإدارة محليًا:
```powershell
.\.venv\Scripts\python.exe backend/manage.py runserver 127.0.0.1:8000 --insecure --noreload
```
افتح http://127.0.0.1:8000/admin/ . الخيار --insecure مخصص لخدمة ملفات CSS مع DEBUG=False على خادم التطوير المحلي فقط؛ لا يستخدم للإنتاج. سجل الاستخدام يبقى للعرض فقط.

جرد مكتبات البيئة الافتراضية: .\.venv\Scripts\python.exe backend/manage.py inventory_python_packages . يسجل الحزم المثبتة وإصداراتها واعتمادياتها، ويحتفظ بسجل الحزم المحذوفة مع علامة غير مثبتة. يشمل pip؛ لا يشمل وحدات مكتبة Python القياسية المدمجة. الجرد يدوي عند تشغيل الأمر.
## مراقبة مكتبات Python
خادم التطوير يشغل مراقبًا كل 30 ثانية تلقائيًا. يتوقف المراقب بتوقف الخادم؛ في الإنتاج شغل `python backend/manage.py watch_python_packages` كعملية عامل مستقلة واحدة. حذف Django نفسه أو تعطيل Python يمنع المراقبة حتى إصلاح البيئة.
سجل الأحداث يحفظ الإصدار السابق والجديد ووقت الرصد (ليس وقت العملية الدقيق). التغييرات الخارجية تسجل دون تخمين منفذ أو سبب. المكتبات الجديدة تحصل على وصف يفيد بأن الشرح يحتاج مراجعة؛ لا يولد وصف غير موثوق.
توثيق استعمال مكتبة مع الهوية والمهمة والسبب:
`python backend/manage.py inventory_python_packages --use django --actor codex-assistant --task arabic-admin-presentation --reason "بناء واجهة إدارة السجل"`
هذه هوية مصرّح بها في أمر محلي موثوق، وليست إثباتًا مستقلًا للشخص الذي نفذ تثبيت pip. الاستعمال يوثق منفصلًا عن التثبيت، ولا توجد مراقبة لكل import. يمكن تمرير actor/task/reason مع الجرد لتوثيق تغييرات لم يرصدها المراقب بعد. راجع هوية المنفذ الفعلية قبل استعمال المثال.
