# Memo: What the Combined Olist Data Tells Us

**To:** Marketplace leadership (commercial, marketing, operations)
**From:** Data team (mishra.sn@northeastern.edu)
**Date:** 2026-10-31
**Subject:** Findings from joining the online store and the sales CRM

We combined two Olist systems, the online store and the sales CRM that signs new
sellers, into one warehouse so the same seller, customer, and order can be
followed across both. Every number below comes from that warehouse. Here is what
we found, what we recommend, and what to keep in mind.

## What we found

**1. The two systems join on the seller, and that is where the gap shows.**
The store and the CRM meet at the seller. Of the 842 sellers the CRM signed, only
380 ever sold anything in the store. The other 462 signed up but never listed a
single product, so they brought in no sales at all. More than half of signed
sellers are not selling.

**2. A "customer" in the raw data is really one order, not one person.**
The store gives each order its own customer code. There are 99,441 of those codes
but only 96,096 real people behind them. Almost everyone buys once: only 3.1% of
people ever place a second order, and the most active buyer placed 17. We rebuilt
the data around the real person so repeat behavior can be measured correctly.

**3. Some channels bring sellers that actually sell; others do not.**
From 8,000 leads the CRM closed 842 deals worth about R$775,800 in store sales.
Organic search brings the most (2,296 leads, 271 deals). Among the paid channels,
paid search converts best, turning about 12 in 100 leads into signed sellers.
Email, display, and "other publicities" convert poorly and produce very few
selling sellers (other publicities signed 3 sellers and none of them ever sold).

**4. The warehouse loads in pieces and still rebuilds to the exact same answer.**
We loaded the data in two passes, like a real daily feed. Between the two,
2,505 orders arrived as updated versions (2,504 of them with a changed status,
for example from shipped to delivered), and the warehouse recorded each change
with its history intact. When we rebuilt the whole thing from scratch, every one
of the 29 tables came out identical. The numbers are reproducible.

**5. The money mostly ties out, but delivery is the weak spot.**
Payments match the order total for 98,416 of 99,441 orders, about 99%. The rest
are small and explainable: 775 orders have no line items recorded, 249 are off by
more than one Real (vouchers, installment rounding, partial refunds), and 1 has
no payment on file. The real problem is delivery. About 6.8% of delivered orders
arrive late, and late orders are punishing: they average a 2.27 star review versus
4.29 for on-time orders. Late delivery is strongly associated with low review
scores.

## What we recommend

- **Rescue the 462 signed-but-not-selling sellers.** This is the biggest easy
  win. A short onboarding follow-up (help them list their first product) could
  convert sign-ups the CRM already paid to win.
- **Shift seller marketing toward what converts.** Keep organic search for
  volume, lean into paid search for its strong conversion, and reduce spend on
  email and display, which bring few sellers that sell.
- **Attack late deliveries.** Cutting the 6.8% late-delivery rate is a direct way
  to lift review scores, since late orders average about two stars lower.
- **Give buyers a reason to come back.** With 97% of people buying only once, even
  a small lift in repeat purchases could add meaningful revenue.

## What to keep in mind

- This is Olist's public sample data, shared under a non-commercial license
  (CC BY-NC-SA 4.0). It is for analysis and learning, not for commercial resale.
- The order history runs out at the end of August 2018 (September and October
  2018 hold only a handful of orders). Some sellers signed near the end simply had
  no time to start selling before the data stops, which explains part of the
  signed-but-not-selling gap.
- The repeat-buyer rate of 3.1% is genuinely low and worth watching, though some
  of it may reflect how the sample was collected.
- The findings above run on our local warehouse. The same pipeline is written to
  run on Snowflake, but that run has not been done yet.
