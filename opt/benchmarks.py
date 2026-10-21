#!/home/krzysiek/basal-env/bin/python
"""Real benchmark results in the model cards (opt/benchmarks.py).

Source: the OpenRouter models API — per-model `benchmarks` carry Artificial Analysis indices
(intelligence/coding/agentic, 0-100) and WebDev Arena Elo ratings per design category. Only real,
sourced numbers are written; models without published benchmarks get no benchmark line.

Registry -> OpenRouter mapping: direct for `openrouter/*` ids; for `opencode/*` ids via the
`canonical_model_id` field from models.dev (base-slug match, `:free` variant preferred); nim-*
entries map through their models.dev canonical ids. Enrichment is idempotent — an existing
"Benchmarks (...)" suffix is replaced, never duplicated. The role text of a card is never touched.

Usage:
  ~/basal-env/bin/python opt/benchmarks.py enrich   # fetch + apply to models.json (cron runs this)
  ~/basal-env/bin/python opt/benchmarks.py status   # show the mapping
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import router  # noqa: E402

DIR = ROOT / "opt" / "benchmarks"
DATA_PATH = DIR / "data.json"
OR_MODELS = "https://openrouter.ai/api/v1/models"


def base_slug(identifier):
    return identifier.split("/")[-1].removesuffix(":free")


def fetch_or_models():
    response = httpx.get(OR_MODELS, timeout=60)
    response.raise_for_status()
    return response.json().get("data", [])


def dev_entry(dev, entry_id):
    provider, _, model_id = entry_id.partition("/")
    models = (dev.get(provider) or {}).get("models") or {}
    if model_id in models:
        return models[model_id]
    if provider == "nvidia" and f"nvidia/{model_id}" in models:
        return models[f"nvidia/{model_id}"]
    return None


def candidates_for(entry, dev):
    cands = []
    if entry["id"].startswith("openrouter/"):
        cands.append(entry["id"].removeprefix("openrouter/"))
    de = dev_entry(dev, entry["id"])
    canonical = (de or {}).get("canonical_model_id")
    if canonical:
        cands += [canonical, canonical + ":free"]
    cands.append(base_slug(entry["id"]))
    seen, unique = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def find_or_entry(or_models, cands):
    for cand in cands:
        exact = [m for m in or_models if base_slug(m["id"]) == base_slug(cand)
                 and m["id"].split("/")[0] == cand.split("/")[0]] if "/" in cand else \
                [m for m in or_models if base_slug(m["id"]) == base_slug(cand)]
        if not exact:
            exact = [m for m in or_models if base_slug(m["id"]) == base_slug(cand)]
        free = [m for m in exact if m["id"].endswith(":free")]
        if free:
            return free[0]
        if exact:
            return exact[0]
    for cand in cands:
        b = base_slug(cand)
        if len(b) < 8:
            continue
        pre = [m for m in or_models
               if len(base_slug(m["id"])) >= 8
               and (base_slug(m["id"]).startswith(b) or b.startswith(base_slug(m["id"])))]
        free = [m for m in pre if m["id"].endswith(":free")]
        if free:
            return free[0]
        if pre:
            return pre[0]
    return None


def bench_line(benchmarks):
    parts = []
    aa = benchmarks.get("artificial_analysis") or {}
    for key, label in (("intelligence_index", "intelligence"), ("coding_index", "coding"),
                       ("agentic_index", "agentic")):
        value = aa.get(key)
        if value is not None:
            parts.append(f"{label} {value:.0f}/100")
    elos = [a for a in benchmarks.get("design_arena") or [] if a.get("elo")]
    elos = sorted(elos, key=lambda a: -a["elo"])[:2]
    for a in elos:
        parts.append(f"{a['category']} elo {a['elo']}")
    if not parts:
        return ""
    return f"Benchmarks (Artificial Analysis / WebDev Arena via OpenRouter): {'; '.join(parts)}."


def enrich():
    or_models = [m for m in fetch_or_models() if m.get("benchmarks")]
    dev = router.load_modelsdev()
    registry = router.load_registry()
    mapping = {}
    changed = 0
    for m in registry["models"]:
        entry = find_or_entry(or_models, candidates_for(m, dev))
        line = bench_line(entry.get("benchmarks") or {}) if entry else ""
        card = re.split(r"\s*Benchmarks \(", m["card"])[0].rstrip()
        if line:
            card = f"{card} {line}"
        if card != m["card"]:
            changed += 1
        m["card"] = card
        mapping[m["key"]] = {"or_id": entry["id"] if entry else None,
                             "benchmark_line": line,
                             "aa": entry.get("benchmarks", {}).get("artificial_analysis") if entry else None}
    router.REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    DIR.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps({"fetched": time.strftime("%Y-%m-%d %H:%M"),
                                     "source": "openrouter.ai/api/v1/models (benchmarks field)",
                                     "models_with_benchmarks": len(or_models),
                                     "mapping": mapping}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    matched = sum(1 for v in mapping.values() if v["or_id"])
    print(json.dumps({"matched": matched, "of": len(mapping), "cards_changed": changed},
                     ensure_ascii=False))
    for key, v in mapping.items():
        mark = v["benchmark_line"][:80] if v["benchmark_line"] else "no public benchmarks"
        print(f"  {key}: {v['or_id'] or '-'} :: {mark}")


def status():
    if not DATA_PATH.exists():
        print("no data yet — run: opt/benchmarks.py enrich")
        return
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    print(f"fetched: {data['fetched']} · source: {data['source']}")
    for key, v in data.get("mapping", {}).items():
        print(f"  {key}: {v['or_id'] or '-'} :: {v['benchmark_line'][:90]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="enrich", choices=["enrich", "status"])
    args = parser.parse_args()
    enrich() if args.mode == "enrich" else status()


if __name__ == "__main__":
    main()
