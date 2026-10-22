#!/home/krzysiek/basal-env/bin/python
"""Per-provider rate-limit tracking (RPM and cooldowns) for swarm scheduling.

Free model pools live behind shared quota: blast one provider with parallel subagent calls
and every model behind it starts answering 429. This module keeps a rolling 60-second call
window per provider plus explicit cooldowns raised when a 429 (or a "Rate limit" CLI error)
is observed, and exposes `available(entry)` / `headroom(provider)` so the swarm can spread
subagent assignments across providers before the limits bite.

Limits come from settings.rate_limits ({"<provider>": rpm}), default 20 for unknown
providers. ollama (local) is unlimited by default. State persists in opt/rpm/state.json so
restarts keep cooldowns; a thread lock guards routerd's threadpool access.

Usage:
  ~/basal-env/bin/python opt/rpm.py status
"""
import argparse
import json
import time
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "opt" / "rpm"
STATE = DIR / "state.json"
WINDOW_S = 60.0
DEFAULT_RPM = 20
LOCAL_PROVIDERS = {"ollama"}
RATE_LIMIT_MARKERS = ("rate limit", "429", "too many requests", "retry-after")
QUOTA_MARKERS = ("quota", "usage limit", "usagelimit", "freeusagelimit")
COOLDOWN_S = 60.0
QUOTA_COOLDOWN_S = 900.0

_lock = Lock()


def _load():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"calls": {}, "cooldowns": {}}


def _save(state):
    DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _prune(state, now):
    for provider, stamps in list(state["calls"].items()):
        fresh = [t for t in stamps if now - t < WINDOW_S]
        if fresh:
            state["calls"][provider] = fresh
        else:
            state["calls"].pop(provider, None)
    for provider, until in list(state["cooldowns"].items()):
        if now >= until:
            state["cooldowns"].pop(provider, None)


def limits():
    try:
        import router  # noqa: E402  (available when run from the repo, not on import)
        return router.load_settings().get("rate_limits") or {}
    except Exception:
        return {}


def limit_for(provider):
    if provider in LOCAL_PROVIDERS:
        return None
    custom = limits().get(provider)
    return int(custom) if isinstance(custom, (int, float)) and custom > 0 else DEFAULT_RPM


def provider_of(entry):
    """Provider behind a registry entry: direct runners carry `provider`, opencode-runner
    ids are prefixed with it (opencode/…, ollama/…, openrouter/…)."""
    return entry.get("provider") or entry["id"].partition("/")[0] or "opencode"


def record(provider):
    """Count one call attempt against the provider's rolling window."""
    with _lock:
        state = _load()
        now = time.time()
        _prune(state, now)
        state["calls"].setdefault(provider, []).append(now)
        _save(state)


def mark_rate_limited(provider, retry_after_s=None, quota=False):
    """Raise the provider cooldown. Rate-limit spikes cool down for a minute; quota errors
    (opencode FreeUsageLimitError etc.) are usage limits, not bursts — 15 minutes."""
    with _lock:
        state = _load()
        now = time.time()
        _prune(state, now)
        cooldown = QUOTA_COOLDOWN_S if quota else COOLDOWN_S
        if retry_after_s:
            cooldown = max(5.0, float(retry_after_s))
        state["cooldowns"][provider] = max(now + cooldown,
                                           state["cooldowns"].get(provider, 0))
        _save(state)
        return state["cooldowns"][provider]


def cooling_until(provider):
    with _lock:
        state = _load()
        _prune(state, time.time())
        return state["cooldowns"].get(provider, 0)


def headroom(provider):
    """Calls still allowed in the current window (None = unlimited)."""
    limit = limit_for(provider)
    if limit is None:
        return None
    with _lock:
        state = _load()
        _prune(state, time.time())
        used = len(state["calls"].get(provider, []))
    return max(0, limit - used)


def available(entry):
    """True when the entry's provider is not cooling down and has RPM headroom."""
    provider = provider_of(entry)
    if cooling_until(provider) > time.time():
        return False
    room = headroom(provider)
    return room is None or room > 0


def wait_headroom(providers, max_wait_s=60):
    """Seconds to sleep until some provider frees capacity (0 = capacity now);
    returns None when no provider regains headroom within the bound."""
    now = time.time()
    if any((headroom(p) is None or headroom(p) > 0) and cooling_until(p) <= now
           for p in providers):
        return 0
    with _lock:
        state = _load()
        _prune(state, now)
        window_ends = [min(state["calls"].get(p, [now])) + WINDOW_S for p in providers]
        cooldown_ends = [state["cooldowns"].get(p, 0) for p in providers]
    active_cooldowns = [c for c in cooldown_ends if c > now]
    unblocks = min(window_ends + active_cooldowns) if (window_ends or active_cooldowns) else now
    return None if unblocks - now > max_wait_s else max(0, unblocks - now)


def stats():
    with _lock:
        state = _load()
        _prune(state, time.time())
        calls = {p: len(v) for p, v in state["calls"].items()}
        cooldowns = {p: round(u - time.time(), 1) for p, u in state["cooldowns"].items()}
    return {"window_s": WINDOW_S, "limits": {p: limit_for(p) for p in
                                             set(calls) | set(cooldowns) | set(limits())},
            "calls_in_window": calls, "cooldowns_s": cooldowns}


def looks_rate_limited(text):
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def looks_quota_limited(text):
    lowered = str(text or "").lower().replace(" ", "")
    return any(marker in lowered for marker in QUOTA_MARKERS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="status", choices=["status"])
    args = parser.parse_args()
    print(json.dumps(stats(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
