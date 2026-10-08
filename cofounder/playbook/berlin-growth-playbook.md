# Berlin growth playbook (co-founder strategy)

This is the strategy the assistant follows in its weekly growth review (`sz growth`) and next-action suggestions. Edit it when you change direction; the assistant reads this file every week. Product facts live in `knowledge/settleezy.md`.

## What we're growing

| Part | Who it's for | How it makes money / growth |
|---|---|---|
| **Membership app** (savings at restaurants, cafés, grocery stores, activities, events + guides, expense & savings tracker, document vault, emergency numbers) | Students in Berlin, especially international students, new and already here | 30-day free trial → €40/semester or €70/year |
| **Buddy platform** (A-to-Z support for new incoming students, including accommodation support) | Berlin universities and their incoming international students | University partnerships; every incoming student meets Settleezy on day one → app trials |
| **Workshops** (portfolio, CV, more) | Members and students at partner universities | Reasons to join and stay; content and community |

## The flywheel

```
venues give member-only savings  ->  membership is worth far more than €40/semester
        ^                                                    |
        |                                                    v
venues get steady local student customers  <-  more students join (trial -> paid)
                                                             ^
              universities (Buddy platform) bring every new intake to Settleezy
```

Two engines feed the same membership:
1. **Supply:** more and better partner venues, so the app saves real money every week.
2. **Demand:** universities (Buddy platform), student communities and Instagram bring students into the free trial.

The key number is **trial → paid conversion**: a student who saves more than €40 in their first 30 days converts. Push new trial members to a first saving in week one (a café or grocery deal near their campus).

## Where the leads come from (and how to pitch them)

| Source | What it tells you | Pitch angle |
|---|---|---|
| **Groupon / vspots** Berlin venues | They already accept discounting to win new customers, but give a large share of the voucher price to the platform and mostly attract one-time bargain hunters | "Students who live here and come back every week, through a member discount you control" |
| **Top10 Berlin** Club / Card partners | Trendy venues that like curated, member-only perks (2-for-1) | "A member-only perk for international students choosing their regular spots right now" |
| **UNiDAYS / Student Beans** brands | National brands with a student budget and verification already set up | "Reach students in Berlin inside an app they open to save every day, plus welcome-week activations" |
| **Universities / international offices** | They own the arrival moment and are responsible for incoming students | The **Buddy platform**: A-to-Z support for new incoming students, including accommodation support |
| **Housing / service providers** | Same students, same moment | Accommodation support inside the Buddy platform; co-marketing |
| **Grocery stores, Spätis, organic markets near campuses** | Everyday spending, where savings feel real | "Be the student's default store near campus" |

Lead score = student relevance of the category (food, groceries, universities highest) × proven discount appetite (number of competitor platforms they're on) × reachability (email, Instagram, website).

## The semester calendar drives everything

| When | What students are doing | Our focus |
|---|---|---|
| **May–July** | Admission letters, visa, searching for rooms from abroad | Sign Buddy platform universities for the winter intake; accommodation support; pre-arrival content |
| **Mid-Aug–mid-Oct** (peak) | Arriving, registering, first weeks in the city | Buddy platform live for new intakes, welcome-week stands, app trials, daily Instagram reels, referral push |
| **Mid-Oct–Dec** | Settling in, social life, first budget shock | Venue savings, events, expense tracker ("see what you saved"), first workshops; convert trials before day 30 |
| **Dec–Jan** | Exams, holidays, prices change every January | CV / portfolio workshops before internship season, money-saving content, annual-plan offer |
| **Feb–April** | Summer-semester arrivals (smaller wave) | Repeat the arrival playbook; sign venues and universities for the autumn |

Sign venues **before** the August–October wave, so the app is full of savings when students arrive. Sign universities for the Buddy platform **by early summer** for the winter intake.

## Channels, ranked

1. **Universities via the Buddy platform.** One partnership reaches every incoming intake at the moment they need help most.
2. **Venue co-promotion.** Every partner venue promotes Settleezy at least once (story, table card with a QR code, counter sign).
3. **Referrals.** For example, give a free month to both the referrer and the new member. Students arrive in friend groups and WhatsApp groups.
4. **Instagram + TikTok reels.** "What €10 buys a student in Berlin", "I saved €X this month with Settleezy" (from the savings tracker), partner spotlights, "free things this weekend", document and emergency-number tips for new arrivals, workshop clips. Post 4–5 per week at peak.
5. **Workshops.** CV and portfolio sessions bring students in, build trust and give content for social media.
6. **Communities.** Reddit, Facebook and WhatsApp groups for incoming students by nationality and by university. Help first, always disclose.
7. **Ambassadors.** One student per university and per big nationality community, paid in free membership plus perks.

## Metrics the assistant tracks

- Outreach reply rate by partner type, weekday and send hour (from your mailbox)
- Venues signed per week, by category; venues that actually promoted us
- Universities in conversation / signed for the Buddy platform
- Instagram followers, reach, engagement rate, website clicks
- Calendly bookings (student calls, partner calls)
- New competitor listings per week, by category (shows where Berlin venues are discounting)
- *(Add as soon as you can export them from the app)* trials started, trial-to-paid %, savings per member in the first 30 days, churn at renewal, referrals per member, workshop sign-ups

## Standing experiments (the weekly review proposes new ones)

1. **"Commission-free regulars" angle vs. "new international students" angle** in venue emails: compare reply rates after 20 sends each.
2. **First saving in week one:** nudge new trial members to one specific deal near their campus; measure trial → paid.
3. **Instagram DM first, email second** for venues with an active Instagram account.
4. **Welcome-week bundle:** 3 venues + Settleezy trial on one QR flyer, with a unique code per university.

## Rules (legal and reputation)

- **Drafts only.** The assistant never sends; you review every email.
- **Cold email in Germany (UWG §7):** B2B email without consent is only safe when it's clearly relevant to the recipient's business, personal, and low volume. Prefer warm channels (contact form, Instagram DM, phone, an introduction, visiting in person) for small venues. Always include an easy opt-out, and stop at the first "no".
- **GDPR:** lead data comes from public business pages (Impressum, website). Store only business contact details, note the source, and delete on request. Don't scrape personal social profiles.
- **Scraping:** the scraper respects robots.txt, identifies itself, rate-limits, and only reads public listing pages. Check each site's terms of service; if a site forbids automated access, set `enabled = false` for it in `competitors.toml` and track it manually.
- **Honesty:** never fake reviews, scarcity or partner logos, and only name partners and universities that agreed to be named.
