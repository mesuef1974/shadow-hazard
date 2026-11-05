# مهمة 0002: الحرّاس والخطّافات (حوكمة بالكود)

- الهدف: تحويل قرارات التجميد وقواعد التنسيق إلى حرّاس تعمل بلا توكنز.
- المخرجات:
  1. خطّاف pre-commit (`scripts/guard_frozen.py` + تسجيله) يرفض تغييرات تحت `backend/` (عدا `backend/simulation/`)، و`docs/brand/`، و`docs/blueprint/` (عدا `prototype-first-experiment-ar.md`)، ويطبع اسم القرار (ADR 0002 البند 3). يُتجاوز فقط بمتغير بيئة صريح مع سبب.
  2. `scripts/prepush_check.py`: اختبارات `backend/simulation/` المركّزة؛ فحص أن `.local/` غير مُتعقَّب؛ خروج غير صفري عند أي فشل.
  3. خطّاف SessionStart لـClaude Code (`.claude/settings.json`) يطبع فقرة «حالة الكود» من `docs/handoff-2026-11-04.md` وقائمة `docs/tasks/` المفتوحة فقط.
  4. CI على GitHub Actions يشغّل `scripts/prepush_check.py` عند كل Pull Request.
- القبول: محاولة تعديل ملف تحت `docs/brand/` تُرفض محليًا برسالة واضحة؛ `prepush_check.py` يمر على الحالة الراهنة؛ SessionStart لا يطبع أكثر من 15 سطرًا.
- خارج النطاق: أي تغيير في `src/` أو منطق المحاكاة؛ أي أداة محاسبة توكنز في قاعدة البيانات (القسم الوكيلي مجمّد).
- المرجع: `docs/coordination-protocol.md`، ADR 0002 فقرة «ما جُمِّد»، وقالب `shschool_mvp` (`guard_git`, `prepush_check.py`) يُنسخ منه ما يلزم فقط.
- الإنهاء: ثلاثة أسطر تحت «حالة الكود» في ملف التسليم.
- الحالة: منجزة في 2026-11-04 (Claude Code): `scripts/` و`.githooks/` و`.claude/settings.json` ووظيفة «Pre-push check» في `ci.yml`. التفعيل مرة لكل نسخة: `python scripts/install_hooks.py`.
