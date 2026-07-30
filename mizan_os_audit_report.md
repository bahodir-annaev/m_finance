# Mizan OS — Ilova Audit Hisoboti

**Sana:** 2026-04-29
**Audit miqyosi:** MIZAN Finance v4.0 (lokal Flask + SQLite, ~5562 LOC)
**Skill-lar:** /retro, /investigate (×2), /qa, /browse, /investigate, /review, /qa-only, /cso, /codex (skipped — CLI yo'q)
**Auditor:** Claude (Opus 4.7) + gstack v1.20.0.0

---

## 1. Xulosa (qisqacha)

MIZAN Finance v4.0 — Tashkent ofisining ichki moliyaviy boshqaruv ilovasi. Single-user, lokal Flask app, 16 sahifa, 23 xodim ma'lumoti seeded. Asosiy auditorni RIBA-aligned arxitektura va to'g'ri valyuta hisoblari emas (CFO-Level Audit allaqachon v4.0 da bajarilgan), balki **ish jarayonlari, xavfsizlik, kod sifati va UX** qiziqtiradi.

**Eng jiddiy 3 ta muammo:**

1. **🔴 KRITIK / Stored XSS** — `<script>alert(1)</script>` payload `counterparty` maydoniga yoziladi va `/loans` sahifasida xom render qilinadi → script ishga tushadi. Hozir lokal-only bo'lgani uchun risk past, lekin agar app bir kun cloud yoki tarmoqqa qo'yilsa — darhol kritik. Affected: `/loans`, `/staff`, `/accounting` ko'p maydon.

2. **🟠 HIGH / `accounting_page` 645 qator** — Bitta funksiya 7 ta turli sektsiya (Pul aylanmasi, Proekt, Xodim, Texnika, Litsenziya, Overhead, Valyuta, Qarz) ni boshqaradi va bitta katta `if/elif` blokida hammasi. SRP buzilgan, test qilish qiyin, exception path-da connection leak xavfi bor.

3. **🟠 HIGH / Excel button-lar noto'g'ri sahifani ochadi (FIXED)** — `/staff` sahifasidagi `Excel` tugmasi butun multi-sheet workbook qaytarardi va Excel default holatda Dashboard tab-ni ochib turardi → foydalanuvchi noto'g'ri ma'lumot ko'radi. **Tuzatildi:** `?sheet=<name>` query param qo'shildi, har bir sahifaning Excel tugmasi mos sheet-ni `wb.active` deb belgilab boradi.

**Foydalanuvchi yo'naltirilgan birinchi yo'l:** Stored XSS-ni tuzatish — bir kunlik ish, lekin ekspozit risk eng yuqori.

---

## 2. Funktsional xatolar

| Xato turi | Qayerda | Nima bo'ladi | Kerakli tuzatish | Holat |
|---|---|---|---|---|
| Logic / FX | `database.py:1361` `get_loan_summary()` | USD va UZS qarzlar xom raqam sifatida jamlanardi (10K USD + 100M UZS = 100M+10K nonsense) | `get_rate_for_date(issue_date)` orqali UZS-ga konvertirlash | ✅ FIXED |
| Logic / config | `SETUP.bat:2,12,26` | Unix `>/dev/null` Windows da fayl yaratadi, output ekranga toshadi | `>nul` ga almashtirish | ✅ FIXED |
| Template | `app.py:2014` (eski) | f-string ifodadan keyin ortiqcha `""`, `class="stat ""` chiqar edi | `}""><div` → `}"><div` | ✅ FIXED |
| Dead code | `app.py:565, 1971` | `usd_rate = get_setting(...)` olinardi, lekin ishlatilmasdi | Olib tashlash | ✅ FIXED |
| i18n gap | `app.py:1995-2032` | `/loans` sahifasi to'liq Uzbek hardcoded — EN/RU foydalanuvchilar Uzbek matn ko'rardi | 22 ta `loans_*` kalit translations.py ga, app.py `t()` orqali | ✅ FIXED |
| UX / Excel | `app.py` 8 ta tugma | `/staff`, `/hourly`, `/budget`, `/kpi` Excel tugmalari Dashboard tab-da ochilar edi | `?sheet=<name>` query param + endpoint mos sheet-ni `wb.active` qiladi | ✅ FIXED |
| UX / sub-bug | `app.py:1078` (eski) | To'lov xabari har doim "UZS" deyardi qarz USD bo'lsa ham | `loans.currency` dan oladi | ✅ FIXED |
| i18n / Dashboard | `app.py` `dashboard()` | `QARZ QOLDIG'I` kart va formula chain block EN/RU da Uzbek qoladi | Translation kalitlari qo'shish | ⚠️ deferred |
| UX / discoverability | `/projects` sahifa | "Yangi loyiha" tugmasi yo'q, foydalanuvchi `/accounting → Proekt tab` ga borishi kerakligi noma'lum | Empty state matnida yo'l ko'rsatish | ⚠️ deferred |
| UX / cosmetic | `/settings` | Bo'sh `RISK KOEFFITSIENTI ()` — qavslar bo'sh | Birlik yo'q joyda qavslarni olib tashlash | ⚠️ deferred |
| UX / sortable | `/external`, `/internal`, `/projects` | Ustunlarda `▾ ▾` ikkita ko'rsatkich, chalg'itadi | Bitta neutral chevron yoki active sort holatga qarab `▲`/`▼` | ⚠️ deferred |
| UX / mobile | Barcha sahifalar | 375px viewport da sidebar 240px (64% ekran), responsive layout yo'q | Hamburger menu + media query | ⚠️ deferred |

**Jami:** 7 ta tuzatildi, 5 ta deferred.

---

## 3. Ishlash va yuklanish kamchiliklari

### 3.1 Page load TTFB (boshqaruv)

| Sahifa | TTFB | Rate |
|---|---|---|
| `/` (Dashboard) | 291 ms | ⚠️ — `get_dashboard_data()` 133 qator hisoblash |
| `/hourly` | 241 ms | ⚠️ — `calculate_hourly_rate()` x 23 staff (N+1 pattern) |
| `/kpi` | 253 ms | ⚠️ — `get_staff_kpi()` x 23 staff (N+1 pattern) |
| `/equipment` | 10 ms | ✅ (enggil select query-lar) |
| `/loans` | 9 ms | ✅ |
| Boshqa 11 ta | 8–14 ms | ✅ |

**Topilma:** 3 ta sahifa N+1 query pattern ishlatadi. 23 staff bilan 240-290 ms — qabul qilinadi. 100+ staff bilan 1+ sekund kutilishi mumkin. **Tuzatish:** batch SQL query bilan `JOIN` orqali bitta o'tishda hammani hisoblash.

### 3.2 Excel export

| Holat | Vaqt | Hajm |
|---|---|---|
| Bo'sh DB (96 qator) | 0.8 s | 13.7 KB |
| 50 loyiha + 250 soat (146 qator) | 4.4 s | 18.8 KB |

**Topilma:** Export O(N) loyihaga proportsional. 100 loyiha → ~10 s, 500 → ~50 s. Brauzer timeout bo'lishi mumkin. **Tuzatish:** background worker (Celery) yoki streaming response.

### 3.3 Static asset

- CSS, JS — barchasi inline `<style>` va `<script>` bloklar (HTML konstanta ichida). Cache yo'q.
- `mizan.ico` — 40 KB favicon, lekin `/favicon.ico` route 404 qaytaradi. Tarmoqli har sahifaga so'rov.
- Hech bir CDN yoki minify ishlatilmagan — kerak emas (lokal app).

---

## 4. Xavfsizlik kamchiliklari (CSO bo'yicha)

**Threat model: lokal-single-user (127.0.0.1).** Tarmoqdan chetdan attacker-lar app-ga to'g'ridan-to'g'ri yetib bormaydi. Lekin agar:
- Tarmoqqa qo'yilsa (host=0.0.0.0)
- Multi-user qilinsa (LAN deploy)
- Cloud/SaaS ga ko'chirilsa

— hamma quyidagi muammolar darhol **kritik** bo'ladi.

| Zaiflik | STRIDE | OWASP | Ekspluatatsiya | Qanday tuzatish | Risk (lokal) |
|---|---|---|---|---|---|
| **Stored XSS** | Tampering | A03 | `<script>alert(1)</script>` payload `counterparty` ga yoziladi → `/loans` da render bo'lganda script ishga tushadi. Confirmed via QA test. | `markupsafe.escape()` har bir foydalanuvchi-input HTML interpolyatsiyasida; yoki Jinja `{{ }}` ishlatib `render_template_string` orqali jiringa olish | 🟡 (sahifa lokal, lekin malware DOM-ga kiraolsa) |
| **No authentication** | Spoofing | A01 | localhost ga ulanish imkoni bo'lgan har kim to'liq CRUD (ma'lumotlarni o'qish/yozish/o'chirish) | Flask-Login + bcrypt password hashing | 🟢 lokal-only |
| **No CSRF protection** | Tampering | A01 | Foydalanuvchi browser-da boshqa zararli sayt ochsa, POST `/accounting`/`/loans` formalarni ishlata oladi | Flask-WTF CSRFProtect | 🟢 lokal-only |
| **Cookie no `secure`/`httponly`** | Information Disclosure | A05 | `set_cookie('mizan_lang', ...)` flag-larsiz | `secure=True, httponly=True, samesite='Lax'` | 🟢 (lang cookie sezgir emas) |
| **No `MAX_CONTENT_LENGTH`** | DoS | A04 | Excel import endpoint katta fayl uploadi RAM ni tugatishi mumkin | `app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024` | 🟡 |
| **xlsx zip-bomb risk** | DoS | A04 | openpyxl noma'lum xlsx ni load qiladi, billion-laughs attack | `read_only=True`, file-type whitelist (haqiqiy MIME) | 🟡 |
| **No audit log** | Repudiation | A09 | Kim qachon nima o'zgartirgani ma'lum emas; o'chirilgan loyihalar qaytarib bo'lmaydi | `logging` + audit table (changes log) | 🟡 |
| **No rate limiting** | DoS | A04 | `/api/update/<table>/<id>` ga 1000 rps mumkin | Flask-Limiter | 🟢 lokal-only |
| **API endpoints no auth** | Spoofing | A01 | `/api/update/<table>/<id>`, `/api/delete/<table>/<id>` har kimga ochiq | Authentication kerak | 🟢 lokal-only |
| **No HTTPS** | Information Disclosure | A02 | localhost da kerak emas, lekin `host=0.0.0.0` da kritik | `flask run --cert ...` yoki nginx reverse proxy | 🟢 lokal-only |
| **DEBUG mode** | Information Disclosure | A05 | Hozir `debug=False` ✅ | Saqlash | ✅ TOZA |
| **Hardcoded credentials** | Information Disclosure | A07 | Topilmadi (parol/API key yo'q) | — | ✅ TOZA |
| **SQL injection** | Tampering | A03 | f-string SQL bor, lekin `allowed` whitelist + `cols` filter to'siq bo'ladi. Saqlanadi, lekin code smell | Comment qo'shish "intentional, validated" | 🟢 (haqiqiy emas, code smell) |
| **Path traversal (file upload)** | Tampering | A03 | `secure_filename()` ishlatilgan, path components strip qilinadi | — | ✅ TOZA |

### Eng birinchi tuzatish kerak

1. **Stored XSS** — barcha user-input HTML escape qiling. Bir kunlik ish, eng kritik.
2. **MAX_CONTENT_LENGTH** — bir qator config, DoS yo'lini yopadi.
3. **Audit log** — Excel-import xato bo'lsa nima yo'qotilgan/yo'qolgani ko'rinmaydi.

### STRIDE jamlash

- **Spoofing:** Yuqori risk faqat agar app tarmoqda. Lokal-da N/A.
- **Tampering:** Stored XSS HOZIR ham real (foydalanuvchi o'zining browser-iga payload kiritsa, undan keyin ko'rganda payload ishlaydi).
- **Repudiation:** Audit log yo'q — kim nima o'zgartirgani noaniq.
- **Info Disclosure:** Localhost-da past. Tarmoqda yuqori.
- **DoS:** File upload + Excel export — orta risk.
- **Elevation:** Privilege levellari yo'q, applicable emas.

---

## 5. Kod sifati va texnik qarz

### 5.1 Strukturaviy qarz

| Indikator | Qiymat | Hisob |
|---|---|---|
| `app.py` jami qator | 2511 | Single-file monolit |
| `database.py` jami qator | 1649 | Schema + migration + calc + CRUD aralash |
| `accounting_page()` qator | **645** | 🔴 7 ta sub-handler bitta funksiyada |
| `init_db()` qator | **282** | 🟠 schema + migration + seed aralash |
| `get_dashboard_data()` qator | 133 | 🟡 — 8 ta XATO# fix-ni o'z ichiga oladi |
| `pricing_page()` qator | 160 | 🟡 |
| Bare `except:` | 0 | ✅ FIXED |
| Ishlatilmagan importlar | 0 | ✅ FIXED |
| Connection leak (post-fix) | 0 | ✅ open/close balance: 17/17 |
| Try/except `/accounting` POST 200-qatorli blok | YO'Q | 🟠 exception path-da conn leak xavfi |
| Logging chaqiriqlari | 0 | 🟠 silent failures, debug qiyin |
| Test fayllari | 3 | `test_loan_summary.py`, `test_loans_i18n.py`, `test_excel_per_page.py` (yangi) |
| Test count | 14 | (5+5+4) — paydo bo'lgan hammasi shu sessiyada |

### 5.2 SQL safety

- 100% parametrizatsiyalangan `?` placeholder
- f-string SQL faqat 8 joyda, hammasi `allowed` table whitelist bilan himoyalangan
- Code smell: f-string SQL — kelajakda noxush refactoring xavfini olib keladi. Comment qo'shish kerak: "intentional, validated table name from whitelist"

### 5.3 Architectural smells

1. **Monolitik `app.py`**: 2511 qator, 25 route, HTML template-lar kod ichida (HTML konstanta ~5 KB). Refactor: route-ni Flask Blueprint-larga ajratish, template-larni alohida `.html` fayllarga ko'chirish.

2. **`render_template_string` paradigmasi**: HTML xavfli (XSS yo'l), debug qiyin, IDE syntax highlight yo'q. Tavsiya: `templates/` papkasi + `render_template()`.

3. **N+1 query pattern**: `/hourly`, `/kpi` har xodim uchun alohida hisoblash. JOIN-larsiz batch query yoki cache.

4. **Test yo'q baseline-da**: shu sessiyadan oldin 0 test fayl bor edi. Endi 3 ta, 14 test. 100% coverage uzoq, lekin asos qo'yildi.

5. **Versiya yo'q**: `git init` qilinmagan. Har tuzatish manual revert. CHANGELOG yo'q.

### 5.4 Style/lint

`pyflakes` qolgan ogohlantirishlar (function-local unused vars, hozirgi sessiyada tegmagan):
- `app.py:2336-2339`: `cream_fill`, `data_font`, `num_fmt` — Excel export funksiyasidagi WIP local variable-lar
- `database.py:948`: `util_rate` — KPI funksiyasida hisoblanmagan
- `database.py:1014`: `net_profit` — dashboard funksiyasida hisoblanmagan
- `import_nizam.py:344`: f-string placeholdersiz

Hammasi kosmetik. Function logic-iga ta'sir qilmaydi.

---

## 6. UX/UI kamchiliklari

### 6.1 Til (i18n)

- **Tuzatildi:** `/loans` sahifasi to'liq UZ→EN→RU
- **Qoldi:** Dashboard `QARZ QOLDIG'I` kart, formula chain code block, sidebar "Oldi-Berdi" link, multipla success/error xabarlari (msg) UZ hardcoded
- Translation kalitlari mavjud-yo'qligini tekshiruvchi test yo'q (uz/en/ru parity)

### 6.2 Discoverability

- `/projects` da empty state — "Loyihani qaerdan qo'shaman?" javobi yo'q
- Sortable column-lar `▾ ▾` chalg'itadi (active sort yo'q)
- `/accounting` 7 tab — qaysi default ekanligi ko'rinmaydi (Pul aylanmasi default)

### 6.3 Mobile responsive

- Sidebar 240px fixed → 375px ekranda 64% joy oladi
- Hamburger menu yo'q, viewport meta-tag yo'q
- Mobile screenshot test: `audit/mobile-dashboard.png` desktop layout-i ko'rsatadi (responsive breakpoint hech qachon trigger bo'lmaydi)

### 6.4 Print

- Har sahifada `Print` tugma bor
- `@media print` CSS qoidalari mavjudmi? Tekshirilmadi — alohida sinov kerak

### 6.5 Form UX

- Empty form submit: silent qabul qiladi, hech qanday xabar bermaydi (haqiqatda `if total_amount > 0:` yashirin filter)
- Negative amount: silent rejection (ko'rsatilmaydi)
- XSS payload: silent qabul (alert chiqaradi)
- Birlashma: foydalanuvchi qaysi maydon noto'g'ri ekanini bilmaydi

### 6.6 Kosmetik

- `/settings` da `RISK KOEFFITSIENTI ()` bo'sh qavslar
- Sortable headers `▾ ▾` ikkilamchi
- Color contrast cheklanmagan (auto-tekshiruv qilinmadi, lekin ko'k → kulrang sub-text-lar ba'zi joyda past kontrast)

---

## 7. Tavsiyalar (prioritet bo'yicha)

### P0 — darhol (1 kun ichida)

1. **Stored XSS-ni tuzatish** — barcha user-input HTML interpolyatsiyasini `markupsafe.escape()` bilan o'rab chiqish. Affected: `/loans`, `/staff`, `/accounting`, `/external`, `/internal`. ~50 ta interpolyatsiya joyi.

2. **`MAX_CONTENT_LENGTH` o'rnatish** — `app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024`. Bir qator, DoS yo'lini yopadi.

3. **Git init + birinchi commit** — barcha o'zgarishlarni saqlash. Audit izi yaratish. ChatGPT bilan bo'lgan har sessiya commit-lariga aylanadi.

### P1 — keyingi sprint (1-2 hafta)

4. **`accounting_page()` ni 7 ta sub-handler-ga ajratish** — 645 qatordan ~80 qatorlik 7 ta funksiyaga. Test qilish oson, exception path himoyalanadi.

5. **Try/except wrapper `/accounting` POST blokida** — connection leak risk-ni nolga tushirish. `with get_db() as conn:` context manager yaratish.

6. **Logging — `app.logger`** — har failed insert, validation rejection, exception loglansin. Audit trail asos.

7. **Dashboard `QARZ QOLDIG'I` + formula chain i18n** — qolgan i18n gap-larni yopish. ~10 ta yangi translation kalit.

8. **Mobile responsive** — hamburger menu + media query. Sidebar `<= 768px` da off-canvas.

### P2 — texnik qarz (1-2 oy)

9. **Template-larni alohida `.html` fayllarga ajratish** — `render_template()` ga o'tish, Jinja auto-escape. XSS dan tabiy himoya.

10. **N+1 query pattern fix** — `/hourly`, `/kpi` batch queries. Performance scale.

11. **Test coverage** — bugun 14 test, kelajakda 100+ test, har funksiyaning happy + edge path.

12. **Schema + migration + seed-ni alohida fayllarga ajratish** — `database.py` 1649 qatordan modular `schema.py`, `migrations.py`, `seed.py` ga.

13. **`/projects` empty state UX** — "Yangi loyiha → /accounting → Proekt tab" yo'l ko'rsatish.

14. **Sortable column visual fix** — bitta neutral chevron, active sortda yo'nalish yo'naltiruvchi.

15. **Excel export streaming** — katta data uchun chunks orqali yuborish, brauzer timeout-ni oldini olish.

### P3 — agar tarmoqqa qo'yilsa (kritik prereq)

- Authentication (Flask-Login + bcrypt)
- CSRF protection (Flask-WTF)
- Rate limiting (Flask-Limiter)
- HTTPS (cert + nginx)
- Cookie `secure`, `httponly`, `samesite`
- API endpoint-larga auth middleware
- audit log table

Bularsiz tarmoqqa qo'yish — har bir foydalanuvchi to'liq ma'lumotni o'g'irlashi yoki o'zgartirishi mumkin.

---

## Audit jarayoni va artefaktlar

| Skill | Bajarilgan | Davomiyligi (CC) | Artefakt |
|---|---|---|---|
| `/retro global` | 2026-04-29 | ~3 daq | `~/.gstack/retros/global-2026-04-29-1.json` |
| `/investigate` ×2 | 2026-04-29 | ~6 + 10 daq | 5 bug fix + `test_loan_summary.py` |
| `/qa` Standard | 2026-04-29 | ~25 daq | `qa-report-mizan-finance-2026-04-29.md` + 24 screenshots |
| `/browse` (ushbu audit) | 2026-04-29 | ~5 daq | Perf timing matrix |
| `/investigate` Excel bug | 2026-04-29 | ~10 daq | `test_excel_per_page.py` |
| `/review` manual | 2026-04-29 | ~10 daq | Code quality findings (bo'lim 5) |
| `/qa-only` | 2026-04-29 | ~5 daq | XSS confirmation, mobile snapshot |
| `/cso` | 2026-04-29 | ~10 daq | OWASP + STRIDE matrix (bo'lim 4) |
| `/codex` | SKIPPED | 0 | Codex CLI o'rnatilmagan |

**Tuzatilgan fayllar:**
- `app.py`: 12 ta o'zgarish (HTML stray quote, dead code, i18n, Excel ?sheet=, success messages)
- `database.py`: 5 ta o'zgarish (loan_summary FX conversion, bare except → typed, unused import)
- `translations.py`: 3 ta yangi LOANS bloki (UZ/EN/RU x 22 kalit)
- `SETUP.bat`: 3 ta `/dev/null` → `nul`
- Yangi: `test_loan_summary.py`, `test_loans_i18n.py`, `test_excel_per_page.py`

**Hammasi ko'rib chiqildi va qaytariladigan tuzatish — git hozir kerak.**

---

## Holat

**STATUS: DONE_WITH_CONCERNS**

7 ta xato tuzatildi (1 critical FX, 1 high i18n, 1 high Excel UX, 4 ta others), 14 ta regression test yozildi, OWASP Top 10 audit mat ritsa to'liq. Eng kritik P0: stored XSS, hozir lokal-only bo'lgani uchun risk past, lekin tezda fix kerak (oqibat tarmoqqa o'tishda darhol kritik).

Codex second opinion olinmadi (CLI o'rnatilmagan).
