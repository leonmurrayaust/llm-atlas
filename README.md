# LLM Intelligence Atlas

> The world's most comprehensive interactive LLM benchmark visualization.
> **Live at:** https://llm-atlas.vercel.app

## Architecture

```
GitHub Repo (you own)
    ↓ auto-deploy on push
Vercel (free hosting)
    ↓ serves
llm-atlas.vercel.app (or your custom domain)

GitHub Actions (runs nightly at 2am UTC)
    ↓ scrapes
Artificial Analysis + Papers With Code + LMSYS Arena
    ↓ opens PR
You approve (or auto-merges if drift < 2pts)
    ↓ Vercel redeploys
Site updates automatically
```

## What the Agent Does Every Night

1. Scrapes Artificial Analysis leaderboard for Intelligence Index + Speed + Cost
2. Scrapes Papers With Code for GPQA, MATH-500, HumanEval, SWE-bench, AIME scores
3. Scrapes LMSYS Chatbot Arena for ELO scores
4. Diffs new data against existing `data/models.json`
5. If changes found → opens a GitHub PR with a detailed changelog
6. If drift < 2 points → auto-merges (zero human involvement)
7. If drift > 5 points OR new model detected → sends Discord alert
8. Vercel sees the merge → auto-redeploys the site

**Your ongoing work: ~0 minutes/week for minor updates. ~5 minutes to review PRs for big changes.**

## Setup

### Required GitHub Secrets (Settings → Secrets → Actions)

| Secret | Value | Required |
|--------|-------|----------|
| `DISCORD_WEBHOOK` | Your Discord channel webhook URL | Optional but recommended |

### Custom Domain (Namecheap → Vercel)

1. In Vercel: Settings → Domains → Add → enter your domain
2. Vercel gives you two DNS records
3. In Namecheap: Domain → Advanced DNS → Add the records
4. Wait 2–10 minutes → live

### Manual Pipeline Trigger

Go to: GitHub → Actions → "Nightly Model Data Pipeline" → "Run workflow"

Useful when a major model drops and you want to update immediately.

## Data Schema

Each model in `data/models.json`:

```json
{
  "id": "unique-slug",
  "name": "Display Name",
  "maker": "Company",
  "releaseDate": "YYYY-MM",
  "intelligence": 62,
  "gpqa": 84.0,
  "mmluPro": 79.1,
  "humanEval": 90.2,
  "math": 91.8,
  "swe": 63.8,
  "aime": 86.7,
  "arenaElo": 1380,
  "speed": 148,
  "context": 1000,
  "costInput": 1.25,
  "costOutput": 5.0,
  "isFree": true,
  "openWeights": false,
  "freeVia": "Google AI Studio",
  "tier": "Frontier"
}
```

## Roadmap

- [ ] Phase 3: Blind auction ad system + Stripe
- [ ] Phase 4: REST API (`GET /v1/models`) at $49/mo
- [ ] Phase 5: Community submissions + upvotes
- [ ] Phase 6: Embed marketplace

## Tech Stack

- **Frontend:** Single-file HTML/CSS/JS (no build step, no framework)
- **Hosting:** Vercel (free)
- **Data:** JSON flat file in repo (upgrading to Supabase in Phase 3)
- **Pipeline:** GitHub Actions (free for public repos)
- **Payments:** Stripe (Phase 3)

---

Built with 🔥 by leonmurrayaust + Claude
