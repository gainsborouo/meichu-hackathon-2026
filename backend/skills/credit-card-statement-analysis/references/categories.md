# Spending categories

Canonical category names for the `category` field, with the merchants that most often
appear on Taiwanese card statements. Use the exact names in the left column — the
aggregation script groups on them verbatim, and `dining` vs `Dining` becomes two rows.

## Taxonomy

| Category | Covers | Common merchants (zh-TW / EN) |
|---|---|---|
| `dining` | Restaurants, cafés, bubble tea, food delivery | 星巴克, 麥當勞, 鼎泰豐, 路易莎, 50嵐, foodpanda, Uber Eats |
| `convenience` | Convenience stores — small, frequent baskets | 7-ELEVEN, 統一超商, 全家, 萊爾富, OK超商 |
| `groceries` | Supermarkets, wet markets, bulk stores | 全聯福利中心, 家樂福, 大潤發, 美廉社, Costco 好市多 |
| `transport` | Transit, taxis, ride-hailing, parking, tolls | 悠遊卡加值, 一卡通, 台北捷運, 台灣大車隊, Uber, 高鐵, 台鐵, 停車費, SUICA/PASMO top-ups |
| `fuel` | Petrol and charging | 台灣中油, 台塑石油, 全國加油站 |
| `online_shopping` | E-commerce marketplaces | 蝦皮購物, momo購物網, PChome, 博客來, Amazon, 淘寶 |
| `shopping_retail` | Physical retail, clothing, electronics | 統一時代, SOGO, 新光三越, UNIQLO, 燦坤, 全國電子 |
| `entertainment` | Games, cinema, concerts, ticketing, music | Steam, 威秀影城, KKTIX, TOWER RECORD, ARTIST SITE, 拓元售票 |
| `software_services` | Software, AI, cloud and media subscriptions | Anthropic/Claude, OpenAI, GitHub, Adobe, iCloud, Google One, Netflix, Spotify, KKBOX |
| `utilities_telecom` | Phone, internet, electricity, water, gas | 中華電信, 台灣大哥大, 遠傳電信, 台灣電力, 自來水事業處, povo / ahamo (JP SIM) |
| `healthcare` | Clinics, hospitals, pharmacies, optical | 診所, 醫院, 康是美, 屈臣氏 (when pharmacy), 藥局 |
| `beauty_personal` | Salons, cosmetics, personal care | 髮廊, 美容, 寶雅, 屈臣氏 (when general goods) |
| `travel_lodging` | Flights, hotels, tours, booking platforms | 長榮航空, 中華航空, Booking.com, Agoda, Airbnb, 雄獅旅遊 |
| `education` | Tuition, courses, books, exam fees | 補習班, 學費, Udemy, Coursera, 誠品 (when books) |
| `home` | Furniture, hardware, household goods | IKEA, 特力屋, 宜得利, HOLA |
| `insurance` | Premiums charged to the card | 國泰人壽, 富邦人壽, 新光人壽 |
| `financial_fees` | Annual fees, interest, late fees, FX fees | 年費, 循環利息, 違約金, 手續費, **國外交易服務費** |
| `pets` | Vets, pet supplies | 動物醫院, 寵物 |
| `charity` | Donations | 慈濟, 紅十字會, 捐款 |
| `other` | Real spending that fits nothing above | — |
| `uncategorized` | Merchant name too opaque to classify | 線上購物, bare acronyms, unreadable rows |

## Judgment calls that come up constantly

**`convenience` vs `groceries`.** 全家 and 7-ELEVEN are `convenience` even when someone
buys food there; 全聯 and 家樂福 are `groceries` even for a small basket. The split is
worth keeping because the behaviors differ — frequent NT$80 convenience runs and a
monthly NT$3,000 supermarket trip say different things about how a person shops.

**`dining` vs `groceries` for delivery.** foodpanda and Uber Eats are `dining` by default.
They do deliver groceries now, so if the description makes that explicit, follow the
description.

**Drugstores.** 屈臣氏 and 康是美 straddle `healthcare` and `beauty_personal`. Without more
detail, use `beauty_personal` — general-goods baskets are the common case. A row that
names a prescription or clinic belongs in `healthcare`.

**Fees are spending, but not consumption.** Annual fees and interest go in
`financial_fees`. The script totals them separately so the narrative can say "NT$1,200 of
this was the card's annual fee, not something you bought."

**Installments (分期).** Record the amount charged *this* statement, not the full purchase
price, with `type: "installment"`. The statement only bills this month's slice.

**Don't invent precision.** A merchant you can't place is `uncategorized`, not a plausible
guess. A category breakdown where 30% is honestly unknown is more useful than one that is
quietly wrong.

## Spending abroad

Foreign transactions arrive with a companion `國外交易服務費` row — categorize the purchase
on what was bought and the fee as `financial_fees`. Categorize by what the merchant *is*,
not where it is: a SUICA top-up in Tokyo is `transport` exactly like an 悠遊卡 top-up in
Taipei, and a Japanese SIM charge is `utilities_telecom`. Keeping them in their real
categories is what lets a month of travel show up as a spike in ordinary categories rather
than an opaque "travel" bucket.

`travel_lodging` stays for the travel itself — flights, hotels, tours.

## Subscriptions vs entertainment

`software_services` exists because recurring digital subscriptions behave differently from
a cinema ticket: they charge every month whether or not anyone uses them, which is exactly
what people want surfaced. A one-off game purchase on Steam is `entertainment`; a monthly
Claude or Adobe charge is `software_services`. When a streaming service could plausibly go
either way, prefer `software_services` — the point is to make the recurring column visible.

## Adding categories

Extending the list is fine when a statement clearly needs it — use a lowercase
`snake_case` name and keep it at the same level of granularity. Resist splitting too
finely: twenty categories with one transaction each tells the person nothing.
