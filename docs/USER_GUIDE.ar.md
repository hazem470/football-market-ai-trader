# دليل المستخدم (بالعربي)

<div dir="rtl">

هذا الدليل مكتوب لشخص لم يشغّل مشروع Python من قبل. اتبع الخطوات بالترتيب.
**لا تتخطَّ إلى التداول الحقيقي** — الدليل كله مبني ليوصلك إليه بأمان، والبرنامج نفسه يرفض
التشغيل لو تخطيت خطوة.

> تذكير: هذه البرمجيات قد تخسر مالًا. لا شيء هنا نصيحة مالية ولا يوجد ضمان للربح.
> التداول الحقيقي معطّل حتى تفعّله أنت بشكل صريح.

---

## الخطوة 1 — ثبّت Python

تحتاج **Python 3.10 أو أحدث** (3.11 مستحسن).

* **ويندوز:** نزّل من [python.org/downloads](https://www.python.org/downloads/) وضع علامة صح على
  **«Add Python to PATH»** في المثبّت. لو نسيست الخطوة دي الأوامر مش هتشتغل.
* **ماك:** `brew install python@3.11`
* **لينكس:** `sudo apt install python3 python3-venv python3-pip`

تأكد:

```bash
python --version      # أو: python3 --version
```

لازم تشوف `Python 3.10` أو أعلى.

## الخطوة 2 — ثبّت Git

* **ويندوز:** [git-scm.com/download/win](https://git-scm.com/download/win)
* **ماك:** `brew install git`
* **لينكس:** `sudo apt install git`

تأكد: `git --version`

## الخطوة 3 — نزّل المشروع

```bash
git clone https://github.com/hazem470/football-market-ai-trader.git
cd football-market-ai-trader
```

## الخطوة 4 — ثبّت المكتبات

اعمل «بيئة معزولة» عشان ما تلخبطش نسخة بايثون عندك على الجهاز:

```bash
python -m venv .venv

# ويندوز (git-bash أو CMD):
.venv\Scripts\activate

# ماك / لينكس:
source .venv/bin/activate

pip install -r requirements.txt
```

هتلاقي `(.venv)` ظهر في أول سطر الأوامر. كل الخطوات الجاية بتحسب إن دي مفعّلة.

> لو `pip` بطيء أو واقع، استخدم `uv` لو متاح عندك:
> ```bash
> uv pip install --python .venv/Scripts/python.exe -r requirements.txt
> ```

## الخطوة 5 — جهّز ملف الإعدادات `.env`

```bash
python -m app.cli init
```

ده يعمل المجلدات، والقاموس (19 جدول)، وينسخ `.env.example` إلى `.env`.

**افتح ملف `.env` بأي محرر نصي الآن.** كل القيم اختيارية ما عدا `TRADING_MODE` وهو مضبوط
بالفعل على الوضع الآمن `backtest`.

> **ملف `.env` لن يُرفع على Git أبدًا** — مذكور في `.gitignore`. لا تنقله لمكان متابع ولا تلصق
> محتواه في أي محادثة أو Issue أو Discord.

## الخطوة 6 — افحص النظام

```bash
python -m app.cli check
```

لازم تشوف في الآخر:

```
READY - 0 critical failure(s), 0 warning(s)
```

لو ظهر `FAIL` في أي سطر، البرنامج هيقولك السبب بالضبط. أشهر حالة: الإنترنت واقع أو مزوّد بيانات
غير متاح — جرّب تاني.

## الخطوة 7 — شوف الأسواق الموجودة فعلًا

```bash
python -m app.cli markets
```

بيطبع كل أسواق كرة القدم الحقيقية على Polymarket في اللحظة دي، مع نوع السوق، والسعر القابل
للتنفيذ، والهامش، والسيولة. القائمة دي **ديناميكية** — مش قائمة مكتوبة جوه الكود.

## الخطوة 8 — احسب الإشارات (بدون أي شراء)

```bash
python -m app.cli signals --limit 25
```

هيطبع لكل سوق: احتمال النموذج، الاحتمال بعد المعايرة، السعر القابل للتنفيذ، **الربحية الواقعية**
بعد كل التكاليف، وفي الآخر القرار.

### إزاي تقرأ الإشارة

```
Signal ID      : SIG-2026-0920-6B3941
Market         : Arsenal vs Chelsea | Arsenal
Market type    : MATCH_RESULT
Model          : dixon_coles_match_result
Model prob     : 73.61%
Calibrated prob: 73.61%
Market price   : 30.00%      ← السعر اللي هتدفع هو فعلًا (الـ ask) مش منتصف السوق
Realistic edge : +37.81%     ← بعد الهامش والانزلاق والعمولة وعدم اليقين
Confidence     : 95.00%
Decision       : BUY
```

`Market price` هو السعر القابل للتنفيذ فعلًا. `Realistic edge` هو اللي فاضل بعد كل التكاليف.
القرار بيترفض دايمًا لو البيانات أو السعر مش موثوقين.

### معاني حالات الرفض

| الحالة | المعنى |
|---|---|
| `NO_TRADE` | كل حاجة متاحة، بس الفرصة مش كويسة |
| `INSUFFICIENT_DATA` | السوق سليم بس البيانات ناقصة أو قديمة أو مش متاحة |
| `UNSUPPORTED` | العائلة دي ملهاش مسار بيانات في النسخة الحالية — لا نخمّن |
| `BUY` / `SELL` | إشارة قابلة للتنفيذ وعدّت محرك المخاطرة كمان |

> **نسبة الرفض العالية طبيعية ودي مش عيب** — دي طريقة عمل النظام حسب التصميم.

## الخطوة 9 — الاختبار التاريخي (backtest)

```bash
python -m app.cli backtest --market-type MATCH_RESULT --league E0
```

رموز الدوريات (أكواد عامة من football-data.co.uk):

| الكود | الدوري |
|---|---|
| `E0` | الدوري الإنجليزي الممتاز |
| `E1` | دوري البطولة الإنجليزية |
| `D1` | الدوري الألماني |
| `I1` | الدوري الإيطالي |
| `SP1` | الدوري الإسباني |
| `F1` | الدوري الفرنسي |
| `N1` | الدوري الهولندي |
| `P1` | الدوري البرتغالي |

التقرير بيفصل بين حاجتين **منفصلتين تمامًا**:

* **MODEL PERFORMANCE** — جودة النموذج (Brier score, Log loss, المعايرة)
* **TRADING PERFORMANCE** — نتيجة التداول (P/L, ROI, أقصى انخفاض)

نموذج جيد لا يعني تنفيذًا مربحًا — ولهذا الفصل مقصود.

> التقرير كمان بيطبع **DATA LIMITATIONS** (حدود البيانات). اقرأها قبل أي استنتاج: أسعار الـ
> backtest مبنية على أسعار إغلاق المراهنات (بعد إزالة هامش المراهن) وليس على تاريخ أسعار
> Polymarket الحقيقي، لأن الأخير غير متاح مجانًا.

## الخطوة 10 — اضبط المعايرة

```bash
python -m app.cli calibrate --market-type MATCH_RESULT --league E0
```

بيتعلّم تحويل احتمال النموذج الخام إلى احتمال معاير أدق، ويحفظ النتيجة في
`artifacts/calibration.json` ويستخدمها تلقائيًا بعد كده.

## الخطوة 11 — وضع الظل (Shadow)

بيانات حقيقية، قرارات حقيقية، **صفر أوامر**.

```bash
python -m app.cli shadow --limit 25
```

اتأكد إن الإشارات منطقية، وإن كل رفض سببه مقنع.

## الخطوة 12 — التداول الوهمي (Paper)

بيانات حقيقية، رأس مال وهمي، تنفيذ محاكى على **كتاب الأوامر الحقيقي**، وحدود مخاطرة حقيقية.

```ini
TRADING_MODE=paper
```

```bash
python -m app.cli paper --limit 25 --iterations 4 --poll 120
```

راقب: هل الأوامر بترفض لأسباب منطقية؟ هل التعرض للسوق فضل تحت حدودك؟ هل الزر الطارئ عمل من
غير سبب؟

## الخطوة 13 — لوحة التحكم

```bash
streamlit run app/dashboard/streamlit_app.py
```

تفتح على `http://localhost:8501`. 15 تبويب: نظرة عامة، الأسواق، الإشارات، المراكز، الأوامر،
الأداء، الاختبار التاريخي، التداول الوهمي، المخاطرة، البيانات، الذكاء الاصطناعي، تليجرام،
المحفظة، السجلات، الإعدادات. **للقراءة فقط**.

## الخطوة 14 — التداول الحقيقي (فقط بعد كل ما سبق)

### 14.1 جهّز حساب Polymarket

1. اعمل حساب على [polymarket.com](https://polymarket.com).
2. **اعمل محفظة تداول منفصلة** — لا تعيد استخدام محفظتك الأساسية:
   * الأسهل: تسجيل الدخول بالإيميل (Magic) ويعطيك محفظة وسيطة (proxy).
   * أو اربط محفظة متصفح جديدة (MetaMask / Rabby / Coinbase Wallet) معمولة للمشروع ده فقط.
3. موّلها بـ **USDC على شبكة Polygon** — فقط المبلغ اللي تتحمل خسارته.
4. نفّذ صفقة صغيرة يدويًا من واجهة Polymarket مرة واحدة، عشان تتفعّل صلاحيات USDC (allowances).
5. لو بتستخدم محفظة وسيطة، اعرف **عنوان التمويل (funder address)** — العنوان اللي بيحفظ الـ USDC.

**لا تُدخل Seed Phrase في المشروع ده أبدًا. هيرفضها، وأنت مش محتاجها أصلًا.**

### 14.2 اضبط ملف `.env`

```ini
TRADING_MODE=live
POLYMARKET_ALLOW_LIVE_TRADING=true
POLYMARKET_PRIVATE_KEY=0x...        # مفتاح محفظة التداول المنفصلة فقط
POLYMARKET_CHAIN_ID=137
POLYMARKET_FUNDER_ADDRESS=0x...     # لو بتستخدم محفظة وسيطة
POLYMARKET_SIGNATURE_TYPE=1         # شوف الجدول تحت
```

| طريقة دخولك | `POLYMARKET_SIGNATURE_TYPE` |
|---|---|
| محفظة مباشرة (EOA) | `0` |
| إيميل Polymarket / Magic | `1` |
| MetaMask / Rabby / Coinbase | `2` |

### 14.3 شغّل

```bash
python -m app.cli live --limit 5 --confirm "I UNDERSTAND THE RISK" --i-understand-risk
```

قبل أي أمر، بيعمل الـ 15 فحص كاملين. **لو أي فحص حرج فشل، لن يُرسل أي أمر.**
ابدأ برصيد صغير جدًا وبقيمة `RISK_MAX_TRADE` صغيرة.

### 14.4 الإيقاف

* `Ctrl-C` في النافذة — إغلاق نظيف.
* أو من نافذة تانية:

```bash
python -m app.cli stop --reason "إيقاف يدوي"
```

زر الإيقاف بيمنع الأوامر الجديدة ويطلب إلغاء الأوامر المعلّقة، بس **مش بيقفل المراكز المفتوحة
بالقوة** — القرار ده بتاعك، وقواعد الخروج موثّقة في الكود.

### 14.5 إرجاع التداول الحقيقي لمقفول

في `.env` خلي:

```ini
TRADING_MODE=backtest
POLYMARKET_ALLOW_LIVE_TRADING=false
```

كده وضع live بقى غير قابل للوصول مهما استخدمت من أوامر.

---

## حل المشاكل الشائعة

| المشكلة | الحل |
|---|---|
| `python: command not found` | أعد تثبيت Python مع خيار «Add Python to PATH» |
| `pip install` بيفشل | تأكد إن البيئة مفعّلة `(.venv)`، واستخدم `python -m pip install ...` |
| `READY` مكانه `FAIL` | اقرأ سطر الفشل — غالبًا الإنترنت أو مزوّد بيانات خارج الخدمة |
| كل الإشارات `INSUFFICIENT_DATA` | الفرق المعروضة مش موجودة في تاريخ الدوريات المحمّلة — طبيعي. زوّد دوريات في `.env`: `FOOTBALL_DATA_UK_LEAGUES=E0,E1,D1,I1,SP1,F1,N1,P1` |
| `PLAYER_SHOTS` دايمًا مرفوض | متوقع — الـ API المجاني بتاع FPL مش بينشر تسديدات فردية |
| `wallet/funder mismatch` أو رصيد صفر | خطأ في `POLYMARKET_SIGNATURE_TYPE` أو `POLYMARKET_FUNDER_ADDRESS` |
| `Refusing to start` في live | دي البوابات الثلاثة بتشتغل — راجع الخطوة 14 |
| الداشبورد مفتحش | ইনستول `pip install -r requirements.txt` ثم جرّب بورت تاني: `--server.port 8502` |

الحل الكامل لكل الحالات في [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## تحتاج مساعدة؟

* الأوامر كلها: `python -m app.cli version`
* حالتك الحالية: `python -m app.cli status`
* الإعدادات الفعّالة: `python -m app.cli config`

</div>
