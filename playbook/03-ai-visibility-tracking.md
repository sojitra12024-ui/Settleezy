# 3. Track whether AI assistants mention Settleezy

Once a month, ask each assistant the questions in `ai-visibility-prompts.csv` and record the results. Use a **logged-out / private window** (or a fresh account) so your own history doesn't bias the answers.

Assistants to test: **ChatGPT** (with search), **Claude** (web search on), **Perplexity**, **Google** (AI Overview + Gemini), **Microsoft Copilot**.

For each prompt, record:
- `mentioned`: does the answer name Settleezy? (yes/no)
- `cited`: does it link a settleezy.de page? Which one?
- `accurate`: are the price and free trial correct? (catches old .com pricing)
- `competitors`: which other sites or brands were cited instead?

## What to do with the results

| You see | Do |
|---|---|
| Not mentioned, competitor page cited | Read that page. Is it more specific, more recent, or does it have a table you lack? Improve your guide to beat it. |
| Mentioned with the wrong price or old packages | Make sure settleezy.com redirects and the old pricing is gone; update llms.txt and pricing.md; re-submit in Bing via IndexNow |
| Cited for one topic only | Link that guide to your other guides, and build more off-site mentions for the missing topics |
| Nothing changes after 3 months | Focus more on off-site work (Reddit, universities, partners); AI answers lag behind new websites |

## Realistic timeline

- **Weeks 1–4:** pages indexed by Google and Bing. Perplexity and ChatGPT search can start citing individual guides for specific questions.
- **Months 2–6:** with steady Reddit, university and partner mentions, Settleezy starts appearing in broader answers ("how to save money in Berlin as a student").
- **6–12 months+:** models trained on newer web data start to "know" Settleezy without searching.

No one can guarantee AI assistants will recommend a specific brand. Steady, accurate, well-sourced content and real third-party mentions are what move the numbers.

## Traffic from AI assistants

In Google Analytics 4, create an exploration filtered by session source containing:
`chatgpt.com`, `chat.openai.com`, `perplexity.ai`, `claude.ai`, `gemini.google.com`, `copilot.microsoft.com`, `bing.com`.
Many AI links also carry `utm_source=chatgpt.com`.
