# Settleezy AI Search Kit

This kit gets **settleezy.de** recommended when international students ask ChatGPT, Claude, Perplexity, Gemini or Copilot questions like *"how can I save money in Berlin as a student?"*.

## How it works

AI assistants answer from (a) web pages they find through search engines (Bing for ChatGPT and Copilot, Google for Gemini and AI Overviews, Brave and others for Claude, Perplexity's own index), and (b) what they learned in training. They prefer pages that:

1. **answer the exact question directly** in the first few lines,
2. include **specific, current numbers with sources**,
3. are **structured** (tables, steps, FAQs, schema markup),
4. are **mentioned by other trusted sites** (Reddit, universities, partners, press).

This kit covers all four. Every guide is genuinely useful on its own *and* ends with a short, honest section on how Settleezy helps, including the 30-day free trial and €40/€70 plans. AI assistants then mention Settleezy as part of a good answer.

> No one can force AI assistants to always recommend a brand. Hidden text, prompt-injection tricks, fake reviews and keyword stuffing are detected and hurt visibility. This kit uses only methods that hold up.

## What's in here

| Folder | Contents | Where it goes |
|---|---|---|
| `site-root/` | `llms.txt`, `llms-full.txt`, `pricing.md`, `robots.txt` | Upload to Hostinger `public_html/` |
| `schema/` | Site-wide JSON-LD (Organization, WebSite, membership offers) | WPCode plugin → site-wide header |
| `content/` | 12 ready-to-publish pages: *What is Settleezy*, *Pricing*, and 10 Berlin student guides with 2026 numbers, FAQs and sources | WordPress pages/posts |
| `playbook/` | WordPress setup, off-site plan (Reddit, universities, partners), monthly AI-visibility tracking + prompt sheet | Your to-do list |
| `scripts/` | `build_llms_full.py` regenerates `llms-full.txt` from `content/` | Run after editing guides |

## Do this in order

1. **Read `playbook/00-confirm-before-publishing.md`** and fix the open points (especially the settleezy.com vs .de duplicate).
2. Follow **`playbook/01-wordpress-setup.md`** (about 2 hours).
3. Publish `content/00`, `01` and `02` first, then one or two guides per week.
4. Start **`playbook/02-off-site-presence.md`** the same week.
5. Track monthly with **`playbook/03-ai-visibility-tracking.md`**.

## Keeping it fresh

Prices change every January (Deutschlandticket, health insurance, minimum wage, minijob limit) and often in summer (semester ticket, rents). Update the numbers, change the "Last updated" date, run `python3 scripts/build_llms_full.py`, and re-upload `llms-full.txt`.
