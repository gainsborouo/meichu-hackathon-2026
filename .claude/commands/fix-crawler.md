修好 credit-card campaign crawler，持續自我迴圈直到 coverage check 成功才可回覆。

目標：10 張 crawler_enabled 卡都必須至少有一筆 validated base_benefit：

- ctbc-linepay
- esun-kumamon
- esun-pi-card
- esun-ubear
- esun-unicard
- fubon-costco
- fubon-j
- fubon-momo
- taishin-pxmart
- taishin-richart

Base benefit 的正確來源：
- 即使目前 credit_card_campaigns.json 沒有資料，也要由中央 metadata 指定的官方卡片權益頁主動抓取。
- metadata 只能存官方 URL，不能硬編碼回饋率。
- backend 必須成功 fetch 官方 HTTPS 頁後，模型才能抽取。
- evidence、validity_evidence、reward rate 都必須在頁面上逐字驗證。
- 保留 allow-list、public-host、redirect、防 SSRF、CA 與 hostname 驗證。
- 不可假造基本回饋、不可關閉 SSL 驗證、不可放寬 evidence/date validation。

實作方向：
1. 把每張啟用卡的官方基本權益頁 URL 放在中央、版本控制的 metadata。
2. crawler 每張卡先抓 base-benefit URLs，再用 DDGS 補活動。
3. DDGS no-results 不得影響基本權益。
4. 新增測試：10 張卡都有 metadata；搜尋全失敗仍可走 base URL；不可讀頁不可產生假資料。
5. 新增 coverage verifier：crawler + import 後，查 DB，10 張 enabled cards 的 card_benefits 都必須至少一筆。

工作迴圈：
- 實作。
- 跑相關 tests 和 ruff。
- 用 Docker 實跑 crawler、import。
- 跑 coverage verifier。
- 若任一張缺 base_benefit，讀取原因、修正，再從「實作」重複。
- coverage 成功前，不可問我問題、不可只給 proposal、不可說完成。
- 不要 commit、不要 push。
- 最後一定執行：docker compose -f compose.dev.yaml down