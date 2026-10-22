#!/home/krzysiek/basal-env/bin/python
"""Automated discovery and refresh of free models — the self-updating registry layer.

Sources: models.dev (the master catalog; free = zero cost), the live `opencode models` list (what the
opencode runner can actually execute), the OpenRouter catalog (:free variants) and the catalogs of
providers that have a saved key. New models get role-oriented English cards written by a free model
through routerd — the card research showed role cards route and raw facts do not. Which candidates
to add (and which vanished entries to drop) is also decided by a free model, then hard-guarded:
registry cap 20, the default model is never touched, existing cards are never rewritten, and every
change is journaled in opt/discovery/change_log.json with full revert data.

Usage:
  ~/basal-env/bin/python opt/discovery.py scan       # sources -> proposal (no changes applied)
  ~/basal-env/bin/python opt/discovery.py refresh    # scan + apply (cron and the panel run this)
  ~/basal-env/bin/python opt/discovery.py status
  ~/basal-env/bin/python opt/discovery.py revert <n>
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import router  # noqa: E402

DIR = ROOT / "opt" / "discovery"
LAST_SCAN = DIR / "last_scan.json"
CHANGELOG = DIR / "change_log.json"
ROUTERD = "http://127.0.0.1:8788/v1/chat/completions"
OPENCODE = "/home/krzysiek/.opencode/bin/opencode"
FREE_RE = re.compile(r"(-free$|:free$)")
REGISTRY_CAP = 20
KNOWN_FREE = {"opencode/big-pickle"}


def llm(prompt):
    """Registry-update reasoning runs on the sandboxed Hermes agent — its one job in this repo.
    A plain routerd call stays as the fallback if the agent is unavailable, errors out, or
    returns an opencode error event instead of text."""
    text = ""
    try:
        text = router.execute_hermes(router.load_settings().get("hermes_model") or "auto", prompt, 600)
    except Exception:
        text = ""
    if not text.strip() or '"type":"error"' in text:
        try:
            response = httpx.post(ROUTERD,
                                  json={"model": "auto", "messages": [{"role": "user", "content": prompt}]},
                                  timeout=600)
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
    marker = "\n---\n[router]"
    if marker in text:
        text = text.split(marker)[0].strip()
    return text


def parse_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def fetch_opencode_models():
    try:
        proc = subprocess.run([OPENCODE, "models"], capture_output=True, text=True, timeout=60)
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except Exception:
        return set()


def fetch_openrouter_free():
    try:
        response = httpx.get("https://openrouter.ai/api/v1/models", timeout=60)
        response.raise_for_status()
        free = {}
        for m in response.json().get("data", []):
            pricing = m.get("pricing") or {}
            if m.get("id", "").endswith(":free") or (pricing.get("prompt") == "0"
                                                     and pricing.get("completion") == "0"):
                free[f"openrouter/{m['id']}"] = {
                    "name": m.get("name", ""),
                    "description": (m.get("description") or "")[:200],
                    "context": m.get("context_length")}
        return free
    except Exception:
        return {}


def fetch_keyed_provider_models():
    out = {}
    for name, cfg in router.load_providers().items():
        if name.startswith("_") or not cfg.get("apiKey"):
            continue
        try:
            response = httpx.get(f"{cfg['baseURL'].rstrip('/')}/models",
                                 headers={"Authorization": f"Bearer {cfg['apiKey']}"}, timeout=30)
            response.raise_for_status()
            raw = response.json()
            raw = raw.get("data", raw) if isinstance(raw, dict) else raw
            for m in raw:
                if isinstance(m, dict) and m.get("id"):
                    out[f"{name}/{m['id']}"] = {"name": m["id"], "description": "", "context": None}
        except Exception:
            continue
    return out


def opencode_auth_providers():
    """Providers credentialed inside opencode's own auth store. Models of other providers
    still appear in the `opencode models` catalog but fail with 401 when executed."""
    try:
        auth = json.loads(Path.home().joinpath(".local/share/opencode/auth.json").read_text())
        return {p for p, v in auth.items()
                if isinstance(v, dict) and (v.get("key") or v.get("access"))}
    except Exception:
        return set()


def ollama_local_models():
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=10).json()
        return {m.get("name", "").split(":")[0] for m in tags.get("models", [])}
    except Exception:
        return set()


def dev_entry(dev, entry_id):
    provider, _, model_id = entry_id.partition("/")
    models = (dev.get(provider) or {}).get("models") or {}
    if model_id in models:
        return models[model_id]
    if provider == "nvidia" and f"nvidia/{model_id}" in models:
        return models[f"nvidia/{model_id}"]
    return None


def dev_facts(entry):
    limit = entry.get("limit") or {}
    cost = entry.get("cost") or {}
    caps = []
    if entry.get("reasoning"):
        caps.append("deep reasoning")
    if entry.get("tool_call"):
        caps.append("tools (function calling)")
    if "image" in (entry.get("modalities") or {}).get("input", []):
        caps.append("images (vision)")
    return {"context": limit.get("context"), "output": limit.get("output"),
            "free": cost.get("input") == 0 and cost.get("output") == 0,
            "capabilities": caps, "description": (entry.get("description") or "")[:300]}


def write_card(entry_id):
    provider, _, model_id = entry_id.partition("/")
    entry = dev_entry(router.load_modelsdev(), entry_id)
    facts = dev_facts(entry) if entry else {"context": None, "free": True,
                                            "capabilities": [], "description": ""}
    prompt = ("Write a concise English role card (under 45 words) for a model-router registry.\n"
              f"Model id: {entry_id}.\nFacts from the catalog: {json.dumps(facts)}\n"
              "Focus on which tasks this model is best at, in the style: "
              "'Fast free model for X: a, b, c. Not for d.' Factual only. Output ONLY the card text.")
    try:
        card = llm(prompt)
        if 10 < len(card) < 500 and "{" not in card:
            return card
    except Exception:
        pass
    return router.build_catalog_card(provider, model_id)


def scan():
    dev = router.load_modelsdev()
    opencode_listed = fetch_opencode_models()
    openrouter_free = fetch_openrouter_free()
    keyed = fetch_keyed_provider_models()
    registry = router.load_registry()
    by_id = {m["id"]: m for m in registry["models"]}

    reachable = {}
    for mid in sorted(opencode_listed):
        if FREE_RE.search(mid) or mid in KNOWN_FREE:
            reachable.setdefault(mid, {"name": mid, "description": ""})
    reachable.update(openrouter_free)
    reachable.update(keyed)

    oc_auth = opencode_auth_providers()
    ollama_local = ollama_local_models()
    keyed_providers = {p for p, c in router.load_providers().items()
                       if not p.startswith("_") and c.get("apiKey")}

    def executable(mid):
        """Catalog presence is not reachability: a model only counts when its execution
        path is credentialed (opencode gateway, opencode-stored auth, or our own key)."""
        provider, _, model_id = mid.partition("/")
        if provider == "opencode":
            return True
        if provider == "ollama":
            return model_id.split(":")[0] in ollama_local
        return provider in oc_auth or provider in keyed_providers

    candidates = {mid: info for mid, info in reachable.items()
                  if mid not in by_id and executable(mid)}

    vanished = []
    for m in registry["models"]:
        if m.get("runner", "opencode") != "opencode":
            continue
        provider = m["id"].partition("/")[0]
        if not executable(m["id"]):
            vanished.append(m["key"])
        elif provider == "opencode" and m["id"] not in opencode_listed:
            vanished.append(m["key"])
        elif provider == "openrouter" and m["id"] not in openrouter_free:
            vanished.append(m["key"])
        elif provider == "ollama" and m["id"] not in opencode_listed:
            vanished.append(m["key"])

    slots = REGISTRY_CAP - len(registry["models"])
    proposal = {"add": [], "drop": [], "reason": ""}
    raw = ""
    if candidates or vanished:
        listing = "\n".join(f"- {mid} :: {(info.get('description') or '')[:140]}"
                            for mid, info in list(candidates.items())[:40])
        current = "\n".join(f"- {m['key']} :: {m['card'][:70]}" for m in registry["models"])
        prompt = (f"A local model router keeps a registry of free models (cap {REGISTRY_CAP}, "
                  f"now {len(registry['models'])}, {slots} free slots).\n"
                  f"Current registry:\n{current}\n\n"
                  f"Newly reachable free models:\n{listing or '(none)'}\n\n"
                  f"Entries that vanished from their source or lost a working execution path: "
                  f"{vanished or 'none'}\n"
                  "Entries must never sit dead in the registry: they poison routing decisions. "
                  "Pick at most "
                  f"{max(slots, 0)} additions that fill missing specialisations, and drop only "
                  "vanished entries (never the default model).\n"
                  'Output STRICT JSON: {"add": [model ids], "drop": [registry keys], '
                  '"reason": "one sentence"}. No other text.')
        try:
            raw = llm(prompt)
            proposal = parse_json(raw) or proposal
        except Exception:
            raw = ""
    if not proposal.get("add") and not proposal.get("drop") and not proposal.get("reason"):
        proposal["reason"] = ("agent produced no parseable proposal — no changes applied"
                              if (candidates or vanished) else "")
    proposal["add"] = [mid for mid in proposal.get("add", []) if mid in candidates][:max(slots, 0)]
    default_key = registry.get("default_model")
    proposal["drop"] = [k for k in proposal.get("drop", []) if k in vanished and k != default_key]

    result = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "sources": {"opencode": len(opencode_listed),
                          "openrouter_free": len(openrouter_free),
                          "keyed_provider_models": len(keyed),
                          "modelsdev_providers": len(dev)},
              "reachable": len(reachable), "candidates": len(candidates),
              "vanished": vanished, "proposal": proposal,
              "raw_proposal": raw[:600]}
    DIR.mkdir(parents=True, exist_ok=True)
    LAST_SCAN.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def save_registry(registry):
    router.REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")


def apply_proposal(result):
    registry = router.load_registry()
    keyed_providers = {p for p, c in router.load_providers().items()
                      if not p.startswith("_") and c.get("apiKey")}
    added = []
    for entry_id in result["proposal"]["add"]:
        if len(registry["models"]) >= REGISTRY_CAP:
            break
        provider, _, model_id = entry_id.partition("/")
        existing = {m["key"] for m in registry["models"]}
        key = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:32] or "model"
        if key in existing:
            continue
        runner = "direct" if provider in keyed_providers else "opencode"
        entry = {"key": key, "id": entry_id, "name": model_id,
                 "card": write_card(entry_id), "runner": runner,
                 "tags": [runner, "discovered"],
                 "meta": {"card_source": "discovery (free-model written card)"}}
        if runner == "direct":
            entry["provider"] = provider
            entry["model"] = model_id
        registry["models"].append(entry)
        added.append(key)

    drop_set = set(result["proposal"]["drop"])
    dropped_entries = [m for m in registry["models"] if m["key"] in drop_set]
    dropped = [m["key"] for m in dropped_entries]
    if not added and not dropped and not result["proposal"].get("reason"):
        return {"added": [], "dropped": [], "reason": "",
                "registry_size": len(registry["models"])}
    registry["models"] = [m for m in registry["models"] if m["key"] not in drop_set]
    save_registry(registry)

    changelog = []
    if CHANGELOG.exists():
        changelog = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    changelog.append({"n": len(changelog) + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "added": added, "dropped": dropped,
                      "dropped_entries": dropped_entries,
                      "reason": result["proposal"].get("reason", "")})
    CHANGELOG.write_text(json.dumps(changelog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"added": added, "dropped": dropped,
            "reason": result["proposal"].get("reason", ""),
            "registry_size": len(registry["models"])}


def refresh():
    result = scan()
    if not router.load_settings().get("model_autorefresh", True):
        print("[refresh] model_autorefresh disabled in settings — scan saved, no changes", flush=True)
        return result
    summary = apply_proposal(result)
    result["applied"] = summary
    LAST_SCAN.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    try:
        subprocess.run([sys.executable, str(ROOT / "opt" / "benchmarks.py"), "enrich"],
                       capture_output=True, text=True, timeout=180)
        subprocess.run([sys.executable, str(ROOT / "opt" / "performance.py"), "refresh"],
                       capture_output=True, text=True, timeout=120)
    except Exception as exc:
        print(f"[refresh] post-update enrich failed: {exc}", flush=True)
    return result


def revert(n):
    changelog = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    entry = next((e for e in changelog if e.get("n") == n), None)
    if not entry:
        raise SystemExit(f"no changelog entry {n}")
    registry = router.load_registry()
    added = set(entry.get("added", []))
    registry["models"] = [m for m in registry["models"] if m["key"] not in added]
    registry["models"].extend(entry.get("dropped_entries", []))
    save_registry(registry)
    print(f"reverted change {n}: removed {entry.get('added')}, restored {entry.get('dropped')}")


def status():
    if LAST_SCAN.exists():
        result = json.loads(LAST_SCAN.read_text(encoding="utf-8"))
        print(json.dumps({k: result[k] for k in ("ts", "sources", "candidates", "vanished",
                                                 "proposal")}
                         | {"applied": result.get("applied", "not applied")}, ensure_ascii=False, indent=2))
    else:
        print("no scan yet")
    if CHANGELOG.exists():
        changelog = json.loads(CHANGELOG.read_text(encoding="utf-8"))
        for e in changelog[-3:]:
            print(f"change {e['n']}: +{e['added']} -{e['dropped']} :: {e['reason'][:100]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="status", choices=["scan", "refresh", "status"])
    parser.add_argument("--revert", type=int)
    args = parser.parse_args()
    if args.revert is not None:
        revert(args.revert)
        return
    if args.mode == "scan":
        result = scan()
        print(json.dumps({"candidates": result["candidates"], "vanished": result["vanished"],
                          "proposal": result["proposal"]}, ensure_ascii=False, indent=2))
    elif args.mode == "refresh":
        refresh()
    else:
        status()


if __name__ == "__main__":
    main()
