# MIZAN Finance v5 — User Manual

**Version 5.0 · Русская версия: [USER_MANUAL.ru.md](USER_MANUAL.ru.md)**

This manual describes how to operate MIZAN Finance v5 day to day and month to month.
It is written for two readers: the person who enters documents and closes the books,
and the director who needs to know what the hourly cost figure is built from.

The app runs in three languages (O'zbekcha, Русский, English). This manual quotes the
**English** labels. Where a screen is easier to find by its Uzbek name — the default
language — the Uzbek label follows in parentheses on first mention. A full
three-language glossary is in [§39](#39-reference).

---

## Contents

**Part I — Getting started**

1. [What this app is](#1-what-this-app-is)
2. [Installing and running](#2-installing-and-running)
3. [Logging in, language, and who can do what](#3-logging-in-language-and-who-can-do-what)
4. [Reading the screen](#4-reading-the-screen)

**Part II — Concepts you need before you start**

5. [Documents become ledger entries](#5-documents-become-ledger-entries)
6. [The chart of accounts, without the theory](#6-the-chart-of-accounts-without-the-theory)
7. [Settlement is allocation, not a checkbox](#7-settlement-is-allocation-not-a-checkbox)
8. [Where the hourly cost comes from](#8-where-the-hourly-cost-comes-from)
9. [The double-count rule](#9-the-double-count-rule)
10. [Periods](#10-periods)

**Part III — Everyday tasks**

11. [Counterparties](#11-counterparties)
12. [Sales invoice](#12-sales-invoice)
13. [Money in](#13-money-in)
14. [Purchase invoice](#14-purchase-invoice)
15. [Money out](#15-money-out)
16. [Payroll](#16-payroll)
17. [Loans](#17-loans)
18. [Dividends](#18-dividends)
19. [Manual entries and opening balances](#19-manual-entries-and-opening-balances)
20. [Timesheets](#20-timesheets)

**Part IV — Staff, cost and pricing**

21. [Staff register and salary history](#21-staff-register-and-salary-history)
22. [Equipment, asset classes, licenses, overhead](#22-equipment-asset-classes-licenses-overhead)
23. [Hourly Cost](#23-hourly-cost)
24. [Overhead reconciliation](#24-overhead-reconciliation)
25. [Projects and the price ladder](#25-projects-and-the-price-ladder)
26. [Budget](#26-budget)

**Part V — Month-end and reports**

27. [The month-end checklist](#27-the-month-end-checklist)
28. [The reports](#28-the-reports)
29. [Reopening a period](#29-reopening-a-period)

**Part VI — Administration**

30. [Settings](#30-settings)
31. [Users and roles](#31-users-and-roles)
32. [Chart of accounts and the account map](#32-chart-of-accounts-and-the-account-map)
33. [Lookup tables](#33-lookup-tables)
34. [Exchange rates](#34-exchange-rates)
35. [One-time migration from v4](#35-one-time-migration-from-v4)
36. [Backup and recovery](#36-backup-and-recovery)

**Part VII — Troubleshooting and reference**

37. [Error messages](#37-error-messages)
38. [When two numbers do not match](#38-when-two-numbers-do-not-match)
39. [Reference](#39-reference)

---

# Part I — Getting started

## 1. What this app is

MIZAN Finance v5 is a local, offline financial application for an architecture firm.
It runs on one computer, stores everything in a single file, and is reachable only from
the local network. There is no cloud service and no internet dependency.

**Its purpose is to calculate what one hour of each production employee's time actually
costs the firm.** Everything else in the app exists to make that number trustworthy.

That is worth saying plainly, because the app looks like an accounting system and is
one — it keeps a full double-entry ledger following the Uzbek national chart of accounts
(НСБУ №21). But the ledger is not the goal. The goal is that when the app tells you an
architect costs 92,400 so'm an hour, that figure is built from salaries actually paid,
overhead actually incurred, and equipment actually bought — not from numbers somebody
typed into a spreadsheet last year and never revisited.

From that one number the app derives everything a firm needs to quote work profitably:
the billing rate, project cost, project profitability, the price ladder for a new
proposal, and plan-versus-actual on a running job.

### What is new compared with v4

If you used MIZAN v4, three things have changed in kind, not just in degree:

| | v4 | v5 |
|---|---|---|
| **How money is recorded** | A transaction row with an `amount` and a `paid` column | A document that produces a balanced double-entry posting |
| **Where overhead comes from** | A hand-maintained table of monthly costs | The ledger — what was actually spent, averaged over a trailing window |
| **How a payment closes an invoice** | Editing the invoice's `paid` field | A separate payment document *allocated* to the invoice |

v4 is still installed and still runs. It uses a different database file
(`mizan_finance.db`) and a different port, so both can be open at the same time. v5's
database is `mizan5.db`. Nothing you do in v5 touches v4.

If you are moving from v4 for real, see [§35](#35-one-time-migration-from-v4) — there is
a one-time migration that replays your entire v4 history into v5.

---

## 2. Installing and running

### Requirements

Python 3.10 or newer, with Flask and openpyxl installed:

```bash
pip install flask openpyxl
```

### Starting the app

From the `mizan5` directory:

```bash
python app.py
```

The app finds a port (5000 if it is free, otherwise any free port), prints a banner, and
opens your browser automatically after about a second and a half:

```
==================================================
  MIZAN Finance v5.0
  http://127.0.0.1:5000
  To'xtatish: Ctrl+C
==================================================
```

Press `Ctrl+C` in that window to stop the app. Stop it deliberately rather than closing
the window while people are still working, so nothing is interrupted mid-write.

### The first run

On the very first start, the app creates its database and a single administrator
account, then prints the password **once**:

```
Admin foydalanuvchisi yaratildi / Admin user created
    login:  admin
    parol / password:  kX8vQ2mN4pR7
Bu parol faqat bir marta ko'rsatiladi.
```

**Write it down before you close that window.** It is not stored anywhere in readable
form and cannot be recovered — if it is lost, the only route back in is to delete the
database and start over. You can avoid the problem entirely by choosing the password
yourself in advance with `MIZAN_ADMIN_PASSWORD` (below).

Once logged in, create real user accounts — see [§31](#31-users-and-roles).

### Environment variables

All optional. Set them in the shell before `python app.py`, or put them in a file called
`.env` next to `app.py` as `KEY=value` lines, one per line. Real environment variables
always win over the `.env` file.

| Variable | What it does |
|---|---|
| `PORT` | Force a specific port instead of auto-detecting. `PORT=5177 python app.py` |
| `MIZAN_NO_BROWSER` | Set to `1` to stop the browser opening automatically. Useful when running headless. |
| `MIZAN_ADMIN_PASSWORD` | Sets the first admin password instead of generating one. Only has effect on a database with no users yet. |
| `MIZAN_SECRET_KEY` | The session signing key. If unset, the app generates one and stores it in `.mizan5_secret_key`. Changing it logs everyone out. |
| `MIZAN5_DB` | Use a different database file. Handy for a test copy: `MIZAN5_DB=test.db python app.py` |

### Who can reach it

The app refuses any connection that does not come from the local network. Allowed
addresses start with `127.` (this computer), `192.168.`, `10.`, or `172.`. Anything else
receives:

```
Access denied: this app is only available on the local network.
```

The server also binds to `127.0.0.1`, so out of the box it is reachable **from this
computer alone**. Letting colleagues on the office network use it requires changing that
binding deliberately — it will not happen by accident, and it should not be done without
considering who else is on that network.

### Where the data lives

Everything is in one file: **`mizan5.db`**, in the `mizan5` directory. Accounts,
documents, postings, staff, projects, settings — all of it. Copy that one file and you
have copied the entire system. See [§36](#36-backup-and-recovery).

---

## 3. Logging in, language, and who can do what

### Logging in

Open the app and you get a login card: **Username** and **Password**, then **Log in**.
A wrong entry shows *Wrong username or password* — the app does not reveal which of the
two was wrong. A deactivated user cannot log in even with the correct password.

### Language

Three languages, switchable at any time and remembered for a year in a browser cookie:

- On the **login page**, the three links under the form: `UZ · RU · EN`
- Once logged in, at the **bottom of the sidebar**, next to your username

The default is Uzbek. Switching language changes only what you see — it never changes
data, and two people can use the app in different languages at the same time.

### Roles

Every user has one of three roles. They are cumulative: a manager can do everything a
viewer can, and an admin everything a manager can.

| Role | Label | Can do |
|---|---|---|
| `viewer` | Viewer (Kuzatuvchi) | Read every page and every report. Cannot create, edit, post, or delete anything. |
| `manager` | Manager (Menejer) | Everything above, plus all normal work: enter and **post** documents, delete drafts, manage counterparties, staff, salaries, timesheets, equipment, licenses, overhead, projects, milestones and loans; run payroll and remit it; enter exchange rates; take a rate snapshot. |
| `admin` | Administrator | Everything above, plus everything irreversible or structural: **void** a posted document, **close and reopen periods**, post depreciation and FX revaluation, edit the chart of accounts and the account map, change asset-class default lives, reclassify equipment, change rate settings, change app settings, manage users and lookup tables. |

The split is deliberate. Day-to-day data entry is manager work. Anything that rewrites
history (voiding a posted document), freezes it (closing a period), or changes how every
number in the firm is calculated (accounts, rate settings) is admin work.

Attempting something above your level returns **403 — Ruxsat yo'q** (*Access denied*).
Nothing is damaged; press Back.

---

## 4. Reading the screen

### The sidebar

The sidebar is the whole navigation. It is grouped into six sections, in this order:

| Group | Item | Route | What it is for |
|---|---|---|---|
| — | **Dashboard** (Boshqaruv paneli) | `/` | Cash, receivables (aged), payables, profit, runway, capacity, on-course projects, FX, loans, CFO ratios, top projects, recent documents |
| **Documents** | **Sales Invoices** | `/documents/sales_invoice` | Invoices you issue to clients |
| | **Purchase Invoices** | `/documents/purchase_invoice` | Invoices suppliers issue to you |
| | **Money In** | `/documents/cash_in` | Cash and bank receipts |
| | **Money Out** | `/documents/cash_out` | Cash and bank payments |
| | **Payroll** | `/payroll` | The monthly payroll sheet and its payout |
| | **Loans** | `/loans` | Money borrowed and money lent |
| **Accounting** | **Journal** | `/journal` | Every posting, newest first |
| | **Trial Balance** | `/trial-balance` | Opening, turnover and closing per account |
| | **Profit & Loss** | `/reports/pnl` | Income and expense for a date range |
| | **Balance Sheet** | `/reports/balance-sheet` | Assets against liabilities and equity |
| | **VAT Report** | `/reports/vat` | Output VAT against input VAT |
| | **Periods** | `/periods` | Close and reopen months; depreciation; FX revaluation |
| **Staff** | **Hourly Cost** | `/rates` | The central output: cost per hour per person |
| | **Staff** | `/staff` | Employee register and salary history |
| | **Timesheets** | `/staff/hours` | Hours per person, per project, per month |
| | **Equipment** | `/staff/equipment` | Equipment, asset classes, licenses, overhead budget |
| | **KPI** | `/kpi` | Per-employee hours, utilisation, cost and revenue value |
| **Projects** | **Projects** | `/projects` | Project register; each opens a detail page |
| | **Budget** | `/budget` | Frozen plan against actual, and month-by-month drift, across all projects |
| | **Pricing** | `/pricing` | A scratch quote for a prospect, and a project picker |
| **Reports** | **Cash Flow** | `/reports/cashflow` | Money in and out by period |
| | **AR / AP** | `/reports/aging` | Who owes you, whom you owe, by age |
| **Admin** | **Counterparties** | `/counterparties` | Clients, vendors, founders, banks |
| | **Bank accounts** | `/bank-accounts` | The firm's own bank accounts behind 5110 / 5210 |
| | **Chart of Accounts** | `/accounts` | The accounts themselves and what posts where |
| | **Settings** | `/settings` | Parameters, exchange rates, users, lookups |

Below the nav: the language switcher, your username, and **Log out**.

### Pages that are not in the sidebar

Several useful pages are reached by link rather than by menu:

| Page | Route | How you get there |
|---|---|---|
| **Manual Entry** | `/documents/manual` | The **[+ Manual entry]** button on `/journal` |
| **Opening Balances** | `/documents/opening` | Type the address directly; used once at setup ([§19](#19-manual-entries-and-opening-balances)) |
| **Dividend** | `/documents/dividend` | Type the address directly ([§18](#18-dividends)) |
| **Account ledger** | `/accounts/<id>/card` | Click any account code on `/accounts`, `/trial-balance` or `/journal` |
| **Overhead reconciliation** | `/rates/overhead` | The **[Ledger]** button on `/staff/equipment`, or the link on `/rates` |
| **Project detail** | `/projects/<id>` | Click a project name on `/projects` |
| **Document detail** | `/documents/<id>` | Click any document number anywhere |

### How every list page works

Once you have used one list page you have used them all. They share one layout:

```
+----------------------------------------------------------+
|  Page title                                    [ + New ]  |
+----------------------------------------------------------+
|  From [____] To [____] Status [___] Search [___] Filter   |
+----------------------------------------------------------+
|  Date   Number   Counterparty   Amount   Status      (/)  |
|  ...                                                      |
|  Total                          123 456                   |
+----------------------------------------------------------+
```

- **[+ New]**, top right, opens a dialog for a new record.
- The **pencil** at the end of a row opens the *same* dialog, filled in with that row.
- **Filter** applies the filter bar; **Reset** clears it. Filters live in the address
  bar, so a filtered view can be bookmarked or sent to a colleague.
- Most tables end with a **totals row** covering the rows currently shown — the filtered
  set, not the whole table.
- Document lists are **paged** (25 / 50 / 100 / 200 per page, chosen at the foot of the
  table) and **sortable** — click a column header to sort by it, click again to flip the
  direction. The page number, sort and per-page setting all live in the address bar too.
  Document lists also filter by **counterparty**, **project** and **responsible person**,
  and the search box reaches notes and project names.

Inside the dialog, forms behave the ordinary way: fill in, press **Save**, the page
reloads with a confirmation or an error at the top. There is no autosave, and nothing is
kept if you press **Cancel** or `Esc`.

### Line grids

Document dialogs have a grid at the bottom — invoice lines, payment allocations, payroll
rows, journal debits and credits. They all work the same way:

- **[+ Add line]** appends a row; the **✕** on a row removes it.
- Totals under the grid recalculate as you type: net, VAT, gross, allocated,
  unallocated, debit, credit.
- On a manual or opening entry, a note under the grid shows whether debits equal credits.
  **Until they do, the save button stays disabled.** The app will not let you build an
  unbalanced entry, so the books cannot be broken through this form.

### The Post checkbox

At the bottom of every document dialog there is a checkbox — **Post** — and it is
**ticked by default**. Leaving it ticked saves the document *and* writes it to the ledger
in one step. That is what you want almost always.

Untick it to save a **draft**: the document is stored, remains editable, and has no
effect on any report. See [§5](#5-documents-become-ledger-entries).

---

# Part II — Concepts you need before you start

These six sections are short, and reading them once will save you a great deal of
confusion. v5 behaves differently from v4 in ways that are not visible on screen.

## 5. Documents become ledger entries

You never write accounting entries directly. You enter **documents** — an invoice, a
receipt, a payment — and the app derives the accounting from them:

```
   You fill in a dialog
            |
            v
   A document is saved            <- you can still edit this
            |
            v
   A posting rule builds the debits and credits
            |
            v
   The entry is checked and written to the ledger
            |
            v
   Every report is a view of the ledger
```

Because every report reads the ledger and nothing else, a document that has not been
posted appears in no report at all.

### The three states

| State | Label | What it means |
|---|---|---|
| `draft` | Draft (Qoralama) | Saved but not in the ledger. Editable and deletable. Invisible to every report. |
| `posted` | Posted (Provodka qilingan) | In the ledger. **Not editable.** Visible everywhere. |
| `void` | Void (Bekor qilingan) | Was posted, then reversed. A mirror entry cancels it. |

### Why a posted document cannot be edited

This is the single biggest behavioural difference from v4, and the thing people trip
over first.

In v4 you could open a transaction and change the amount. In v5 you cannot, and the app
will tell you so:

> *A posted document cannot be edited — void it instead*

The reason is that the document is no longer the only record of what happened. A posting
exists, dated, numbered, sitting in a month that may already be closed, feeding a trial
balance somebody has already looked at. Silently changing the document underneath all of
that would make the ledger disagree with itself.

**To correct a posted document:** press **Void**, give a reason, and enter a fresh,
correct document. Both remain visible. That is a feature, not clutter — the correction
history is the audit trail, and it is what an inspection expects to see.

### What Void actually does

Voiding does not delete anything. It writes a second entry that is an exact mirror of the
first: every debit becomes a credit of the same amount on the same account, with the same
project, counterparty and staff tags. The pair nets to zero on every account, in every
report, at every date. The original stays in the journal marked as reversed; the mirror
is labelled *Storno*.

A document can only be voided once. Voiding is **admin-level** work.

### Deleting

Only a **draft** can be deleted, and deleting it removes it completely. A posted document
can never be deleted — only voided. If you want a posted document gone from the record
entirely, that is not something this app will do, by design.

---

## 6. The chart of accounts, without the theory

The app ships with the Uzbek national chart of accounts (НСБУ №21). You do not need to
learn it. In everyday work you will meet perhaps twenty accounts, and the app picks
almost all of them for you.

### The four kinds

Every account has a **Kind** (Вид), which decides which side it naturally sits on:

| Kind | Name | Plain meaning | Grows on the |
|---|---|---|---|
| **А** | Asset (Aktiv) | Something the firm owns or is owed: cash, receivables, equipment | Debit side |
| **КА** | Contra-asset (Kontraktiv) | A deduction from an asset — accumulated depreciation is the only one you will meet | Credit side |
| **П** | Liability / Equity (Passiv) | Something the firm owes: suppliers, salary, taxes, loans; and the owners' capital | Credit side |
| **Т** | Transactional (Tranzaksion) | Income and expense. Emptied into the financial result when the year closes | Either, by type |

The only practical consequence: on the balance sheet, А accounts appear as assets, П
accounts as liabilities and equity, КА accounts as deductions shown in parentheses.
Т accounts appear on the Profit & Loss instead, and start each year at zero.

### The accounts you will actually meet

| Code | What it holds | Where it comes from |
|---|---|---|
| **5110** | Bank account (so'm) | Money in and out, bank |
| **5010** | Cash till | Money in and out, cash |
| **5210** | Foreign-currency account | Any document in a currency other than UZS |
| **4010** | Receivable — what clients owe you | Sales invoices |
| **6010** | Payable — what you owe suppliers | Purchase invoices |
| **6310** | Advances received | A client payment with no invoice behind it |
| **4310** | Advances issued | A supplier payment with no invoice behind it |
| **6410.1** | Output VAT (VAT you charged) | Sales invoices |
| **4410** | Input VAT (VAT you paid, reclaimable) | Purchase invoices |
| **6710** | Salary payable — net pay owed to staff | Payroll accrual |
| **6420.1** | PIT withheld (JSHDS / НДФЛ) | Payroll accrual |
| **6520** | Social contribution payable | Payroll accrual |
| **2010** | Work in progress — direct project cost | Payroll for production staff; project-tagged purchases |
| **9030** | Revenue from work and services | Sales invoices |
| **9420** | Administrative expenses | Purchases not tagged to a project |
| **9420.1** | Depreciation expense | Monthly depreciation run |
| **9420.2** | Administrative salary | Payroll for admin staff |
| **9420.3** | Licenses and software | License costs |
| **9910** | Financial result | Period close |
| **0000** | Suspense — opening balances only | Opening balance document |

The three highlighted children of 9420 — **9420.1, 9420.2 and 9420.3** — exist for one
specific reason, explained in [§9](#9-the-double-count-rule). Do not change their
settings without reading it.

### Purposes, not codes

The app never has an account code written into its logic. Instead each posting rule asks
for a **purpose** — "the receivable account", "the bank account", "the revenue account" —
and a lookup table maps purposes to real accounts.

That table is on `/accounts`, in the card headed **Posting → Account**. It means the firm
can re-point any purpose at a different account without touching code. If your bank
movements should go to a different ledger account, you change it there once and every
future posting follows. If the firm has *several* real accounts at the bank behind the
one code 5110, that is not a re-mapping — register them on `/bank-accounts`
([§15](#15-money-out)).

---

## 7. Settlement is allocation, not a checkbox

There is no "paid" field in v5. Nothing you tick marks an invoice as settled.

Instead: an invoice is one document, a payment is another, and you **allocate** the
payment to the invoice. The invoice's outstanding balance is its total minus everything
allocated to it.

```
   Sales invoice SF-2026-00042        12 000 000
        ^                     ^
        |                     |
   PK-...-00113  5 000 000    |          outstanding 7 000 000
   PK-...-00131  7 000 000 ---+          outstanding 0
```

This sounds like more work than editing a `paid` column, and for a single full payment it
is marginally more clicking. What you get in return:

- **One payment can settle several invoices.** A client transfers 30 million against
  four invoices; you enter one receipt and split it four ways. In v4 that was four
  separate edits and the bank statement no longer matched anything.
- **Every payment keeps its own date, method and currency.** Partial payments do not
  overwrite each other.
- **The cash-flow report is real.** Each receipt appears on the day it arrived.

### What happens to money you do not allocate

The dialog shows a live **Unallocated** figure. Whatever is left over when you save has
to go somewhere, and the app decides as follows:

| Situation | Where the remainder goes |
|---|---|
| Money **in**, counterparty named | **6310** — advance received (a prepayment) |
| Money **out**, counterparty named | **4310** — advance issued (a prepayment to a supplier) |
| Money **in**, no counterparty | **9390** — other operating income |
| Money **out**, no counterparty | **9430** — other operating expense |

The advance case is normal and correct: a client who pays before you invoice genuinely
has a prepayment with you, and it clears itself when you invoice and allocate later.

The no-counterparty case is a fallback, not a destination. If receipts are landing on
9390 regularly, someone is skipping the counterparty field. The two are deliberately
different accounts — 9430 sits in the overhead pool and 9390 does not, so an
unattributed *receipt* posted to 9430 would subtract itself from overhead and quietly
lower every hourly rate in the firm.

### Rules the app enforces

- You cannot allocate more than the payment amount.
- You cannot allocate more than the invoice's outstanding balance.
- You can only allocate against a **posted** invoice — a draft has no balance to settle.

---

## 8. Where the hourly cost comes from

This is the number the app exists to produce. It is worth understanding what feeds it,
because if a figure on `/rates` looks wrong, the cause is always one of these inputs.

### Available hours

```
available hours = (365 - 104 weekends - holidays - leave) / 12 * 8
```

With the default 14 public holidays and 20 days of leave and sickness, that is about
**151 hours a month**, or 1,816 a year. That sits inside the 1,760–1,880 band that
architecture and engineering practice uses internationally.

This is not 176 hours. Using 176 — twelve months of 22 working days — would assume nobody
ever takes a holiday, and would understate every hourly cost by roughly 15%.

### The cost stack

For each production employee, eight components are added up to give the full monthly cost
of employing that person, then divided by available hours:

| Component | What it is | Maintained on |
|---|---|---|
| Gross salary | Base + premium | `/staff` |
| Tax | PIT on the gross | `/settings` (`tax_rate`) |
| Social | Employer social contribution | `/settings` (`social_rate`) |
| Admin share | This person's share of all administrative salaries | `/staff` (the admin employees) |
| Personal equipment | Their own computer, monitor, desk — monthly depreciation | `/staff/equipment` |
| Personal licenses | Their Revit, 3ds Max and so on — annual cost divided by 12 | `/staff/equipment` |
| General equipment share | Their share of shared printers, servers, furniture | `/staff/equipment` |
| Overhead share | Their share of rent, utilities, and other indirect cost | The ledger, or `/staff/equipment` |

```
cost rate    = total monthly cost / available hours
billing rate = cost rate x markup          (markup 2.0 by default)
```

The markup is a single multiplier that absorbs both the utilization loss — nobody bills
100% of their available hours — and the target margin. No further margin is added
anywhere else; adding one would be double-counting.

### The four shared costs

Admin salaries, general equipment and overhead are not attributable to one person, so
they are spread across the production staff. The default basis is **direct labour
cost**: a senior architect on a higher salary absorbs proportionally more overhead than a
junior one. The alternative basis is **billable hours**, which is what v4 used.

Either way, the shares always add up to exactly 1 — every so'm of overhead is charged to
somebody. Nothing is left unrecovered.

You choose the basis on `/rates` — see [§23](#23-hourly-cost).

---

## 9. The double-count rule

**This is the most important warning in the manual.** It concerns one setting on one
page, and getting it wrong inflates every cost rate, every billing rate and every price
the firm quotes — with nothing on screen looking wrong.

### The situation

Some costs reach the hourly rate through a **register** — a hand-maintained list the rate
engine reads directly:

| Register | Page | Feeds |
|---|---|---|
| Equipment | `/staff/equipment` | Personal and general equipment depreciation |
| Staff and salaries | `/staff` | Admin share |
| Licenses | `/staff/equipment` | Personal licenses |

The same costs are *also* real expenses, and the ledger books them as such: depreciation
to 9420.1, administrative salary to 9420.2, licenses to 9420.3.

Meanwhile, the overhead pool is built by summing every account flagged **Indirect**
(`cost_pool = indirect`) and spreading it across production staff.

### The rule

> **An account must not be flagged Indirect if the same cost is already charged from a
> register.**

If it is, the cost is charged twice: once by the register, once through the overhead
share, with nothing anywhere to offset it. Every rate simply comes out too high.

### What this looks like in practice

The three accounts are shipped flagged **Excluded** (`Hisobga olinmaydi`) for exactly
this reason:

| Account | Register that already charges it | Effect if wrongly flagged Indirect |
|---|---|---|
| **9420.1** Depreciation | Equipment register | One 15 million so'm laptop moved its owner's cost rate from 73,744 to 93,018 — **+26%** |
| **9420.2** Admin salary | Staff register | On the real firm data (27 production, 11 admin): +183 million so'm a month in the pool, **+13% on every rate** |
| **9420.3** Licenses | License register | Grows with the size of the license estate |

**Excluded does not mean ignored.** The cost is fully counted — it is simply counted
through its register instead of through the pool. The flag only says *do not count it a
second time here*.

### The safeguards

Three things protect this, and you should know they exist:

1. The app **refuses to post** depreciation, admin payroll or a license cost to an
   account that is flagged Indirect. You get:
   > *This account is in the indirect pool — the cost is already charged from its
   > register and would be counted twice*
2. Every time the app starts, it **re-corrects** those three accounts back to Excluded.
3. The automated test suite fails if posting depreciation or payroll moves any cost rate
   by even one so'm.

So the rule is hard to break permanently. But between an edit and a restart, rates on
screen will be wrong, and nothing will look broken.

### The one gap that is still open

Equipment bought through a purchase invoice is expensed to **9420** — which *is* in the
pool — while the equipment register depreciates the same asset into the same rates. That
is the same double count coming in through a different door.

The app detects it and warns you on `/rates`:

> *Uncapitalized equipment (not on 0150)*

The fix is manual: when you enter a purchase invoice for equipment, set that line's
account to **0150** (or the right asset account for its class) instead of leaving the
default. See [§14](#14-purchase-invoice).

---

## 10. Periods

Every month is a **fiscal period**, created automatically the first time something is
posted into it, and has one of three states:

| State | Label | Meaning |
|---|---|---|
| `open` | Open (Ochiq) | Normal. Anything can be posted. |
| `soft_closed` | Soft closed (Yumshoq yopilgan) | No new postings accepted. Reversible. |
| `hard_closed` | Hard closed (Qattiq yopilgan) | No new postings accepted. The intended final state. |

Both closed states refuse postings identically:

> *This period is closed — posting refused*

That includes **back-dated** postings. If March is closed and you try to enter a document
dated in March, it is refused — which is the entire point. Closing a month means the
numbers reported for that month will not change afterwards.

### Opening, annotating and deleting a period

Periods appear on their own when a document is posted into a new month. **[+ Period]**
opens one ahead of time (the next month is suggested). Each row has a notes box — a
one-line reminder such as *closed after the audit* — saved with the tick. An open
period with **no journal entries** can be deleted with **×**; one with entries cannot.

The list also shows how complete the close is: the number of employee rate snapshots
written and the timesheet rows stamped with their rate, against the rows the month has.

### Soft close and hard close

**[Soft close]** locks the month and takes the rate snapshots but can be reopened from
this page. **[Close period]** is a hard close: it does everything a soft close does and
is **final** — the UI will not reopen it. A correction to a hard-closed month is a
reversal entry in an open month, which keeps the reported figures for that month exactly
as they were reported.

### What closing a month actually does

Closing runs a sequence, in this order, and the order matters:

1. Posts that month's **depreciation**, if it has not been posted already.
2. Reads the **Profit & Loss** for the month.
3. **Freezes the hourly rates** for the month, calculated as of the month's last day —
   not as of today. Closing March in August stamps March's salaries, March's overhead
   window and March's equipment onto March.
4. Writes the **closing entry**: every income and expense account is emptied into
   **9910**, the financial result, leaving the net profit or loss there.
5. Marks the period closed.

After that, every Т account starts the next month at zero, and the month's result sits
in 9910.

Closing and reopening are **admin-level** actions, on `/periods`. The full month-end
procedure is [§27](#27-the-month-end-checklist).

---

# Part III — Everyday tasks

Each section below follows the same shape: **where** the page is, **what to fill in**,
**what it does to the ledger** (so an accountant can check it), and **how to confirm it
worked**.

## 11. Counterparties

**Where:** sidebar → Admin → **Counterparties** (`/counterparties`)

Everyone the firm exchanges money with lives here: clients, suppliers, founders, banks,
the tax office. You will want a counterparty to exist before you write an invoice for it.

### Creating one

**[+ New]**, then:

| Field | Notes |
|---|---|
| **Name** | Required. The short working name you will pick from dropdowns. |
| **Full / legal name** | The name as it appears on contracts. |
| **ИНН / ПИНФЛ** | Tax number. Shown on the VAT report, so fill it in for anyone you invoice. |
| **VAT reg code** | If they have one. |
| **Type** | `client`, `vendor`, `founder`, `bank`, `state`, `staff`, `other` |
| **Currency** | Their usual currency. A default only; any document can override it. |
| **Tel.**, **Address**, **Bank details**, **Notes** | Reference information. |
| **Active** | Untick to hide from dropdowns without deleting. History is preserved. |

Type is a label for filtering — it does not change any accounting. A counterparty can be
both a client and a supplier; pick whichever they mostly are.

### The list

Two columns are computed live from the ledger rather than stored:

- **Receivable** — what this counterparty owes you right now (their unpaid sales invoices)
- **Payable** — what you owe them (your unpaid purchase invoices)

That makes this page a usable debtor and creditor list. For an aged view — how *late*
each amount is — use `/reports/aging` ([§28](#28-the-reports)).

**Filter bar:** search by name or ИНН, filter by type.

---

## 12. Sales invoice

**Where:** sidebar → Documents → **Sales Invoices** (`/documents/sales_invoice`)

An invoice you issue to a client. This is what creates revenue and a receivable.

### Entering one

**[+ New]** opens the dialog.

**Header:**

| Field | Notes |
|---|---|
| **Date** | Required. Determines which period the posting lands in. |
| **Customer** | **Required** — the invoice is refused without one. |
| **Their document #** | The client's own reference, if they gave you one. |
| **Project** | Sets the default project for all lines. |
| **Contract** | Contract reference, free text. |
| **Due date** | Drives the AR aging report and the overdue flags. Worth filling in. |
| **Responsible** | The employee who owns this document — optional, filterable on the list |
| **Currency** | `UZS` or `USD`. Choosing USD reveals the exchange rate field. |
| **Description** | What the invoice is for. |

**Line grid** — at least one line is required:

| Column | Notes |
|---|---|
| **Description** | The work being billed. |
| **Qty** and **Price** | Optional convenience — the app multiplies them into Net. |
| **Net amount** | The amount excluding VAT. This is the number that matters. |
| **VAT %** | Pre-filled from the `vat_rate` setting (12 by default). Set to 0 for an exempt line. |
| **VAT amount** | Calculated from the rate; you can override it. |
| **Project** / **Milestone** | Per line, overriding the header. |

Totals recalculate as you type: subtotal, VAT, and gross total.

> **Tag your lines with a project.** Project profitability, the Budget page and every
> per-project figure in the app read the project tag on the revenue line. An untagged
> line still posts correctly but is invisible to project reporting.

**Post** is ticked by default. Leave it ticked unless you are preparing a draft.

### What it posts

```
Dr 4010  Receivable          total (incl. VAT)
    Cr 9030  Revenue              net, one line per invoice line, carrying its project
    Cr 6410.1 Output VAT          total VAT
```

Memo: `Hisob-faktura <number>`. Number format: **SF-2026-00042**.

### Checking it

- The invoice appears in the list with an **Outstanding** figure equal to its total.
- The client's **Receivable** on `/counterparties` goes up by the same amount.
- It appears on `/reports/vat` under Output VAT.
- Revenue appears on `/reports/pnl` for the invoice date.

---

## 13. Money in

**Where:** sidebar → Documents → **Money In** (`/documents/cash_in`)

Every so'm arriving: client payments, loan proceeds, refunds.

### Entering one

**[+ New]**, then:

| Field | Notes |
|---|---|
| **Date** | The day the money actually arrived. |
| **Payer** | The counterparty. Not strictly required, but see below. |
| **Payment method** | `bank`, `naqd` (cash), `karta`, `online`. This chooses the account. |
| **Amount** | The full amount received. |
| **Currency** | Non-UZS goes to the foreign-currency account 5210. |
| **Purpose** | Free-text purpose of payment. |

**The allocation grid** is the heart of this screen. Once you select a payer, their open
invoices are listed with a box beside each. Type how much of this receipt settles each
invoice.

Under the grid, two running figures:

- **Allocated** — the sum you have distributed
- **Unallocated** — what is left

Bring Unallocated to zero whenever you can. If you cannot — the client has prepaid —
leave it, and the app books the remainder as an advance on **6310**. That is correct
accounting, and it clears itself when you later invoice and allocate against it.

There is also a **line grid** below, for receipts that are not invoice settlements at all
and belong on a specific account.

### What it posts

```
Dr 5110 (bank) / 5010 (cash) / 5210 (foreign currency)     total
    Cr 4010  Receivable        for each allocated invoice
    Cr <account>              for each explicit line
    Cr 6310  Advance received  whatever is left, if a payer is named
    Cr 9390  Other income      whatever is left, if no payer is named
```

Memo: `Pul tushumi <number>`. Number format: **PK-2026-00113**.

### Checking it

- The settled invoice's **Outstanding** drops on the Sales Invoices list; at zero its
  status shows as fully settled.
- Cash on the Dashboard rises.
- The receipt appears in `/reports/cashflow` in the period of its date.

---

## 14. Purchase invoice

**Where:** sidebar → Documents → **Purchase Invoices** (`/documents/purchase_invoice`)

An invoice a supplier issues to you: outsourcing, materials, services, equipment.

### Entering one

Same shape as a sales invoice, with **Vendor** in place of Customer. A vendor and at
least one line are both required.

**The account each line lands on is the important decision here.** If you leave the line
account blank, the app chooses:

| The line is | Default account | Meaning |
|---|---|---|
| Tagged to a **project** | **2010** Work in progress | A direct project cost. Appears in that project's cost and profitability. |
| **Not** tagged to a project | **9420** Administrative expenses | Overhead. Goes into the indirect pool and is spread across all production staff. |

That default is right most of the time. There are two cases where you must override it:

**Buying equipment.** If the invoice is for a laptop, a monitor, a desk — anything you
will also enter in the equipment register — set the line account to **0150** (computer
equipment) or the correct asset account for its class. If you leave it on the default, the
laptop is expensed to 9420 *and* depreciated through the equipment register, and its cost
lands in the hourly rates twice. See [§9](#9-the-double-count-rule). The app warns about
this on `/rates` as *Uncapitalized equipment*.

**Buying a software license.** Set the line account to **9420.3**, not 9420, for the same
reason — the license register already charges it.

### What it posts

```
Dr <line account>  (2010 if project-tagged, else 9420)   net per line
Dr 4410  Input VAT                                        total VAT
    Cr 6010  Payable                                       total incl. VAT
```

Memo: `Xarid hisob-fakturasi <number>`. Number format: **SP-2026-00088**.

### Checking it

- The vendor's **Payable** on `/counterparties` rises.
- It appears on `/reports/vat` under Input VAT.
- If project-tagged, the project's Cost on `/projects/<id>` rises.

---

## 15. Money out

**Where:** sidebar → Documents → **Money Out** (`/documents/cash_out`)

Every so'm leaving: supplier payments, rent, taxes, salary payouts.

Identical in shape to Money In, mirrored. **Recipient** instead of Payer; the allocation
grid lists your open *purchase* invoices.

### What it posts

```
Dr 6010  Payable            for each allocated invoice
Dr <account>               for each explicit line
Dr 4310  Advance issued     leftover, if a recipient is named
Dr 9430  Other expense      leftover, if no recipient is named
    Cr 5110 / 5010 / 5210    total
```

Memo: `Pul chiqimi <number>`. Number format: **RS-2026-00204**.

### Which bank account the money leaves from

**Where:** sidebar → Admin → **Bank accounts** (`/bank-accounts`)

One ledger code — 5110 for so'm, 5210 for currency — usually stands for several real
20-digit accounts at the bank. The app follows the 1С convention: the ledger account stays
one code, and each posting on it names the bank account as an *analytic* (субконто
«Банковские счета»). Nothing that adds up cash by account changes; the bank account is a
breakdown underneath.

Register each account once on `/bank-accounts`: **Ledger account** (5110 or 5210),
**Name**, **Account number**, **Bank**, **MFO**, **Currency**, and tick **Default** on the
one most payments leave from. The page then shows every account's balance and, per ledger
account, an **Unassigned** row for postings made before the register existed.

From then on every Money Out, Money In, payroll remittance and loan document has a
**Bank account** dropdown, pre-set to the default and hidden for cash-till payments. It is
part of the payment order: the document detail page shows the payer account number, bank
and MFO. The rule the app enforces:

- A ledger account with **no** bank accounts registered posts exactly as before.
- Once it has one, a posting on it **without** a bank account is refused
  (*Choose a bank account — account 5110 is subdivided into bank accounts*), as is a bank
  account whose currency differs from the document's, an inactive one, or one that belongs
  to a different ledger account.
- The dropdown decides the ledger account: choosing a USD bank account sends the money to
  5210, whatever the payment method says.

**Filling in the history.** Postings made before the register was filled carry no bank
account. If they all belong to one real account — the usual case — an administrator
presses **Assign** on the Unassigned row and picks the account. Only the analytic column
changes; no amount, date or entry is touched, and the ledger balance stays the same to the
tiyin. The action is logged in the audit trail and is a no-op the second time.

**Reading it back.** The account card for 5110 (`/accounts/<id>/card`) shows a balance per
bank account and can be filtered to one — the statement of a single расчётный счёт. The
Dashboard cash tile lists the bank accounts under the ledger codes.

> A payment with **no recipient and no allocation** lands on 9430, which is inside the
> overhead pool — so it silently becomes overhead and raises every hourly rate. That may
> be exactly right for a genuine unattributed expense. Make sure it is deliberate.

### Payments that are not supplier settlements

Use the **line grid** with an explicit account:

| Paying | Account |
|---|---|
| Net salary to staff | 6710 |
| PIT to the tax office | 6420.1 |
| Social contribution | 6520 |
| A declared dividend | 6610 |
| Loan principal | 6820 / 7820 |
| Loan interest | 9610 |

For payroll and loans the app builds these lines for you — see [§16](#16-payroll) and
[§17](#17-loans). Enter them by hand only for one-off cases.

---

## 16. Payroll

**Where:** sidebar → Documents → **Payroll** (`/payroll`)

Payroll is a three-step monthly cycle: **build the sheet → accrue it → pay it out**.
Accrual records what is owed; remittance records the money leaving.

### Step 1 — Build the sheet

Pick the month at the top and press **Filter**. The app proposes one row per employee who
was employed during that month, using the salary in force on the **last day** of the
month:

| Column | How it is calculated |
|---|---|
| **Gross** | Base salary + premium, from `/staff` |
| **PIT** | Gross × `tax_rate` (12% by default) |
| **Social** | Gross × `social_rate` (12% by default) |
| **Net** | Gross − PIT (calculated, not editable) |

Every figure is editable. A mid-month hire, unpaid leave, a one-off bonus — correct the
row directly. The app proposes; it does not insist.

Five tiles above the grid: Gross salary, PIT, Social, Net pay, and Total employer cost
(gross + social).

### Step 2 — Accrue

Two buttons:

- **Save** — stores the sheet as a draft. Nothing is posted. Use it if you are still
  waiting on numbers.
- **Accrue payroll** (Hisoblash) — saves *and* posts.

Accrual posts:

```
Dr 2010    Work in progress    gross + social   for each production employee
Dr 9420.2  Admin salary        gross + social   for each admin employee
    Cr 6710   Salary payable      gross            for each employee
Dr 6710    Salary payable      total PIT
    Cr 6420.1 PIT payable         total PIT
    Cr 6520   Social payable       total social
```

Memo: `Ish haqi <period>`. Number format: **ZP-2026-00009**.

Two things to notice. Production salary goes to **2010**, so it becomes project cost.
Admin salary goes to **9420.2** and never to 9420 — because admin salary is already
charged into every production rate through the admin share ([§9](#9-the-double-count-rule)).

After accrual, **6710 holds exactly the net pay**.

### Step 3 — Remit

The **Remittance** card lists the three amounts now owed. Enter the payment **Date** and
**Payment method**, then press **Money out**.

The app creates and posts a single `cash_out` clearing all three:

```
Dr 6710    net pay
Dr 6420.1  PIT
Dr 6520    social
    Cr 5110 / 5010   total
```

### Checking it

The **Payable** card shows the balances of 6710, 6420.1 and 6520. **After a fully settled
month, all three should read zero.** That is the test that payroll was both accrued and
paid correctly. A non-zero balance means something is still owed — or was posted twice.

The **Period history** card lists every payroll month, its status and its gross.

---

## 17. Loans

**Where:** sidebar → Documents → **Loans** (`/loans`)

Two kinds, and the app calls them by their Uzbek names throughout:

- **olgan** — money the firm **borrowed** (a liability)
- **bergan** — money the firm **lent** (an asset)

### Registering a loan

**[+ New]**:

| Field | Notes |
|---|---|
| **Type** | `olgan` (borrowed) or `bergan` (lent) |
| **Counterparty** | The lender or borrower |
| **Amount**, **Currency** | The principal |
| **Interest %** | Annual rate, for reference |
| **Term** | `short` → 6820 (or 5820 if lent); `long` → 7820 |
| **Issue date** | Required |
| **Due date** | Repayment deadline |
| **Payment method** | Which cash account the money moves through |
| **Status** | `ochiq` (open) or `yopilgan` (closed) |
| **Post** | Tick to book the money now |

**Post** is deliberately optional here. A loan agreed today may not be disbursed until
next week; leave it unticked to register the agreement, and book the money separately
when it actually moves.

Booking posts:

```
olgan  (borrowed):   Dr 5110/5010   |  Cr 6820 (short) or 7820 (long)
bergan (lent):       Dr 5820        |  Cr 5110/5010
```

### Recording a repayment

Press the amount button on the loan's row. The dialog asks for:

- **Date** and **Payment method**
- **Amount (principal)** — reduces the loan balance
- **Amount (interest)** — an expense, or income if you are the lender

Splitting them is required, because they go to different places:

| | Principal | Interest |
|---|---|---|
| Loan you took (`olgan`) | Dr 6820 / 7820 | Dr **9610** interest expense |
| Loan you gave (`bergan`) | Cr 5820 | Cr **9530** interest income |

A repayment on a borrowed loan creates a `cash_out`; a repayment received on a loan you
gave creates a `cash_in`.

### The balance

The **Ledger balance** column is read live from the ledger — 6820/7820 for borrowed,
5820 for lent — restricted to the documents booked on *this* loan, and is never stored.
It cannot drift from the accounts, because it *is* the accounts. Two loans with the same
counterparty keep separate balances.

### The page layout

Borrowed (`olgan`) and lent (`bergan`) loans sit in two separate tables. The tiles above
them show, per side, the outstanding total, how many loans are open and closed, any
non-UZS balances, and how many are **overdue** (past their due date with a balance).

Each row shows **Principal paid** and **Interest paid** separately, and a
**Payment history** link that unfolds every document booked on the loan — the
disbursement and each repayment, with its principal/interest split, method and status.
A loan whose money has never been booked is marked *not booked*.

### Auto-close

A loan **closes itself** the moment its principal balance reaches zero, and reopens if a
repayment is later voided. Interest never counts toward closing. The Status field in
the dialog is still there for a manual override (a written-off loan, for instance).

---

## 18. Dividends

**Where:** `/documents/dividend` — type the address; it is not in the sidebar.

A dividend is two separate events, and they are usually on different dates.

**1. Declaration** — the decision to pay:

```
Dr 8710  Retained earnings   |   Cr 6610  Dividends payable
```

Enter it as a dividend document. Number format: **DV-2026-00002**. From this moment the
founder is a creditor of the firm.

**2. Payment** — the money leaving. Enter it as a normal **Money Out**
([§15](#15-money-out)) with one line on account **6610** and the founder as recipient:

```
Dr 6610   |   Cr 5110 / 5010
```

After both, 6610 returns to zero. A non-zero balance on 6610 means a dividend has been
declared but not yet paid — which may be entirely correct.

---

## 19. Manual entries and opening balances

### Manual entry

**Where:** `/journal` → **[+ Manual entry]** (`/documents/manual`)

For anything with no document of its own: a correction, an accrual, a reclassification.

Fill in the date and description, then the grid: **Account**, **Description**, **Debit**,
**Credit**, one line each. Debit and credit totals appear under the grid, and **Save stays
disabled until they are equal.**

Number format: **MJ-2026-00017**.

Use it sparingly. If a routine document type exists for what you are recording, use that
instead — it carries analytics and appears in the right reports.

### Opening balances

**Where:** `/documents/opening` — type the address. Used once, when you start.

If the firm has history before it started using v5, you enter where every account stood
on day one. Each line is an account and its balance, on the correct side.

The trick that makes this manageable is account **0000**, the suspense account. You do
not have to enter every account at once and you do not have to make each entry balance —
the app squares off whatever is missing against 0000:

```
Dr 5110   Bank                  45 000 000
Dr 4010   Receivables          120 000 000
    Cr 6010  Payables               30 000 000
    Cr 8710  Retained earnings     135 000 000
    (0000 absorbs any difference)
```

**When the full set is in and the books genuinely balance, 0000 nets to zero.** That is
the check. Open its account card (`/accounts` → click 0000) — a non-zero balance means
something is still missing or wrong.

Number format: **OB-2026-00001**.

---

## 20. Timesheets

**Where:** sidebar → Staff → **Timesheets** (`/staff/hours`)

Hours worked, by person, by project, by month. This is the link between the hourly cost
and the projects the firm actually did.

### Entering hours

**[+ New]**:

| Field | Notes |
|---|---|
| **Project** | Required |
| **Staff** | Required |
| **Period** | `YYYY-MM`, for example `2026-03`. The format is validated. |
| **Hours** | In steps of 0.5 |

There is one row per person, per project, per month. Entering the same combination again
**overwrites** the existing figure rather than adding to it — so enter the month's total,
not each day.

The period selector at the top filters the list and submits automatically.

### Importing the NIZAM timesheet

**[⇧ NIZAM import]** at the top of the page takes the monthly `table.xlsx` that the NIZAM
CRM exports and writes one month of hours in one go.

| Field | Notes |
|---|---|
| **File** | The NIZAM export. Row 2 holds employee names from column D; each data row is one project with its hours per person in `HH:MM`. |
| **Period** | The `YYYY-MM` the sheet covers. Every hour in the file lands on this month. |
| **Create unknown projects** | Ticked: a project name not in the register is created. Unticked: it is reported and queued instead. |

Rules, all deliberate:

- **Employees are matched, never created.** A column header is matched against the
  staff register's `nizam_name`, then the name or full name (case-insensitive), then the
  legacy NIZAM map. A header that matches nothing is listed in a warning; add or rename
  the employee on the Staff page, then import again. A match writes `nizam_name` back
  so the next import is exact.
- **Non-billable rows** (Office, HR, internal tooling…) and rows with no hours are
  skipped, and the confirmation lists every skipped row with its reason.
- **Re-importing the same month updates the hours in place.** A milestone tag or a
  rate snapshot already on the row is kept.
- Existing projects are matched through their aliases and a normalised name, so a
  project renamed in NIZAM does not fork into two.

### Why hours matter

Hours feed four things:

1. **Project cost** — hours × that person's cost rate.
2. **Utilization** — billable hours against available hours, per person, on `/rates`.
3. **The allocation base** — if the firm uses the hours basis rather than labour cost,
   these hours decide who absorbs what share of overhead.
4. **Project completion and earned revenue** — actual hours against estimated hours.

### The applied rate columns

The list shows **Applied cost rate** and **Total** alongside the hours. These are blank
for an open month and fill in when the period is closed or a snapshot is taken: the rate
in force at that time is stamped onto the row and never recalculated afterwards. That is
what keeps closed history from moving when salaries change later.

---

# Part IV — Staff, cost and pricing

## 21. Staff register and salary history

**Where:** sidebar → Staff → **Staff** (`/staff`)

### The one field that matters most

**Staff type** decides how a person's cost is treated, and it changes every number
downstream:

| Type | Label | What happens to their cost |
|---|---|---|
| `production` | Production (Ishlab chiqarish) | They bill hours to projects. Their cost becomes an hourly rate. Their salary posts to **2010**. |
| `admin` | Admin (Ma'muriy) | They do not bill hours. Their entire cost is redistributed across the production staff as *admin share*. Their salary posts to **9420.2**. |

Architects, BIM engineers and visualizers are production. HR, project management,
IT and finance are admin. If you are unsure, ask whether their time is ever charged to a
client — if not, they are admin.

Getting this wrong is not a small error. Marking an admin employee as production adds a
person with no billable hours to the rate table and removes their salary from everyone
else's admin share.

### Finding people

The filter bar narrows the register by **department**, **type** (production / admin),
**status** (active / archived) and a free-text search over name, full name and role.

### Adding an employee

**[+ New]**:

| Field | Notes |
|---|---|
| **Name** | Required. Short working name. |
| **Full name** | Legal name. |
| **Role** | Required. A datalist of the roles you have used. |
| **Department** | Required. Same. |
| **Staff type** | Production or admin — see above. |
| **Hire date** | Used to decide who appears on a payroll sheet. |
| **Termination date** | Leave blank while employed. |
| **Active** | Untick to archive. History is preserved. |

A **staff code** is assigned automatically: `MZ-###` for production, `MA-###` for admin.

### Salary history

Salaries are a timeline, not a single field. **[+ Base salary]**:

| Field | Notes |
|---|---|
| **Staff** | Required |
| **Effective from** | Required. The date the new salary starts. |
| **Base salary** | Monthly gross base |
| **Premium** | Regular monthly premium on top |

Adding a salary **automatically closes the previous one** the day before the new start
date. You never edit the old row — you add the new one and the app maintains the timeline.

That timeline is why a payroll sheet for March uses March's salary even if you run it in
August, and why closing a period freezes the right rates.

### The rate columns

The list shows each production employee's **Cost rate** and **Billing rate**. An employee
with no salary row shows *No salary set* and a link to fix it — not a zero. A zero would
silently understate every project that person has touched.

---

### Staff KPI

**Where:** sidebar → Staff → **KPI** (`/kpi`)

One row per production employee, sorted by hours: how many projects they have booked
time to, their lifetime billable hours, their cost and billing rate, and two derived
values — **cost value** (hours × cost rate) and **revenue value** (hours × billing rate,
also in USD). **Utilisation** is those hours over the KPI window, which is the
`kpi_months` setting times the available hours per month; the bar turns amber under
60% and red under 35%. The star rating is a five-band scale on utilisation.

This page is a report. Nothing on it feeds a rate.

---

## 22. Equipment, asset classes, licenses, overhead

**Where:** sidebar → Staff → **Equipment** (`/staff/equipment`)

Four registers on one page. All four feed the hourly cost.

### Equipment

Computers, monitors, furniture, vehicles, machinery — anything depreciated.

| Field | Notes |
|---|---|
| **Name** | Required |
| **Asset class** | What the thing is. Sets the useful life and the ledger accounts. |
| **Type** | `personal` or `general` — see below |
| **Staff** | Who it belongs to, for personal assets |
| **Count** | Number of units |
| **Amount** | Price per unit |
| **Lifespan (months)** | Leave blank to take the class default |
| **Purchase date** | **Fill this in.** Without it the asset is skipped by depreciation. |
| **Active** | Untick when retired or sold |

Monthly cost is `count × price ÷ lifespan months`.

The filter bar narrows the register by kind (personal / general), asset class, employee,
license type and name; the licenses table below follows the employee and license-type
filters too.

### Two columns that look similar and are not

This is the distinction to keep straight:

- **Type** (`kind`) answers **who bears the cost**. A `personal` asset is charged to one
  named employee's rate. A `general` asset is spread across all production staff.
- **Asset class** answers **what the thing is**. It decides the useful life and which pair
  of ledger accounts the depreciation uses.

They are independent. A desk and a laptop can both be `personal`; they are different
classes. A shared plotter and a shared meeting table are both `general`; again different
classes.

### The asset classes

| Class | Asset account | Accumulated depreciation | Default life |
|---|---|---|---|
| Computer equipment | 0150 | 0250 | 36 months |
| Furniture & office equipment | 0140 | 0240 | 36 months |
| Machinery & equipment | 0130 | 0230 | 36 months |
| Vehicles | 0160 | 0260 | 36 months |
| Buildings & structures | 0120.1 | 0220.1 | 36 months |
| Other fixed assets | 0190 | 0290 | 36 months |

Every class ships at 36 months. That is the single value the whole v4 register used, so
introducing classes changed no number when the firm migrated. Setting realistic lives is
a decision for the firm to make.

For reference, the Tax Code (Art. 306 §30) caps depreciation at 20%/year for computers
(60 months) and 15%/year for furniture (80 months). 36 months is 33.3%/year, above both.
Those are *tax* ceilings; under НСБУ №5 the book life is the firm's own estimate, which is
why it is a setting here.

**Changing a class default is admin work**, in the Asset classes card: edit the
**Default life (months)** box and press **Save**.

> **A default is only a default.** Changing it affects assets you enter *afterwards*.
> It never re-lives an asset already recorded — doing so would move depreciation already
> posted and every rate quoted from it. Rows whose life differs from their class default
> are badged **Off default**, so the divergence is visible rather than hidden.

### Reclassify

The **Reclassify** button guesses each asset's class from its name using keyword rules,
and it is careful in two ways: it only touches rows still sitting on *Other*, so a
correction you made by hand is never overwritten; and it shows you what it would do.

It reads machine names before accessory names, because a laptop's register entry
routinely lists its monitor.

### Licenses

Revit, 3ds Max, AutoCAD — anything with an annual fee.

| Field | Notes |
|---|---|
| **Name** | Required |
| **Staff** | Who it is assigned to |
| **Annual cost** | Monthly cost is this ÷ 12 |
| **Active** | Untick when expired |

### Overhead budget

The **fallback** source of overhead, used only when the ledger cannot supply it. Simple
line items: office rent, utilities, internet, meals, transport, other.

> The app ships this table **pre-filled with demo figures** (office rent 25,000,000 and so
> on). If your rates are being built from this table rather than from the ledger, they are
> being built from example numbers. `/rates` warns when this happens. See
> [§24](#24-overhead-reconciliation).

The **[Ledger]** button opens the reconciliation page comparing the two sources.

---

## 23. Hourly Cost

**Where:** sidebar → Staff → **Hourly Cost** (`/rates`)

The central output of the whole app.

### The tiles

| Tile | What it means | Healthy range |
|---|---|---|
| **Overhead rate** | Indirect cost as a percentage of direct labour | 150–180% |
| **Utilization** | Billable hours ÷ available hours | 75–85% |
| **Available hours** | Working hours per person per month | ~151 |
| **Avg cost rate** | Average cost per hour, with the billing rate beneath | — |
| **Total monthly cost** | Full monthly cost of the priced staff | — |

The three benchmark bands come from architecture and engineering practice (AIA/PSMJ,
FAR Part 31 / AASHTO). They are **reported only** — the app never feeds them back into
the arithmetic. They tell you whether your firm looks normal, not what to charge.

### Reading the table

| Column | What it is |
|---|---|
| **Staff code** | `MZ-###` |
| **Gross salary** | Base + premium |
| **Tax + Social** | Employer burden on the gross |
| **Admin share** | Their slice of all administrative salaries |
| **Overhead share** | Their slice of the indirect pool |
| **Equipment share** | Personal equipment + licenses + their slice of general equipment |
| **Total monthly cost** | The sum of everything above |
| **Billable hours** | From the timesheets |
| **Utilization** | Billable ÷ available, colour-banded |
| **Overhead rate** | Their indirect cost as a % of their direct labour |
| **Cost rate** | **The number.** Total monthly cost ÷ available hours |
| **Billing rate** | Cost rate × markup |
| **Net multiplier** | Billing rate ÷ raw labour rate |

A **Print** button gives a clean version for a meeting.

The **Billing rate $** column is the billing rate at the current USD rate, with the cost
rate in dollars underneath — the figure a foreign client is quoted.

### The two switches

The **Calculation basis** card holds the settings that change *how* the rate is built.
Both are **admin-level**.

**Allocation base** — how shared costs are divided:

| Option | Meaning | When to use |
|---|---|---|
| **By labor cost** (default) | A person's share is proportional to their salary cost | The standard basis. A senior hour genuinely absorbs more overhead than a junior one. |
| **By hours** | Share is proportional to billable hours | v4's behaviour. Choose it to reproduce v4's numbers exactly. |

**Overhead source** — where the overhead figure comes from:

| Option | Meaning | When to use |
|---|---|---|
| **Ledger (actual)** (default) | A trailing average of what was actually posted to indirect accounts | Once you have a few months of real postings |
| **Budget table** | The hand-maintained table on `/staff/equipment` | Only when the ledger has no history yet |

Also on this card: **Markup** (2.0), **Holidays** per year (14), average **leave** days
(20), and the **overhead window** in months (12).

### The Period snapshot button

**[Period Save]** freezes the selected month's rates into permanent storage. Press it
before closing a month — although closing does it for you.

The important detail: a snapshot costs the period **as of that period's last day**, not
as of today. Snapshotting March in August uses March's salaries, March's overhead window,
and the assets alive in March.

### The two warnings

**Overhead fallback:**

> *Rates are being built from the budget table*

The app wanted the ledger and could not use it. The reason is shown alongside — see
[§24](#24-overhead-reconciliation). Your rates are currently built from a hand-maintained
table that ships with demo numbers. Treat this as urgent.

**Uncapitalized equipment:**

> *Uncapitalized equipment (not on 0150)* — *These assets were expensed to 9420, so the
> register charges them to rates a second time*

Equipment was bought through a purchase invoice and expensed to 9420 instead of being
capitalized to an asset account, while the equipment register also depreciates it. The
cost is in the rates twice. See [§14](#14-purchase-invoice) for the fix.

---

## 24. Overhead reconciliation

**Where:** `/rates/overhead` — the **[Ledger]** button on `/staff/equipment`, or the link
on `/rates`

One job: show what the overhead figure *is*, where it came from, and — if it did not come
from where you asked — why.

### The three tiles

- **Ledger source** — the monthly average from the ledger, with the window used and how
  many months of history actually exist
- **Budget source** — the total of the static table
- **Variance** — the difference

The active source is highlighted. Below, a breakdown of the ledger pool by account, so you
can see exactly which accounts are contributing.

### How the ledger figure is built

Every account flagged **Indirect** (9410, 9420, 9430 by default) is summed over a trailing
window — debits net of credits — and divided by the months that have actually elapsed.
Netting credits against debits is why a refund correctly reduces overhead.

### The reason codes

| Reason | Meaning | What to do |
|---|---|---|
| `ledger` | Working normally | Nothing |
| `disabled` | You chose the budget table | Nothing, if deliberate |
| `ledger_empty` | *No indirect cost has been posted to the ledger* | Normal on a new install. Post some real expenses. |
| `ledger_stale` | *Every posting predates the window — widen it* | Increase the overhead window on `/rates`. |
| `pool_negative` | *Pool is negative: income may be misfiled to 9430* | Find it — see below. |

### A negative pool

A negative overhead pool is never a real answer, so the app refuses it and falls back.
The cause is almost always income booked to **9430** — usually a receipt entered with no
counterparty, which the app has to put somewhere ([§7](#7-settlement-is-allocation-not-a-checkbox)).

To find it: open the account card for 9430 (`/accounts` → click 9430) and look for credit
entries. Void the offending document and re-enter it with the counterparty filled in.

---

## 25. Projects and the price ladder

**Where:** sidebar → Projects → **Projects** (`/projects`), then click a project name

### Creating a project

**[+ New]**: Name (required), Code, Client, Responsible, Contract amount, Currency, Start
and End date, Planned hours, Status (`active` / `completed` / `paused`), Notes, and a
**Billable** checkbox. Untick Billable for internal work.

### The project list

Each row shows lifetime **hours**, the number of **people** who booked them, milestones
done / total with a red count of **late** ones, and the plan status. The list is sorted by
hours by default; the **Sort by** selector offers name, status, contract and start date.

### The project detail page

Eight tiles: **Contract amount**, **Revenue invoiced** (with outstanding), **Cost** (with
hours), **Profit** (with margin %), **Completion** (with earned revenue), **Earned value**
(the sum of every milestone's planned revenue × its completion), **FX gain / loss** on
the project's foreign-currency invoices, and **On course** — the traffic light explained
under *Monthly plan vs fact* below.

Below them, the **Plan & Price** card — where the project is actually planned and priced.

### Risk factors

Four factors, weighted, saved on the project:

| Factor | Low = 1 | Medium = 2 | High = 3 | Weight |
|---|---|---|---|---|
| **Deadline** | more than 8 months | 4–8 months | under 4 months | 30% |
| **Client type** | Regular | New | Government | 25% |
| **Complexity** | Simple | Medium | High | 25% |
| **Currency** | UZS | USD | Other | 20% |

They produce a risk coefficient:

```
risk coefficient = 1 + (weighted score - 1) x 0.15
```

**Risk raises the price. It never touches the cost.** Planned cost is risk-exclusive, so
plan and actual compare like with like.

### Milestones are the plan

The milestone schedule *is* the project plan. There is no separate planning screen.

**[+ Milestone]** or **[+ Milestones (6)]** — the latter generates the six standard design
stages as an empty skeleton, names and work types only.

Each milestone carries:

| Field | Notes |
|---|---|
| **Name** | Required |
| **Code**, **Order** | A short code (unique within the project) and the sort position in the table |
| **Work type**, **Start**, **End date** | Schedule |
| **Staff hours grid** | Who works on it and for how many hours. The dialog shows each person's live cost rate. |
| **Planned outsourcing**, **Planned materials** | Direct expenses |
| **Planned revenue** | The price. Auto-suggested, overridable. |
| **Completion %**, **Status** | `planned` / `in progress` / `done` / `cancelled` |

`Planned hours` and `planned cost` are derived from the staff grid: hours × that person's
cost rate.

### The price ladder

Applied identically to a single milestone and to the project total:

```
minimum = labour cost x risk coefficient + outsourcing + materials
target  = minimum / (1 - target margin)          <- target margin 50% by default
premium = target x 1.2
```

- **Minimum price** — below this the project loses money
- **Target price** — the price that actually delivers the target margin
- **Premium price** — the ask when you have room

The ladder is linear in its inputs, which means the sum of the per-milestone target prices
always equals the target price of the summed costs, exactly. Milestone figures and project
totals can never disagree.

**[Apply suggested prices]** writes the suggested target into each milestone's Planned
revenue — but leaves alone any price you have edited by hand, unless you force it. An
edited price is flagged. A price of zero means "not priced yet", not an override.
Cancelled milestones are excluded from both the price and the roll-up.

### Freezing the plan

**[Freeze plan]** copies the current live plan onto the project as a permanent baseline,
stamped with the date. That baseline is what the Budget page compares actuals against.

Two tiers exist on purpose:

- The **live plan** on the milestones recalculates every time you edit anything.
- The **frozen baseline** on the project changes only when you press Freeze.

Without the split, the goalpost would move every time somebody adjusted an estimate, and
"are we over budget?" would be unanswerable.

Freezing is refused on a project with no milestones, so a baseline can never be zeroed by
accident. When the live plan drifts away from the frozen baseline, a **Plan has drifted —
re-freeze** badge appears.

---

### Monthly plan vs fact

Under the Plan & Price card, the **Monthly plan / fact** table spreads every dated
milestone's plan across the months it covers (day-weighted) and sets it against what
actually happened in each month: revenue posted to 9030, direct cost posted to 2010,
and timesheet hours priced at the cost rate. Each row carries plan and fact income,
expense and profit, the monthly variance, and both cumulative profits. The current
month is highlighted and never judged — income lands late in a month — and a month
with actuals but no plan is marked *unplanned*.

Above the table, three to-date figures cover the months strictly before the current
one: **profit drift** (fact profit minus plan profit, as a % of planned income),
**collections** (fact income over planned income) and **expense variance**.

They drive the **On course** traffic light on the tile:

| Light | Green | Amber | Red |
|---|---|---|---|
| **Profit** | drift ≥ −5 % | −15 … −5 % | below −15 % |
| **Schedule** | no late milestone | a milestone more than 15 points behind its expected completion | a milestone past its end date and not done |
| **Collections** | ≥ 95 % of planned income received | 85–95 % | under 85 % |

The headline is red if any light is red, amber if any is amber, grey when there is no
dated plan to judge against. Milestones with a plan but no dates are named in a
warning — they cannot be spread across months.

### Actuals not assigned to a milestone

Hours and posted invoices that carry the project but no milestone are listed under
the monthly table. For each timesheet month the milestone that overlaps it most is
pre-selected; press **Assign** to tag the whole month. A posted invoice can be tagged
the same way — this changes only the milestone dimension on the document and its
postings, never an amount, date or account. Milestone actuals (and the CPI / SPI
figures) see the money and hours once they are tagged; the monthly table does not
need tagging at all.

### Scratch quote for a prospect

**Where:** sidebar → Projects → **Pricing** (`/pricing`)

A prospect with no project record yet can still be priced. Fill the schedule grid —
milestone name, work type, months, per-person hours, outsourcing and materials — set
the four risk factors, and press **Calculate**: every row is priced through the same
ladder as the project card and the three contract prices appear above the grid. The
grid recalculates as you type. **Standard schedule (6)** fills the six design stages.

Nothing is stored until you choose a project in the selector and press **Save into
project**: the rows become that project's milestones, employee hours and all, and the
risk factors are written to it. A project that already has milestones is refused
unless *Replace existing milestones* is ticked, and never if documents are attached to
those milestones. The project table underneath is the way into the full Plan & Price
workspace for projects that already exist.

---

## 26. Budget

**Where:** sidebar → Projects → **Budget** (`/budget`)

Every project's frozen plan against its live actuals, on one page.

Four tiles: **Plan total**, **Fact total**, **Variance** (absolute and %), and the count of
projects with **no plan**.

Per project: planned and actual hours, planned and actual cost, plan and fact totals,
variance, revenue and profit. Status badges:

| Badge | Meaning |
|---|---|
| **Over budget** | More than 5% above plan |
| **Under budget** | More than 10% below plan |
| **On budget** | Between the two |
| **No plan** | Never frozen |

**A project with no frozen plan is excluded from the plan-side totals** rather than having
a plan invented from its actuals. That keeps the portfolio variance honest: comparing
actuals with themselves would always show perfect performance.

FACT is recalculated live from actual hours at current cost rates. PLAN is the frozen
figure and does not move.

### The second lens: to date

The first table answers *will we come in over budget?* The second, **To date — plan /
fact**, answers *are we where we should be by now?* For every project it shows the
day-weighted milestone plan against actuals for the months before the current one,
the profit drift and collections ratio, the traffic-light verdict with its three
lights, and the months the plan covers. Two more tiles at the top count the projects
on / at the edge / off course and total the to-date profit against plan.

The two lenses measure different things and are never combined into one number. A
project can be inside its whole-project budget and still be behind this month.

---

# Part V — Month-end and reports

## 27. The month-end checklist

Work through this in order once a month. The order is not arbitrary — several steps
depend on the ones before them, and one of them (depreciation) produces the wrong result
if done after the close.

Closing steps are **admin-level**.

---

**☐ 1. Post every document for the month**

Nothing should be left as a draft. The Dashboard shows a draft count; each document list
can be filtered by **Status → Draft**.

Once the period is closed, a document dated inside it can no longer be posted at all.

---

**☐ 2. Enter the month-end exchange rate**

`/settings` → **Exchange rate** card → the date and the rate → **Save**.

The FX revaluation in step 4 uses the most recent rate on or before the date you give it.
Without a current rate it will revalue against a stale one.

Skip this if the firm has no foreign-currency balances.

---

**☐ 3. Run payroll**

`/payroll` → select the month → **Filter**.

1. Check every row. Correct any mid-month hire, unpaid leave or one-off bonus.
2. **Accrue payroll**.
3. In the **Remittance** card, enter the payment date and method, then **Money out**.
4. Confirm the **Payable** card now shows **6710, 6420.1 and 6520 all at zero**.

Those three zeros are the proof that payroll was both accrued and paid. See
[§16](#16-payroll).

---

**☐ 4. Post the FX revaluation**

`/periods` → **FX revaluation** card → set the date → **FX revaluation**.

This restates every foreign-currency balance at the current rate, booking the difference
to 9540 (gain) or 9620 (loss). Skip if there are no foreign-currency balances.

---

**☐ 5. Review the depreciation preview**

`/periods` → **Depreciation** card → select the month.

The preview shows exactly what will post: each asset, its months used, its charge, and the
resulting book value. **Nothing is written yet.**

Look at the **Skipped assets** table underneath. Every skipped asset has a reason:

| Reason | Meaning | Fix |
|---|---|---|
| *No purchase date* | The register row has no date | **Fix this one.** Add the date on `/staff/equipment` |
| *Invalid purchase date* | Unreadable date | Correct it |
| *Not yet purchased* | Bought after this month | Nothing — correct behaviour |
| *Fully depreciated* | Life already exhausted | Nothing — correct behaviour |
| *No lifespan set* | Blank lifespan | Set one, or leave blank for the class default |
| *Zero cost* | Price is zero | Enter the price |

*No purchase date* matters more than the others: the rate engine charges such an asset to
the hourly rates **forever**, while the ledger never depreciates it. Fix them here and the
register and the ledger stay in step.

Then press **Post depreciation**.

```
Dr 9420.1  Depreciation expense    (per employee for personal assets, one line for general)
    Cr 0250 / 0240 / 0230 / ...      (one line per asset class)
```

Running it twice is harmless — the app detects that the month is already posted and does
nothing.

---

**☐ 6. Take the rate snapshot**

`/rates` → **[Period Save]**.

This freezes the month's hourly rates permanently, costed as of the month's last day. If
you skip it, closing does it for you — but doing it here lets you look at the figures
first.

---

**☐ 7. Check the books balance**

`/trial-balance` — the banner at the top should read:

> *Balanced: debit = credit*

`/reports/balance-sheet` — the banner should read:

> *Balanced: assets = liabilities + equity*

If either shows a warning, **stop and investigate before closing**. See
[§38](#38-when-two-numbers-do-not-match).

---

**☐ 8. Read the month's results**

`/reports/pnl` for the month, and `/reports/cashflow`. This is the point to notice
anything odd — after closing, corrections require reopening.

---

**☐ 9. Close the period**

`/periods` → the month's row → **Close period** → confirm.

Closing will:

1. Post depreciation if step 5 was skipped
2. Read the P&L
3. Freeze the month's rates and stamp them on the timesheet rows
4. Post the closing entry — every income and expense account emptied into **9910**
5. Lock the period

You can close **soft** (reversible in intent) or **hard** (the normal final state). Both
refuse new postings identically.

---

## 28. The reports

All reports are read-only and available to every user.

### Profit & Loss — `/reports/pnl`

Income and expense for a date range, defaulting to year-to-date. Income accounts grouped
and totalled, expense accounts grouped and totalled, then gross profit, net profit and the
target margin.

*If it looks wrong:* check that the period's documents are all posted, not draft — a draft
appears in no report.

### Balance Sheet — `/reports/balance-sheet`

Assets on one side, liabilities and equity on the other, as of a date. Contra accounts
(accumulated depreciation) appear in parentheses as deductions. A banner confirms it
balances.

*If it does not balance:* something is wrong at the ledger level, not in this report.
Go to `/trial-balance` first.

### Cash Flow — `/reports/cashflow`

Money in, money out, net and running balance by period, with a tile for each cash account
and a closing balance tile.

*If it looks wrong:* the closing balance must equal the sum of the 5xxx account balances
and the Cash tile on the Dashboard. If those three disagree, see
[§38](#38-when-two-numbers-do-not-match).

Below the monthly table, **By payment method** splits the same period's cash in and
out by the method on the document — bank transfer, cash, card, online — read off the
money accounts, so its totals equal the cash-flow totals above it.

### VAT Report — `/reports/vat`

Output VAT (6410.1) against input VAT (4410) for a date range, with the difference as VAT
payable. Both sections list the underlying documents with counterparty and ИНН.

*If a document is missing:* it has no VAT amount on its lines, or it is still a draft.

### AR / AP Aging — `/reports/aging`

Toggle **Receivable** / **Payable**. Outstanding amounts bucketed by age: 0–30, 31–60,
61–90, 90+ days. Anything past 60 days is flagged overdue.

Two tables: totals by counterparty, and the individual documents with due dates and days
outstanding.

*If an invoice you have been paid for still appears:* the payment exists but was never
**allocated** to that invoice. Open the payment and allocate it
([§13](#13-money-in)).

### Journal — `/journal`

Every posting, newest first, grouped by entry. Filter by date range, account, project or
counterparty. Reversals are badged. Each account code links to its card.

This is where you go to answer "what actually happened".

### Trial Balance — `/trial-balance`

Opening balance, turnover and closing balance for every account, each split debit and
credit. The banner tells you whether the books balance. Tick **all** to include
zero-balance accounts. There is a **Print** button.

### Account ledger — `/accounts/<id>/card`

Every movement on one account with a running balance, plus four tiles: opening, debit,
credit, closing. Reached by clicking any account code anywhere in the app.

This is the tool for chasing down a specific wrong number.

---

## 29. Reopening a period

**Where:** `/periods` → the closed month's row → **Reopen period**. **Admin only.**

Reopening sets the period back to open and reverses two things:

1. The **closing entry** — income and expense balances return to their accounts, out of
   9910
2. The **depreciation entry** for that month

Depreciation is reversed deliberately. If it were left in place, re-closing would skip it
as already posted, and any correction you made to the equipment register during the
reopening would be silently ignored.

A **hard-closed** period is refused here — only a soft-closed one reopens. See
[§10](#10-periods) for the difference.

### After reopening

1. Make the corrections you reopened for.
2. **Re-close the period** — work through [§27](#27-the-month-end-checklist) again from
   step 5.

A reopened period that is never re-closed leaves the year's figures wrong: the month's
income and expense sit unclosed in their own accounts instead of in the financial result.

---

# Part VI — Administration

## 30. Settings

**Where:** sidebar → Admin → **Settings** (`/settings`). Saving is **admin only**.

Every setting is a number. Each shows its label, its unit and its internal key.

| Key | Default | What it does | Change it? |
|---|---|---|---|
| `usd_rate` | 12850 | Reference USD rate | Rarely — the `exchange_rates` table is the real source |
| `tax_rate` | 0.12 | PIT (JSHDS) rate | Only if the law changes |
| `social_rate` | 0.12 | Employer social contribution rate | Only if the law changes |
| `vat_rate` | 12 | Default VAT % on new invoice lines | **Set to 0 if the firm is on the simplified regime** |
| `billing_multiplier` | 2.0 | Markup: billing rate ÷ cost rate | A firm-level pricing decision |
| `target_margin` | 0.5 | Target margin in the price ladder | A firm-level pricing decision |
| `holidays_per_year` | 14 | Public holidays, for available hours | When the holiday calendar changes |
| `avg_leave_days` | 20 | Average leave + sickness per year | When your real average changes |
| `utilization_rate` | 0.75 | Target utilization, for reference | Rarely |
| `kpi_months` | 43 | KPI / capacity window in months | Rarely |
| `overhead_from_ledger` | 1 | 1 = ledger, 0 = budget table | Use the switch on `/rates` instead |
| `overhead_window_months` | 12 | Trailing window for the overhead average | Widen it if you get *ledger_stale* |
| `allocation_base_labor_cost` | 1 | 1 = labour cost, 0 = hours | Use the switch on `/rates` instead |
| `default_vat_on_sales` | 1 | Pre-fill VAT on new sales invoice lines | If most sales are exempt |

> **`vat_rate` is the one to check on a new install.** The app assumes the firm is
> VAT-registered at 12%. If it is on the simplified regime (упрощённый режим), set this to
> 0 and no VAT will be added to new lines. Nothing else needs to change.

Changing `billing_multiplier`, `holidays_per_year` or `avg_leave_days` changes every
hourly rate in the firm immediately. Closed periods keep their frozen rates and are
unaffected.

---

## 31. Users and roles

**Where:** `/settings` → **Users** card. **Admin only.**

**[+ New]** or the pencil:

| Field | Notes |
|---|---|
| **Username** | Required |
| **Password** | **Leave blank when editing to keep the current password.** Type a new one to change it. |
| **Role** | `viewer`, `manager` or `admin` — see [§3](#3-logging-in-language-and-who-can-do-what) |
| **Active** | Untick to disable the account |

Passwords are stored hashed and cannot be read back by anyone, including an admin. There
is no self-service reset — an admin sets a new one.

**When someone leaves, untick Active rather than deleting the user.** Deleting would
orphan the audit trail that records what they did.

---

You cannot deactivate or demote **your own** account; the app refuses and says so. Ask
another administrator.

---

## 32. Chart of accounts and the account map

**Where:** sidebar → Admin → **Chart of Accounts** (`/accounts`). Editing is **admin only.**

The list shows every account with its code, names, kind, analytic, cost pool, live balance
and active flag. Click any code to open its ledger card.

### Editing an account

| Field | Notes |
|---|---|
| **Code** | Set at creation, not editable afterwards |
| **Name RU** / **Name UZ** | Display names |
| **Kind** | A / KA / P / T — see [§6](#6-the-chart-of-accounts-without-the-theory) |
| **Analytic** | Which dimension the account expects: counterparty, project, staff, bank account |
| **Cost pool** | **Read the warning below** |
| **Active** | Untick to hide from dropdowns |

### The cost pool field

| Value | Meaning |
|---|---|
| *(empty)* | Not part of the rate calculation |
| **Direct labor** | Production labour accumulating on projects |
| **Indirect (overhead)** | Goes into the overhead pool and is spread across production staff |
| **Excluded** | A real cost, already charged through a register — do not count it again |

> **Do not set 9420.1, 9420.2 or 9420.3 to Indirect.** Those costs already reach the
> hourly rates through the equipment, staff and license registers. Flagging them Indirect
> charges them twice and inflates every rate in the firm. The app refuses the resulting
> postings and re-corrects the flag on restart, but rates on screen will be wrong in the
> meantime. Full explanation: [§9](#9-the-double-count-rule).

### The account map

The second card, **Posting → Account**, maps each posting *purpose* to a real account:
"the receivable account", "the bank account", "the revenue account", and about thirty
more.

Nothing in the app has an account code written into its logic — every rule asks for a
purpose and looks it up here. To send all bank movements to a different account, change it
here once and every future posting follows. Existing postings are unaffected.

---

## 33. Lookup tables

**Where:** `/settings` → **Lookups** card. **Admin only.**

Four dropdown lists you can extend:

| Table | Fills the dropdown for |
|---|---|
| `payment_types` | Payment method on cash documents |
| `work_types` | Work type on milestones |
| `departments` | Department on a staff record |
| `staff_roles` | Role on a staff record |

Each row has a **Code** (the stored value), labels in all three languages, a sort order and
an active flag. Deactivate rather than delete — existing records referencing a deleted
code would lose their label.

---

## 34. Exchange rates

**Where:** `/settings` → **Exchange rate** card. **Manager level.**

Enter a **Date** and a **Rate** (so'm per 1 USD) and press Save. Entering an existing date
updates it. The card shows the current rate and a history of the last 40.

**[Fetch CBU rate]** next to the date box asks the Central Bank of Uzbekistan for the
official USD rate on that date and fills the rate box; you still press **Save**. If
the app cannot reach `cbu.uz` the box stays empty and a note says so — type the rate by
hand. The rate history below the form is complete, newest first.

### How a rate is chosen

Whenever the app needs a rate for a date, it takes **the most recent rate on or before
that date**. So you do not need a rate for every day — enter them when they change, and
every date in between uses the last one entered.

The practical consequence: **enter a rate for the last day of each month before running
the FX revaluation** ([§27](#27-the-month-end-checklist), step 2). Without it the
revaluation uses whatever rate was last entered, which may be weeks old.

---

## 35. One-time migration from v4

**This is an installation task, not a user task.** There is no button for it in the app —
it runs from the command line, once, when the firm switches over.

### Running it

From the `mizan5` directory, with the app stopped:

```bash
python -m models.import_v4 ../mizan_finance.db
```

The path argument is the v4 database. If omitted, it looks for `../mizan_finance.db`.

The v4 database is **only ever read**, never modified. v4 keeps working afterwards.

### What it does

It copies the reference data — settings, exchange rates, staff, salary history, projects,
milestones, timesheets, equipment, licenses, overhead — and then **replays every v4
transaction as a real v5 document with a real posting**, in date order.

Because the entire history is replayed, no opening balance is needed and the two systems'
cash figures must agree by construction.

Equipment is classified on the way in by the keyword classifier, and every guess is
printed for review.

### Reading the report

The command prints a reconciliation. Check four things:

| Line | What it should say |
|---|---|
| `cash_diff` | Effectively zero. v5's cash matches v4's computed cash. |
| `receivable_diff` | Zero. v5's 4010 balance matches v4's outstanding receivables. |
| `trial_balance_ok` | True |
| `unbalanced_entries` | 0 |

It also prints:

- **Skipped rows**, each with a reason. These are always reported, never dropped silently.
  On the real firm database, 2 of 1,028 transactions were skipped, both genuinely empty.
- **Asset class assignments**, per class, with an explicit list of rows that matched no
  rule and need a human decision.

### Afterwards

1. Review the skipped rows and enter anything that mattered by hand.
2. Review the asset classes on `/staff/equipment` and correct the unmatched ones.
3. Check `/trial-balance` balances and `/rates` produces sensible figures.
4. Set the real asset lives, if 36 months everywhere is not what the firm wants
   ([§22](#22-equipment-asset-classes-licenses-overhead)).

Do not run the migration twice into the same database.

---

## 36. Backup and recovery

Everything is in one file: **`mizan5.db`**.

### Backing up

Stop the app (`Ctrl+C`), copy the file somewhere else, restart. That is the whole
procedure.

```bash
copy mizan5.db backups\mizan5-2026-09-04.db
```

Copying while the app is running usually works but is not guaranteed — a copy taken
mid-write can be inconsistent. Stopping takes two seconds; do that.

**Do it monthly at minimum, and always before a period close or the v4 migration.**

### Restoring

Stop the app, replace `mizan5.db` with the backup, restart. Everything returns to the
state of the backup — there is no partial restore.

### Testing on a copy

To try something without risk:

```bash
copy mizan5.db test.db
MIZAN5_DB=test.db PORT=5177 python app.py
```

That runs a second, independent instance against the copy. Nothing you do there touches
the real data.

### What is not in the database

Two files sit beside it and are worth keeping: `.mizan5_secret_key` (deleting it logs
everyone out, nothing worse) and `.env`, if you made one.

---

# Part VII — Troubleshooting and reference

## 37. Error messages

Every message below is what the app actually shows, in English.

### Posting refused

| Message | Cause | Fix |
|---|---|---|
| *Entry does not balance: debit ≠ credit* | Debits and credits differ | Correct the lines. The dialog disables Save until they match, so this normally only appears on an imported or manual posting. |
| *This period is closed — posting refused* | The document's date falls in a closed month | Change the date to an open month, or reopen the period ([§29](#29-reopening-a-period)) |
| *Entry has no lines* | Every line was blank or zero | Enter at least one line with an amount |
| *No account selected* | A line has an amount but no account | Choose an account |
| *Date is missing* | No date on the document | Enter one |
| *Amount cannot be negative* | A negative figure in a line | Use the opposite side (debit vs credit), not a minus sign |
| *A line cannot be both debit and credit* | Both boxes filled on one line | Clear one; split into two lines if you need both |
| *Account mapping not found* | A posting purpose points at no account | `/accounts` → **Posting → Account** — fill in the missing purpose |
| *Choose a bank account — account 5110 is subdivided into bank accounts* | The ledger account has bank accounts registered and the document names none | Pick one in the **Bank account** dropdown ([§15](#15-money-out)) |
| *Bank account currency does not match the document* | A USD bank account on a so'm document, or the reverse | Pick a bank account in the document's currency |
| *The selected bank account does not belong to account …* | A manual line names a bank account registered under another ledger account | Pick a bank account of that ledger account |
| *Bank account is inactive* | The chosen bank account was deactivated on `/bank-accounts` | Choose an active one, or reactivate it |
| *This entry is already reversed* | Voiding an already-voided document | Nothing to do |

### The double-count guard

> *This account is in the indirect pool — the cost is already charged from its register
> and would be counted twice*

An account used for depreciation, admin salary or licenses has been flagged **Indirect**.
Go to `/accounts` and set it back to **Excluded**. See [§9](#9-the-double-count-rule).

### Documents

| Message | Cause | Fix |
|---|---|---|
| *A posted document cannot be edited — void it instead* | You tried to change a posted document | Void it and enter a corrected one ([§5](#5-documents-become-ledger-entries)) |
| *Document has no lines* | Invoice or journal entry with an empty grid | Add at least one line |
| *No counterparty selected* | Invoices require a counterparty | Select one |
| *That document number already exists* | Number collision | Let the app assign the number |
| *Document not found* | Stale link or deleted draft | Return to the list |
| *Document is already posted* | Posting twice | Nothing to do |
| *Document is not posted yet* | An action needing a posted document | Post it first |

### Allocations

| Message | Cause | Fix |
|---|---|---|
| *Allocated more than the payment amount* | Allocations exceed the receipt | Reduce them; watch the live Unallocated figure |
| *Allocated more than the invoice outstanding* | Allocating more than the invoice still owes | Check whether it is already partly settled |
| *Only posted invoices can be allocated against* | The target invoice is a draft | Post the invoice first |

### Depreciation

| Message | Meaning |
|---|---|
| *Already posted for this period* | Nothing to do — running twice is safe |
| *Nothing to depreciate* | No eligible assets. Check the Skipped table. |

### Access

| Message | Meaning |
|---|---|
| **403 — Ruxsat yo'q** | Your role is below the level this action needs ([§3](#3-logging-in-language-and-who-can-do-what)) |
| *Access denied: this app is only available on the local network* | You are connecting from outside the allowed address ranges ([§2](#2-installing-and-running)) |
| *Wrong username or password* | Bad credentials, or a deactivated account |

---

## 38. When two numbers do not match

### The Dashboard says the books do not balance

> *WARNING: debit and credit do not match!*

Go to `/trial-balance` and read the totals. Because the app refuses to post an unbalanced
entry, this is nearly always caused by direct database editing or an interrupted
migration. Restore the most recent good backup ([§36](#36-backup-and-recovery)).

### The balance sheet does not balance

Same cause and same first step: `/trial-balance`. If the trial balance *is* balanced but
the balance sheet is not, an account has the wrong **Kind** — most often a П account
created as А. Check recently added accounts on `/accounts`.

### Cash on the Dashboard disagrees with the Cash Flow report

These three must always be equal: the Dashboard Cash tile, the sum of the 5xxx account
balances, and the closing balance on `/reports/cashflow`. If they are not, check whether
the cash-flow report is filtered to a narrower date range than you think.

### An invoice shows as outstanding but the client has paid

The payment was entered but never **allocated** to that invoice — it is probably sitting
on 6310 as an advance. Open the payment document and allocate it
([§13](#13-money-in)).

### Rates jumped and nobody changed a salary

In order of likelihood:

1. **The overhead source fell back to the budget table.** Check for the warning banner on
   `/rates` and open `/rates/overhead` ([§24](#24-overhead-reconciliation)).
2. **An account's cost pool was changed.** Check 9420.1/.2/.3 are still **Excluded**
   ([§9](#9-the-double-count-rule)).
3. **A large expense was posted to 9420.** It is now spread across everyone. Correct if it
   should have been a project cost or capitalized.
4. **Billable hours changed.** On the hours allocation base, new timesheet entries move
   everyone's share.

### `/rates` warns about uncapitalized equipment

Equipment was expensed to 9420 through a purchase invoice while also sitting in the
equipment register — so its cost is in the rates twice. Fix by pointing such invoice lines
at 0150 (or the right asset account) — see [§14](#14-purchase-invoice).

### An asset shows an "Off default" badge

Its lifespan differs from its class default. That is informational, not an error — the
badge exists so the divergence is visible. Changing a class default never re-lives an
existing asset ([§22](#22-equipment-asset-classes-licenses-overhead)).

### A project shows "Plan has drifted — re-freeze"

The live milestone plan has moved away from the frozen baseline. Decide deliberately:
re-freeze to accept the new plan as the baseline, or leave it to keep measuring against
the original commitment ([§25](#25-projects-and-the-price-ladder)).

### An employee has no cost rate at all

They have no salary row. The app shows *No salary set* rather than a zero, because a zero
would silently understate every project they worked on. Add a salary on `/staff`
([§21](#21-staff-register-and-salary-history)).

### The overhead pool is negative

Income was booked to 9430. Open its account card and look for credits — usually a receipt
entered with no counterparty ([§24](#24-overhead-reconciliation)).

---

## 39. Reference

### Document number prefixes

| Prefix | Document | Example |
|---|---|---|
| `SF` | Sales invoice | SF-2026-00042 |
| `SP` | Purchase invoice | SP-2026-00088 |
| `PK` | Money in | PK-2026-00113 |
| `RS` | Money out | RS-2026-00204 |
| `ZP` | Payroll | ZP-2026-00009 |
| `DV` | Dividend | DV-2026-00002 |
| `ZM` | Loan | ZM-2026-00003 |
| `MJ` | Manual entry | MJ-2026-00017 |
| `OB` | Opening balances | OB-2026-00001 |

### What each document posts

| Document | Debit | Credit |
|---|---|---|
| **Sales invoice** | 4010 total | 9030 net (per line, per project) + 6410.1 VAT |
| **Purchase invoice** | line account (2010 if project-tagged, else 9420) + 4410 VAT | 6010 total |
| **Money in** | 5110 / 5010 / 5210 | 4010 allocated · 6310 advance · 9390 unattributed · line accounts |
| **Money out** | 6010 allocated · 4310 advance · 9430 unattributed · line accounts | 5110 / 5010 / 5210 |
| **Payroll** | 2010 production, 9420.2 admin (gross + social); 6710 (PIT) | 6710 gross · 6420.1 PIT · 6520 social |
| **Dividend** | 8710 | 6610 |
| **Loan (borrowed)** | cash | 6820 short / 7820 long |
| **Loan (lent)** | 5820 | cash |
| **Opening balances** | each balance, with 0000 absorbing the difference | |
| **Manual entry** | your lines, balance enforced | |
| **Depreciation** | 9420.1 | the asset class's accumulated account |
| **FX revaluation** | 9620 loss | 9540 gain |
| **Period close** | each income account | each expense account, net to 9910 |

### Account cheat-sheet

| Code | Kind | Name |
|---|---|---|
| 0000 | Т | Suspense (opening balances only) |
| 0120.1 / 0220.1 | А / КА | Buildings / their depreciation |
| 0130 / 0230 | А / КА | Machinery / their depreciation |
| 0140 / 0240 | А / КА | Furniture and office equipment / their depreciation |
| 0150 / 0250 | А / КА | Computer equipment / their depreciation |
| 0160 / 0260 | А / КА | Vehicles / their depreciation |
| 0190 / 0290 | А / КА | Other fixed assets / their depreciation |
| 0410 | А | Patents, licenses, know-how |
| 2010 | А | Work in progress (direct project cost) |
| 4010 | А | Receivable from customers |
| 4310 | А | Advances issued |
| 4410 | А | Input VAT |
| 5010 | А | Cash till |
| 5110 | А | Bank account |
| 5210 | А | Foreign-currency account |
| 5820 | А | Short-term loans issued |
| 6010 | П | Payable to suppliers |
| 6310 | П | Advances received |
| 6410.1 | П | Output VAT |
| 6420.1 | П | PIT payable (JSHDS) |
| 6430 | П | Profit tax payable |
| 6520 | П | Social contribution payable |
| 6610 | П | Dividends payable |
| 6710 | П | Salary payable |
| 6820 / 7820 | П | Short- / long-term loans received |
| 8710 | П | Retained earnings |
| 9030 | Т | Revenue from work and services |
| 9130 | Т | Cost of work |
| 9390 | Т | Other operating income |
| 9410 | Т | Selling expenses *(overhead pool)* |
| 9420 | Т | Administrative expenses *(overhead pool)* |
| 9420.1 | Т | Depreciation *(excluded — register)* |
| 9420.2 | Т | Administrative salary *(excluded — register)* |
| 9420.3 | Т | Licenses and software *(excluded — register)* |
| 9430 | Т | Other operating expenses *(overhead pool)* |
| 9530 / 9610 | Т | Interest received / paid |
| 9540 / 9620 | Т | FX gain / loss |
| 9910 | Т | Financial result |

### Glossary — English · Русский · O'zbekcha

The app's own labels in all three languages, so a reader of this manual can navigate an
app running in any of them.

| English | Русский | O'zbekcha |
|---|---|---|
| Dashboard | Панель | Boshqaruv paneli |
| Sales Invoices | Счета-фактуры (выданные) | Sotuv hisob-fakturalari |
| Purchase Invoices | Счета-фактуры (полученные) | Xarid hisob-fakturalari |
| Money In | Поступление денег | Pul tushumi |
| Money Out | Списание денег | Pul chiqimi |
| Payroll | Зарплата | Ish haqi |
| Loans | Займы | Qarzlar |
| Journal | Журнал проводок | Jurnal |
| Trial Balance | Оборотно-сальдовая ведомость | Aylanma-saldo qaydnomasi |
| Profit & Loss | Отчёт о прибылях и убытках | Foyda va zarar |
| Balance Sheet | Баланс | Balans |
| VAT Report | Отчёт по НДС | QQS hisoboti |
| Periods | Периоды | Davrlar |
| Hourly Cost | Себестоимость часа | Soatlik tannarx |
| Staff | Сотрудники | Xodimlar |
| Timesheets | Табель | Ish soatlari |
| Equipment | Оборудование | Jihozlar |
| Projects | Проекты | Loyihalar |
| Budget | Бюджет | Byudjet |
| Cash Flow | Движение денег | Pul oqimi |
| AR / AP | Дебиторка / Кредиторка | Debitor/Kreditor |
| Counterparties | Контрагенты | Kontragentlar |
| Chart of Accounts | План счетов | Hisoblar rejasi |
| Settings | Параметры | Parametrlar |
| — | — | — |
| Post | Провести | Provodka qilish |
| Posted | Проведён | Provodka qilingan |
| Draft | Черновик | Qoralama |
| Void | Сторнировать | Storno qilish |
| Entry | Проводка | Provodka |
| Debit | Дебет | Debet |
| Credit | Кредит | Kredit |
| Balance | Сальдо | Qoldiq |
| Opening balance | Сальдо на начало | Boshlang'ich qoldiq |
| Closing balance | Сальдо на конец | Yakuniy qoldiq |
| Turnover | Обороты | Aylanma |
| Account | Счёт | Hisob |
| Asset | Актив | Aktiv |
| Contra-asset | Контрактив | Kontraktiv |
| Liability / Equity | Пассив | Passiv |
| Transactional | Транзакционный | Tranzaksion |
| Cost pool | Группа затрат | Xarajat guruhi |
| Direct labor | Прямой труд | To'g'ridan-to'g'ri ish haqi |
| Indirect (overhead) | Косвенные (накладные) | Bilvosita (overhead) |
| Excluded | Не учитывается | Hisobga olinmaydi |
| — | — | — |
| Counterparty | Контрагент | Kontragent |
| Customer | Покупатель | Xaridor |
| Vendor | Поставщик | Ta'minotchi |
| Payer | Плательщик | To'lovchi |
| Recipient | Получатель | Oluvchi |
| Allocation | Распределение | Taqsimlash |
| Outstanding | Остаток | Qoldiq |
| Unallocated | Не распределено | Taqsimlanmagan |
| Advance | Аванс | Avans |
| Due date | Срок оплаты | To'lov muddati |
| Net amount | Сумма без НДС | QQSsiz summa |
| VAT | НДС | QQS |
| — | — | — |
| Cost rate (h) | Себестоимость часа | Tannarx (soat) |
| Billing rate (h) | Ставка продажи (час) | Sotuv narxi (soat) |
| Available hours | Доступные часы | Mavjud soatlar |
| Billable hours | Оплачиваемые часы | Hisoblangan soatlar |
| Utilization | Загрузка | Bandlik |
| Overhead rate | Ставка накладных | Overhead stavkasi |
| Admin share | Доля АУП | Ma'muriy ulush |
| Overhead share | Доля накладных | Overhead ulush |
| Equipment share | Доля оборудования | Jihozlar ulushi |
| Gross salary | Начислено (gross) | Jami maosh (gross) |
| Base salary | Оклад | Asosiy maosh |
| Premium | Премия | Ustama |
| PIT | НДФЛ | JSHDS |
| Social contribution | Социальный налог | Ijtimoiy soliq |
| Net pay | К выплате | Qo'lga tegadigan |
| Accrue | Начислить | Hisoblash |
| Production | Производственный | Ishlab chiqarish |
| Admin | Административный | Ma'muriy |
| — | — | — |
| Asset class | Класс актива | Aktiv sinfi |
| Lifespan (months) | Срок службы (мес.) | Xizmat muddati (oy) |
| Default life (months) | Срок по умолчанию (мес.) | Standart muddat (oy) |
| Off default | Отличается | Standartdan farqli |
| Reclassify | Переклассифицировать | Qayta tasniflash |
| Personal | Личное | Shaxsiy |
| General | Общее | Umumiy |
| Depreciation | Амортизация | Amortizatsiya |
| Accumulated depreciation | Накопленная амортизация | To'plangan amortizatsiya |
| Net book value | Остаточная стоимость | Qoldiq qiymati |
| Skipped assets | Пропущенные объекты | O'tkazib yuborilgan jihozlar |
| — | — | — |
| Milestone | Этап | Bosqich |
| Work type | Вид работ | Ish turi |
| Planned hours | План часов | Reja soat |
| Planned cost | План себестоимости | Reja tannarx |
| Planned revenue | План дохода | Reja daromad |
| Risk coefficient | Коэффициент риска | Risk koeffitsienti |
| Minimum price | Минимальная цена | Minimal narx |
| Target price | Целевая цена | Maqsadli narx |
| Premium price | Премиум цена | Premium narx |
| Freeze plan | Зафиксировать план | Rejani muzlatish |
| Plan | План | Reja |
| Actual | Факт | Fakt |
| Variance | Отклонение | Farq |
| Over budget | Превышение бюджета | Byudjetdan oshgan |
| — | — | — |
| Open | Открыт | Ochiq |
| Soft closed | Мягко закрыт | Yumshoq yopilgan |
| Hard closed | Жёстко закрыт | Qattiq yopilgan |
| Close period | Закрыть период | Davrni yopish |
| Reopen period | Открыть период | Davrni ochish |
| FX revaluation | Переоценка валюты | Kurs farqini qayta baholash |
| Financial result | Финансовый результат | Moliyaviy natija |

---

*MIZAN Finance v5.0 — User Manual. Русская версия: [USER_MANUAL.ru.md](USER_MANUAL.ru.md)*
