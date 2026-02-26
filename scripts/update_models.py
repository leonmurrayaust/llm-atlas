#!/usr/bin/env python3
"""
LLM Atlas — Nightly Model Data Pipeline
Runs via GitHub Actions every night at 2am UTC.
Scrapes Artificial Analysis + Papers With Code for new models/updated scores.
Opens a PR if anything changed. Auto-merges if changes are minor (<2pt drift).
"""

import json
import os
import re
import sys
import time
import datetime
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────

DATA_FILE = Path(__file__).parent.parent / "data" / "models.json"
LOG_FILE  = Path(__file__).parent.parent / "data" / "update_log.json"
THRESHOLD_AUTO_MERGE = 2.0   # score points — drift below this = auto-merge PR
THRESHOLD_ALERT      = 5.0   # score points — drift above this = Discord alert

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
REPO            = os.environ.get("GITHUB_REPOSITORY", "leonmurrayaust/llm-atlas")

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")

def fetch_json(url, headers=None):
    req = Request(url, headers=headers or {"User-Agent": "LLM-Atlas-Bot/1.0"})
    try:
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log(f"  WARN fetch failed {url}: {e}")
        return None

def load_current():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"models": [], "last_updated": None, "version": 1}

def save_data(data):
    data["last_updated"] = datetime.datetime.utcnow().isoformat() + "Z"
    data["version"] = data.get("version", 1) + 1
    DATA_FILE.write_text(json.dumps(data, indent=2))
    log(f"  Saved {len(data['models'])} models → {DATA_FILE}")

def append_log(entry):
    log_data = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else {"runs": []}
    log_data["runs"].append(entry)
    log_data["runs"] = log_data["runs"][-90:]  # keep 90 days
    LOG_FILE.write_text(json.dumps(log_data, indent=2))

# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_artificial_analysis():
    """
    Fetch the Artificial Analysis Intelligence Index leaderboard.
    AA publishes a public JSON endpoint for their rankings.
    """
    log("Fetching Artificial Analysis leaderboard…")
    # AA's public data endpoint (check their docs/network tab for current URL)
    url = "https://artificialanalysis.ai/api/v1/models"
    data = fetch_json(url)
    if not data:
        log("  AA endpoint unavailable — using cached baseline")
        return []

    models = []
    for m in data.get("models", []):
        models.append({
            "id":          m.get("slug", ""),
            "name":        m.get("name", ""),
            "maker":       m.get("organization", ""),
            "intelligence": m.get("intelligence_index", None),
            "speed":       m.get("output_speed", None),
            "context":     m.get("context_window_k", None),
            "costInput":   m.get("input_cost_per_1m", None),
            "costOutput":  m.get("output_cost_per_1m", None),
            "source":      "artificial_analysis",
        })
    log(f"  AA: {len(models)} models fetched")
    return models

def scrape_papers_with_code():
    """
    Fetch benchmark leaderboard data from Papers With Code API.
    Used for GPQA, MATH-500, HumanEval, SWE-bench scores.
    """
    log("Fetching Papers With Code benchmarks…")
    benchmarks = {
        "gpqa":      "gpqa-diamond",
        "math":      "math-500",
        "humanEval": "humaneval",
        "swe":       "swe-bench-verified",
        "aime":      "aime-2025",
    }
    results = {}
    base = "https://paperswithcode.com/api/v1/sota/?benchmark="
    for key, slug in benchmarks.items():
        data = fetch_json(base + slug)
        if not data:
            continue
        for row in data.get("results", [])[:50]:
            model_name = row.get("model_name", "").strip()
            score = row.get("metrics", {}).get("Accuracy", None) or \
                    row.get("metrics", {}).get("Pass@1", None) or \
                    row.get("metrics", {}).get("% Resolved", None)
            if model_name and score is not None:
                if model_name not in results:
                    results[model_name] = {}
                results[model_name][key] = float(score)
        time.sleep(0.5)  # be polite
    log(f"  PWC: scores for {len(results)} models across {len(benchmarks)} benchmarks")
    return results

def scrape_lmsys_arena():
    """
    Fetch LMSYS Chatbot Arena ELO scores.
    """
    log("Fetching LMSYS Chatbot Arena ELO…")
    url = "https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard/raw/main/elo_results.json"
    data = fetch_json(url)
    if not data:
        return {}
    elo_map = {}
    for entry in data.get("full", {}).get("leaderboard_table_df", []):
        name  = entry.get("model", "")
        score = entry.get("elo_rating", None)
        if name and score:
            elo_map[name] = int(score)
    log(f"  Arena: {len(elo_map)} ELO scores")
    return elo_map

# ── Diff & Change Detection ────────────────────────────────────────────────────

def diff_models(old_models, new_models):
    """
    Compare old vs new model data.
    Returns: (changes list, max_drift, needs_alert)
    """
    old_map = {m["id"]: m for m in old_models}
    new_map = {m["id"]: m for m in new_models}

    changes = []
    numeric_keys = ["intelligence", "gpqa", "math", "humanEval", "swe", "aime", "speed", "arenaElo"]

    # Updated models
    for mid, new_m in new_map.items():
        if mid in old_map:
            old_m = old_map[mid]
            diffs = {}
            for k in numeric_keys:
                ov = old_m.get(k)
                nv = new_m.get(k)
                if ov is not None and nv is not None:
                    drift = abs(float(nv) - float(ov))
                    if drift > 0.1:
                        diffs[k] = {"old": ov, "new": nv, "drift": round(drift, 2)}
            if diffs:
                changes.append({"type": "updated", "id": mid, "name": new_m.get("name",""), "diffs": diffs})
        else:
            changes.append({"type": "new_model", "id": mid, "name": new_m.get("name",""), "data": new_m})

    # Removed models (flag, don't delete automatically)
    for mid in old_map:
        if mid not in new_map:
            changes.append({"type": "disappeared", "id": mid, "name": old_map[mid].get("name","")})

    max_drift = max(
        (max(d["drift"] for d in c["diffs"].values()) for c in changes if c["type"] == "updated" and c.get("diffs")),
        default=0.0
    )
    needs_alert = max_drift >= THRESHOLD_ALERT or any(c["type"] == "new_model" for c in changes)

    return changes, max_drift, needs_alert

# ── Git / PR ──────────────────────────────────────────────────────────────────

def git_commit_and_pr(changes, max_drift):
    """
    Commit updated data file and open a GitHub PR.
    Auto-merges if drift is minor.
    """
    branch = f"data-update-{datetime.date.today().isoformat()}"
    auto_merge = max_drift < THRESHOLD_AUTO_MERGE and not any(c["type"] == "new_model" for c in changes)

    subprocess.run(["git", "config", "user.email", "bot@llm-atlas.app"], check=True)
    subprocess.run(["git", "config", "user.name",  "LLM Atlas Bot"],      check=True)
    subprocess.run(["git", "checkout", "-b", branch],                      check=True)
    subprocess.run(["git", "add", "data/"],                                check=True)

    new_models   = [c["name"] for c in changes if c["type"] == "new_model"]
    updated      = [c["name"] for c in changes if c["type"] == "updated"]
    disappeared  = [c["name"] for c in changes if c["type"] == "disappeared"]

    commit_msg_lines = ["🤖 Nightly data update", ""]
    if new_models:   commit_msg_lines.append(f"✨ New models: {', '.join(new_models)}")
    if updated:      commit_msg_lines.append(f"📊 Updated: {', '.join(updated[:10])}" + (" +more" if len(updated) > 10 else ""))
    if disappeared:  commit_msg_lines.append(f"⚠️  Disappeared: {', '.join(disappeared)}")
    commit_msg_lines.append(f"\nMax score drift: {max_drift:.1f}pts | Auto-merge: {auto_merge}")

    subprocess.run(["git", "commit", "-m", "\n".join(commit_msg_lines)], check=True)
    subprocess.run(["git", "push", "origin", branch], check=True)

    # Create PR via GitHub CLI
    pr_body = f"""## 🤖 Nightly Benchmark Data Update

**Date:** {datetime.date.today().isoformat()}
**Max score drift:** {max_drift:.1f} points
**Auto-merge:** {"✅ Yes (drift < {THRESHOLD_AUTO_MERGE}pts)" if auto_merge else "❌ No — please review"}

### Changes
{"".join(f"- ✨ **NEW**: {c['name']}\\n" for c in changes if c["type"] == "new_model")}
{"".join(f"- 📊 **Updated**: {c['name']} — {', '.join(f'{k}: {v[\"old\"]}→{v[\"new\"]}' for k,v in c.get('diffs',{}).items())}\\n" for c in changes if c["type"] == "updated")}
{"".join(f"- ⚠️ **Disappeared**: {c['name']}\\n" for c in changes if c["type"] == "disappeared")}

---
*Generated by LLM Atlas nightly pipeline. Review and merge or close.*
"""
    subprocess.run([
        "gh", "pr", "create",
        "--title", f"🤖 Data update {datetime.date.today().isoformat()} — {len(changes)} changes",
        "--body", pr_body,
        "--base", "main",
        "--head", branch,
    ], check=True)

    if auto_merge:
        log("  Auto-merging (drift within threshold)…")
        time.sleep(5)
        subprocess.run(["gh", "pr", "merge", "--auto", "--squash"], check=True)

    log(f"  PR created — branch: {branch} | auto-merge: {auto_merge}")
    return branch, auto_merge

# ── Discord Alert ─────────────────────────────────────────────────────────────

def send_discord_alert(changes, max_drift, pr_branch):
    if not DISCORD_WEBHOOK:
        log("  No Discord webhook set — skipping alert")
        return

    new_models  = [c["name"] for c in changes if c["type"] == "new_model"]
    big_changes = [c for c in changes if c["type"] == "updated" and
                   any(v["drift"] >= THRESHOLD_ALERT for v in c.get("diffs", {}).values())]

    lines = ["🚨 **LLM Atlas — Rankings Changed**", ""]
    if new_models:
        lines.append(f"✨ **New models detected:** {', '.join(new_models)}")
    if big_changes:
        for c in big_changes:
            lines.append(f"📊 **{c['name']}** moved significantly:")
            for k, v in c["diffs"].items():
                if v["drift"] >= THRESHOLD_ALERT:
                    dir_ = "📈" if v["new"] > v["old"] else "📉"
                    lines.append(f"  {dir_} {k}: {v['old']} → {v['new']} (Δ{v['drift']:.1f})")
    lines.append(f"\n🔗 https://llm-atlas.vercel.app")
    lines.append(f"📋 PR: https://github.com/{REPO}/pulls")

    payload = json.dumps({"content": "\n".join(lines)}).encode()
    req = Request(DISCORD_WEBHOOK, data=payload,
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        urlopen(req, timeout=10)
        log("  Discord alert sent ✓")
    except Exception as e:
        log(f"  Discord alert failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log("═══════════════════════════════════════")
    log("  LLM Atlas — Nightly Pipeline Starting")
    log("═══════════════════════════════════════")

    current_data = load_current()
    old_models   = current_data.get("models", [])
    log(f"  Loaded {len(old_models)} existing models from {DATA_FILE}")

    # Scrape all sources
    aa_models  = scrape_artificial_analysis()
    pwc_scores = scrape_papers_with_code()
    arena_elo  = scrape_lmsys_arena()

    # Merge scraped data into model records
    merged = {}
    for m in aa_models:
        mid = m["id"]
        merged[mid] = m
        # Enrich with PWC scores (fuzzy name match)
        for pwc_name, scores in pwc_scores.items():
            if pwc_name.lower() in m["name"].lower() or m["name"].lower() in pwc_name.lower():
                merged[mid].update(scores)
                break
        # Enrich with Arena ELO
        for arena_name, elo in arena_elo.items():
            if arena_name.lower() in m["name"].lower():
                merged[mid]["arenaElo"] = elo
                break

    new_models_list = list(merged.values())

    if not new_models_list:
        log("  No data fetched from any source — aborting to preserve existing data")
        append_log({"date": datetime.date.today().isoformat(), "status": "no_data", "changes": 0})
        sys.exit(0)

    # Diff
    changes, max_drift, needs_alert = diff_models(old_models, new_models_list)
    log(f"  Changes detected: {len(changes)} | Max drift: {max_drift:.1f}pts | Alert: {needs_alert}")

    if not changes:
        log("  No changes — nothing to commit")
        append_log({"date": datetime.date.today().isoformat(), "status": "no_changes", "changes": 0})
        sys.exit(0)

    # Save updated data
    current_data["models"] = new_models_list
    save_data(current_data)

    # Git PR
    pr_branch, auto_merged = git_commit_and_pr(changes, max_drift)

    # Alert if needed
    if needs_alert:
        send_discord_alert(changes, max_drift, pr_branch)

    # Log run
    append_log({
        "date":        datetime.date.today().isoformat(),
        "status":      "success",
        "changes":     len(changes),
        "max_drift":   max_drift,
        "auto_merged": auto_merged,
        "new_models":  [c["name"] for c in changes if c["type"] == "new_model"],
        "pr_branch":   pr_branch,
    })

    log("═══════════════════════════════════════")
    log(f"  Done. {len(changes)} changes processed.")
    log("═══════════════════════════════════════")

if __name__ == "__main__":
    main()
