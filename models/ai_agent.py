"""Local AI agent — talks to a Gemma model served by Ollama (fully offline).

Design notes
------------
* The app is local/offline, so the agent never calls the cloud. It talks to an
  Ollama server on the local machine (default ``http://127.0.0.1:11434``), which
  serves a Gemma model. Install: https://ollama.com  →  ``ollama pull gemma3``.
* No new pip dependency: we use stdlib ``urllib`` only.
* The agent answers from a *financial snapshot* assembled from the existing
  ``models`` functions (context injection), so it works on any Gemma tag and
  doesn't depend on model-specific tool-calling support.
* Everything degrades gracefully: if Ollama isn't running, callers get a clear
  message instead of a stack trace (see ``is_available`` / ``AIUnavailable``).

Configuration (env vars, all optional):
    MIZAN_AI_URL          base URL of the Ollama server (default http://127.0.0.1:11434)
    MIZAN_AI_MODEL        model tag to use              (default "gemma4:e4b")
    MIZAN_AI_TIMEOUT      HTTP read timeout, seconds    (default 600)
    MIZAN_AI_KEEP_ALIVE   keep model in RAM between reqs (default "30m"; "-1" = forever)
    MIZAN_AI_NUM_CTX      context window in tokens      (default 4096 — smaller = faster on CPU)
    MIZAN_AI_NUM_PREDICT  max output tokens             (default 512)
    MIZAN_AI_NUM_THREAD   CPU threads (0 = Ollama auto; set to physical core count)
"""
import os
import json
import urllib.request
import urllib.error

AI_URL = os.environ.get('MIZAN_AI_URL', 'http://127.0.0.1:11434').rstrip('/')
AI_MODEL = os.environ.get('MIZAN_AI_MODEL', 'gemma4:e4b')

# A 12B model on CPU can take minutes for the first (cold) request, so the read
# timeout is generous. With stream=False no bytes arrive until generation is
# fully done, so this is effectively a "total wait" budget.
_TIMEOUT = int(os.environ.get('MIZAN_AI_TIMEOUT', '600'))

# Performance knobs passed to Ollama. keep_alive avoids reloading the (slow to
# load) model on every call; num_ctx/num_predict bound the work the CPU must do.
_KEEP_ALIVE = os.environ.get('MIZAN_AI_KEEP_ALIVE', '30m')
_NUM_CTX = int(os.environ.get('MIZAN_AI_NUM_CTX', '4096'))
_NUM_PREDICT = int(os.environ.get('MIZAN_AI_NUM_PREDICT', '512'))
_NUM_THREAD = int(os.environ.get('MIZAN_AI_NUM_THREAD', '0'))

# Human-readable language names for the system prompt.
_LANG_NAME = {'uz': "O'zbek (Uzbek)", 'en': 'English', 'ru': 'Russian (Русский)'}


class AIUnavailable(RuntimeError):
    """Raised when the local model server can't be reached."""


# ----------------------------------------------------------------------------
# Low-level Ollama transport
# ----------------------------------------------------------------------------

def _post(path, payload):
    url = f'{AI_URL}{path}'
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.URLError as e:
        raise AIUnavailable(
            f"Local AI server unreachable at {AI_URL}. "
            f"Is Ollama running and is the model '{AI_MODEL}' pulled? ({e})"
        ) from e


def is_available():
    """Return (ok, detail). ``ok`` is True if the model server answers and the
    configured model is present."""
    try:
        req = urllib.request.Request(f'{AI_URL}/api/tags')
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode('utf-8'))
        names = [m.get('name', '') for m in tags.get('models', [])]
        present = any(n == AI_MODEL or n.startswith(AI_MODEL + ':') for n in names)
        if not present:
            return False, f"Model '{AI_MODEL}' not pulled. Run: ollama pull {AI_MODEL}"
        return True, f"{AI_MODEL} ready"
    except urllib.error.URLError:
        return False, f"Ollama not reachable at {AI_URL}. Start it with: ollama serve"
    except Exception as e:  # pragma: no cover - defensive
        return False, str(e)


def warmup():
    """Load the model into RAM without generating anything, so the first real
    request doesn't pay the cold-load cost. Returns True on success. Safe to call
    in a background thread at startup. (Empty prompt + keep_alive just loads.)"""
    try:
        _post('/api/chat', {
            'model': AI_MODEL,
            'messages': [],
            'stream': False,
            'keep_alive': _KEEP_ALIVE,
        })
        return True
    except AIUnavailable:
        return False


def chat(messages, temperature=0.3, format_json=False):
    """Send a chat completion to the local model. ``messages`` is a list of
    ``{'role', 'content'}`` dicts. Returns the assistant text (or parsed dict
    when ``format_json`` is set)."""
    options = {
        'temperature': temperature,
        'num_ctx': _NUM_CTX,
        'num_predict': _NUM_PREDICT,
    }
    if _NUM_THREAD > 0:
        options['num_thread'] = _NUM_THREAD
    payload = {
        'model': AI_MODEL,
        'messages': messages,
        'stream': False,
        'keep_alive': _KEEP_ALIVE,
        'options': options,
    }
    if format_json:
        payload['format'] = 'json'
    result = _post('/api/chat', payload)
    content = (result.get('message') or {}).get('content', '').strip()
    if format_json:
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return {'_raw': content}
    return content


# ----------------------------------------------------------------------------
# Financial snapshot — the context we feed the model
# ----------------------------------------------------------------------------

def build_financial_context():
    """Assemble a compact, current snapshot of the firm's finances as plain text.
    Imports are local to avoid circular imports at module load time."""
    from .dashboard import get_dashboard_data, get_ar_aging
    from .loans import get_loan_summary

    d = get_dashboard_data()
    ar = get_ar_aging()
    loans = get_loan_summary()

    def m(n):  # money formatter (UZS, no decimals)
        try:
            return f'{n:,.0f}'
        except (ValueError, TypeError):
            return str(n)

    lines = [
        '=== MIZAN FINANCE — CURRENT SNAPSHOT (UZS unless noted) ===',
        f"Active billable projects: {d['total_projects']}",
        f"Production staff: {d['production_staff']}, Admin staff: {d['admin_staff']}",
        f"Total billable hours logged: {m(d['total_hours'])}",
        f"Utilization: {d['utilization_pct']:.1f}%",
        f"Avg cost rate: {m(d['avg_cost_rate'])}/h, Avg billing rate: {m(d['avg_billing_rate'])}/h",
        f"Total MIZAN cost: {m(d['total_mizan_cost'])}",
        f"Total income (received): {m(d['total_income'])}",
        f"Gross profit: {m(d['total_profit'])} (margin {d['gross_margin_pct']:.1f}%)",
        f"Monthly overhead: {m(d['monthly_overhead'])}",
        f"Burn rate (monthly fixed cost): {m(d['burn_rate'])}",
        f"Cash balance: {m(d['running_balance'])}, Runway: {d['runway_months']} months",
        f"Capacity booked: {d['capacity_pct']:.1f}% "
        f"({m(d['capacity_booked'])} of {m(d['capacity_total'])} h)",
        f"Accounts receivable outstanding: {m(d['ar_total'])}, overdue (>60d): {m(d['ar_overdue'])}",
        f"FX gain/loss: {m(d['total_fx_gain_loss'])}",
        f"USD rate: {m(d['usd_rate'])}",
        f"Loans — borrowed (olgan) outstanding: {m(loans.get('taken_remaining', 0))}, "
        f"lent (bergan) outstanding: {m(loans.get('given_remaining', 0))}, "
        f"overdue loans: {loans.get('overdue_count', 0)}",
    ]

    top = d.get('top_projects', [])[:8]
    if top:
        lines.append('\nTop projects by hours (name | cost | income | profit | hours):')
        for p in top:
            lines.append(
                f"  - {p.get('name', '?')} | cost {m(p.get('mizan_cost', 0))} | "
                f"income {m(p.get('income', 0))} | profit {m(p.get('profit', 0))} | "
                f"{m(p.get('total_hours', 0))}h"
            )

    overdue = ar.get('overdue_items', [])[:5]
    if overdue:
        lines.append('\nOverdue receivables (>90 days):')
        for o in overdue:
            lines.append(f"  - {o['desc']} | {m(o['outstanding'])} | {o['days']} days")

    return '\n'.join(lines)


# ----------------------------------------------------------------------------
# Agent capabilities
# ----------------------------------------------------------------------------

def _system_prompt(lang):
    lang_name = _LANG_NAME.get(lang, _LANG_NAME['uz'])
    return (
        "You are MIZAN AI, the in-house financial assistant for an architecture "
        "firm. You are given a factual snapshot of the firm's finances. "
        "Answer ONLY from that snapshot and the user's question — never invent "
        "numbers. If the data needed is not present, say so plainly. Be concise "
        "and practical, like a CFO briefing a manager. "
        f"Always reply in {lang_name}."
    )


def ask(question, lang='uz'):
    """Free-form Q&A over the financial snapshot. Returns assistant text."""
    context = build_financial_context()
    messages = [
        {'role': 'system', 'content': _system_prompt(lang)},
        {'role': 'user', 'content': f"{context}\n\n=== QUESTION ===\n{question}"},
    ]
    return chat(messages, temperature=0.3)


def generate_cfo_summary(lang='uz'):
    """Generate a short CFO-style narrative of the firm's current position."""
    context = build_financial_context()
    instruction = (
        "Write a brief CFO summary (4-7 short bullet points) of the firm's "
        "current financial position: profitability, cash/runway, capacity, and "
        "receivables. Flag anything that needs attention. Use only the snapshot."
    )
    messages = [
        {'role': 'system', 'content': _system_prompt(lang)},
        {'role': 'user', 'content': f"{context}\n\n=== TASK ===\n{instruction}"},
    ]
    return chat(messages, temperature=0.4)


def detect_anomalies(lang='uz'):
    """Ask the model to flag financial risks from the snapshot."""
    context = build_financial_context()
    instruction = (
        "Act as a financial controller. From the snapshot, list any RISKS or "
        "RED FLAGS (e.g. short runway, capacity overload above 85%, overdue "
        "receivables, negative-margin projects, FX losses). For each, give a "
        "one-line recommended action. If nothing is concerning, say so."
    )
    messages = [
        {'role': 'system', 'content': _system_prompt(lang)},
        {'role': 'user', 'content': f"{context}\n\n=== TASK ===\n{instruction}"},
    ]
    return chat(messages, temperature=0.3)


# Valid transaction types, mirrored from the schema (see CLAUDE.md).
_TX_TYPES = {
    'external': ['tushum', 'outsourcing', 'material', 'mizan_monthly', 'yakuniy_hisob'],
    'internal': ['maosh', 'premiya', 'ijara', 'kommunal', 'soliq', 'ovqat',
                 'litsenziya', 'malaka', 'overhead'],
}


def categorize_transaction(description, amount=None, direction='internal'):
    """Suggest a tx_type for a free-text transaction description. Returns a dict
    ``{'tx_type', 'confidence', 'reason'}``. Useful during manual entry / import."""
    valid = _TX_TYPES.get(direction, _TX_TYPES['internal'])
    sys = (
        "You categorize accounting transactions for an architecture firm. "
        f"Choose exactly one tx_type from this list: {valid}. "
        "Respond as JSON with keys: tx_type (one of the list), confidence "
        "(0-1 float), reason (short string). No prose outside the JSON."
    )
    user = f"Description: {description}\nAmount: {amount}\nDirection: {direction}"
    out = chat(
        [{'role': 'system', 'content': sys}, {'role': 'user', 'content': user}],
        temperature=0.0, format_json=True,
    )
    # Guard against the model returning an out-of-vocabulary type.
    if isinstance(out, dict) and out.get('tx_type') not in valid:
        out['confidence'] = 0
    return out
