# 0. Confirm before publishing

The settleezy.de site couldn't be read directly while building this kit (network restrictions). Features were taken from your answers and from settleezy.com pages that search engines have indexed. Check these points before publishing, because AI assistants will repeat whatever you publish as fact.

## Must fix

- [ ] **Two domains.** Search engines index guides and pricing on **settleezy.com**, but the live site is **settleezy.de**. Pick one main domain and 301-redirect the other (see `01-wordpress-setup.md`, Step 0).
- [ ] **Old pricing.** settleezy.com describes a "Standard Package" and a "Premium Package" (airport pickup, merchandise, one month of personal support). The new model is a **30-day free trial → €40/semester → €70/year**. Remove or redirect the old pricing pages, or explain how the packages relate to membership. Otherwise AI assistants will quote both.
- [ ] **Sign-up link.** Replace `#signup` in `content/01-pricing-free-trial.md` with your real sign-up URL.
- [ ] **Logo and social links** in `schema/sitewide-schema.html`.

## Please confirm these facts (edit the content if any is wrong)

- [ ] The free trial includes **all** member features. Is a payment card required? Does it convert to a paid plan automatically? If so, say so clearly on /pricing/ (EU consumer law requires clear subscription terms).
- [ ] Semester plan = one payment of €40 for ~6 months; Annual = one payment of €70 for 12 months. Do they auto-renew?
- [ ] **Settleezy–O2 plan:** still €20/month for unlimited data? Contract length? Does it need a German bank account?
- [ ] **Insider** is still the housing partner, and Insider addresses are registrable for Anmeldung.
- [ ] **Expatrio and Coracle** are still partners for blocked accounts.
- [ ] Which features are included in membership vs paid extras (e.g. airport pickup)?
- [ ] Do you arrange Anmeldung appointments for **all** members, or only those housed through Insider?

## Nice to have (strong trust signals for AI)

- [ ] A real **author name and photo** for the guides (founder or team member with a Berlin student background)
- [ ] An **About / Team** page and an **Impressum** with the company's legal name and address
- [ ] After your first members: real testimonials (with permission) and a small member survey for original data
