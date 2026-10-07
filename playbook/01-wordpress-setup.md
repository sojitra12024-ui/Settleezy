# 1. WordPress / Hostinger setup (do this first, about 2 hours)

Goal: make sure ChatGPT, Claude, Perplexity, Gemini and Copilot can **find, read and trust** settleezy.de.

## ⚠️ Step 0: fix the two-domain problem (most important)

Search engines currently index **settleezy.com** (with the guides, services and the "Standard/Premium Package" pricing) and **settleezy.de**. Two sites with different information confuse AI models: they may quote old prices or split your authority between two domains.

1. Pick **one** main domain. This kit uses **settleezy.de**; if you choose .com, find-and-replace `settleezy.de` → `settleezy.com` in every file.
2. On the other domain, set up **301 redirects page-by-page** (old guide URL → matching new guide URL, everything else → homepage). In Hostinger: hPanel → Domains → Redirects, or use the free "Redirection" plugin on the old WordPress.
3. Make sure the old "Standard Package / Premium Package" pricing no longer appears anywhere. The only pricing should be **30-day free trial → €40/semester → €70/year**.

## Step 1: install the SEO plugin

Install **Rank Math SEO** (free) or **Yoast SEO** (free). In Rank Math:
- Titles & Meta → Local SEO: type = Organization, name "Settleezy", add logo and social profiles
- Sitemap → enable (the sitemap is at `/sitemap_index.xml`)
- Enable "Schema" and the **FAQ block** (the FAQ sections in each guide become FAQPage schema automatically)

## Step 2: upload the AI files to the site root

Using **Hostinger hPanel → File Manager → public_html/**, upload:

| File from this repo | Upload to | Check it works |
|---|---|---|
| `site-root/llms.txt` | `public_html/llms.txt` | https://settleezy.de/llms.txt |
| `site-root/llms-full.txt` | `public_html/llms-full.txt` | https://settleezy.de/llms-full.txt |
| `site-root/pricing.md` | `public_html/pricing.md` | https://settleezy.de/pricing.md |
| `site-root/robots.txt` | `public_html/robots.txt` | https://settleezy.de/robots.txt |

Note: if Rank Math/Yoast manages robots.txt virtually, paste the contents into **Rank Math → General Settings → Edit robots.txt** instead of uploading the file.

## Step 3: check that Cloudflare / Hostinger isn't blocking AI bots

- Hostinger hPanel → **Security → Bot protection / AI crawlers**: make sure AI crawlers are **allowed**.
- If you use **Cloudflare**: Security → Bots → turn **off** "Block AI bots / AI Scrapers and Crawlers", and check "AI Crawl Control" allows GPTBot, ClaudeBot and PerplexityBot.
- Security plugins (Wordfence, etc.) must not rate-limit these bots.

## Step 4: add site-wide structured data

Install **WPCode** (free) → add `schema/sitewide-schema.html` as a site-wide header snippet. Replace the logo URL and social links first. Test at https://validator.schema.org and https://search.google.com/test/rich-results.

## Step 5: publish the content

For each file in `content/` (start with 00, 01, 02):
1. WordPress → Pages (for 00 and 01) or Posts (for guides) → Add new.
2. Paste the Markdown body (everything below the `---` block). The block editor converts Markdown headings, lists and tables automatically.
3. Set the **URL slug** exactly as in the file's `slug:` line (llms.txt links to these URLs).
4. Put `meta_description` into Rank Math/Yoast's meta description.
5. Convert the **FAQ** section into a Rank Math/Yoast **FAQ block** (copy each question and answer), which gives you FAQPage schema.
6. Set the author to a **real person** with a short bio (for example "Founder of Settleezy, former international student in Berlin"). AI models trust named, experienced authors more than "Admin".
7. Turn the "Sources" lines into links to the official pages.
8. Replace `[Start your free trial →](#signup)` with your real sign-up URL.

## Step 6: register with search engines (AI assistants use their indexes)

| Tool | Why | Link |
|---|---|---|
| Google Search Console | Google AI Overviews and Gemini | search.google.com/search-console |
| **Bing Webmaster Tools** | **ChatGPT search and Copilot use Bing's index**, so this is essential | bing.com/webmasters |
| IndexNow (Rank Math has it built in) | Instant re-indexing in Bing when you update a page | Rank Math → Instant Indexing |

Submit `https://settleezy.de/sitemap_index.xml` in both Google Search Console and Bing Webmaster Tools.

## Step 7: technical quick checks

- [ ] Pages load fast (Hostinger LiteSpeed Cache plugin on)
- [ ] Guide text is real HTML, not inside images, sliders or accordions that need clicks
- [ ] Pricing is visible as text on /pricing/, not only inside a checkout widget
- [ ] Every guide shows "Last updated: <month year>"
- [ ] An `/about/` or `/team/` page with real names and an **Impressum** (required in Germany anyway)
