# 最佳一刷 / Swipe Right Browser Extension

WXT + Vue + TypeScript，設定輸出 Chrome / Firefox Manifest V3。使用者先透過 Firebase Authentication 使用 Google 登入；偵測支援頁面後，extension 取得商品和正數金額，並在可確認時檢查信用卡付款選項，再透過 background 取得推薦。Google 登入會連線到 Firebase，推薦則由 background 帶 Firebase ID token 呼叫後端取得。PChome 依產品決定在購物車頁預設可刷卡；其他平台仍必須先確認信用卡可用。

**已通過型別檢查、54 個單元測試與 Chrome / Firefox MV3 打包；購物頁偵測曾通過 Chrome / Zen 驗證。** Firebase 登入已在 Chrome 端到端驗證通過（token 驗簽、後端建立使用者、取得推薦）。推薦已改接 `POST /api/v1/recommendations/stream`（SSE），傳輸層與解析已用真實後端回應驗證；完整端到端推薦仍待活動資料更新後複驗（詳見下方「已知限制」）。驗證環境為 Node.js 24.21.0、npm 11.19.0。Adapters 依使用者登入後儲存的三站購物車／付款頁 DOM 建立；測試 fixtures 只保留去識別化的結構和合成商品資料，原始 HTML 未加入 repository。

## 安裝與啟動

需要 Node.js 22.18+ 與 npm。在 repository 根目錄執行：

```sh
cd browser_extension
npm ci
npm run dev
```

Firefox 開發模式（本專案預設用 Zen）：

```sh
npm run dev:firefox
```

已提供 `package-lock.json` 固定依賴版本；使用 `npm ci` 安裝。需要更新依賴時，才使用 `npm install` 並保留 lockfile 變更。

驗證與正式打包：

```sh
npm run typecheck
npm test
npm run build
npm run build:firefox
```

- Chrome：進入 `chrome://extensions`，開啟開發人員模式，載入未封裝項目，選 `.output/chrome-mv3/`。
- Zen：進入 `about:debugging#/runtime/this-firefox`，載入暫時附加元件，選 `.output/firefox-mv3/manifest.json`。`wxt.config.ts` 的 Firefox binary 預設指向 Zen；可用 `FIREFOX_BINARY` 覆寫。
- 如果 Zen 顯示 `background.service_worker is currently disabled. Add background.scripts.`，代表誤選了 `.output/chrome-mv3/manifest.json`。重新執行 `npm run build:firefox`，移除載入失敗或舊的暫時附加元件，再明確選擇 `.output/firefox-mv3/manifest.json`；Firefox build 的 manifest 會使用 `background.scripts`。
- 驗證推薦需要在支援的購物網站結帳頁進行，並且後端可用、帳號已新增持有的信用卡。
- 在真實網站測試前，確認瀏覽器已授予 extension 對該網站的存取權；重新載入 extension 後，重新整理已開啟的結帳頁。

## Firebase Google 登入

第一次打開 extension popup 時，必須點「使用 Google 登入」。Chrome 與 Zen 都由 `browser.identity.launchWebAuthFlow()` 開啟 Firebase Hosting 上的登入頁；互動式 OAuth 必須由使用者操作觸發，不能在 extension 安裝時自動彈出。

公開的 Firebase Web config 位於 `lib/firebase-config.ts`，內容與 `frontend/src/firebase.ts` 相同。這些欄位用來識別 Firebase Web App，不是後端密鑰。Hosted helper 會從 Firebase Hosting 保留的 `/__/firebase/init.json` 取得同一專案的公開設定，不再保存第二份 config。Firebase Admin service-account JSON 含有 private key，僅供 backend 驗證 ID token，禁止複製到 extension、Firebase Hosting 或 repository。

登入 helper 原始檔位於 `auth_host/`。**這頁必須先部署，否則登入一定失敗。** 未部署時 `launchWebAuthFlow` 打開的是 Firebase Hosting 的 404 頁，標題為 `Site Not Found`；這不是 extension 程式錯誤，而是 `AUTH_HELPER_URL` 指向的頁面不存在。第一次部署：

```sh
cd auth_host
npx firebase-tools login
npx firebase-tools deploy --only hosting
```

部署後用以下指令確認回應為 `200`（`404` 代表尚未部署成功）：

```sh
curl -o /dev/null -w '%{http_code}\n' https://meichu-2026.firebaseapp.com/extension-auth.html
```

helper 使用 `signInWithRedirect` 在同一個視窗往返 Google，而不是 `signInWithPopup`：`launchWebAuthFlow` 開出來的視窗內再開巢狀 popup 通常會被瀏覽器阻擋。frontend 在一般網頁分頁中沒有這個限制，所以 `frontend/src/components/SiteHeader.vue` 仍使用 popup。

`firebase/auth/web-extension` 沒有 export `signInWithPopup` 或 `signInWithRedirect`，因此 extension 端只能用 `signInWithCredential` 搭配這個 hosted helper；直接從 extension 打 Google OAuth 會因 `chromiumapp.org` redirect URI 未註冊而得到 `redirect_uri_mismatch`。

`auth_host/firebase.json` 的 CSP 必須允許 `https://apis.google.com`，Firebase 的 redirect 流程會從該網域載入 `js/api.js`。

Google provider 必須在 Firebase Authentication 啟用。目前專案已確認 provider 啟用，且 Firebase Hosting default site 尚未部署其他內容。已授權網域目前為 `localhost`、`meichu-2026.firebaseapp.com`、`meichu-2026.web.app`、`100.64.0.7`。

## 後端契約：POST /api/v1/recommendations/stream

推薦來自 `POST /api/v1/recommendations/stream`（`backend/app/api/v1/routes/recommendations.py`）。它以 **Server-Sent Events** 回覆，不是單次 JSON：後端會跑模型並比對官方頁面，可能需要一到兩分鐘，所以進度會先到、結果最後到。

Request body 對應後端的 `RecommendationRequest`：

```json
{
  "product_name": "無線耳機、手機保護殼",
  "store_name": "shopee",
  "price": 2580,
  "currency": "TWD",
  "locale": "zh-TW"
}
```

- `product_name: string`：結帳頁的商品名稱，多品項以頓號連接。上限 200 字，超過由 `lib/backend.ts` 截斷，避免後端回 422。
- `store_name: string`：送平台 slug（`momo` / `shopee` / `pchome`）。後端用 `_store_matches()` 比對活動的 platform 別名，而每個 slug 都是自己的別名（`backend/app/services/reward_rules.py` 的 `_PLATFORM_ALIASES`），所以直接送 slug 即可命中。
- `price: number`：畫面上的最終應付金額，必須大於零。
- `currency`：固定 `TWD`。`locale`：固定 `zh-TW`，決定後端產生的文案語言。

**request body 不含 `userId`。** 後端的 `get_current_user`（`backend/app/api/deps.py`）從已驗證 ID token 的 `sub` claim 取得使用者，不讀 body，所以沒有可偽造的身分欄位。每次 request 必須帶：

```http
Authorization: Bearer <Firebase ID token>
```

後端用 `google.oauth2.id_token.verify_firebase_token` 驗證，audience 為 `firebase_project_id`（預設 `meichu-2026`，與 `lib/firebase-config.ts` 的 `projectId` 相同）；驗證失敗回 401。

### SSE 事件

```
event: searching
data: {"stage": "preprocessing", "mode": "no_registration", "now_candidates": 0, "future_candidates": 0}

event: recommendation
data: {"mode": "no_registration", "best_now": {...}, "wait_suggestion": null, "explanation": null}

event: done
data: {}
```

- `searching`：進度。`stage` 會是 `preprocessing`、`live_card_lookup`、`reprocessing` 或 `official_verification`；面板依 stage 顯示對應說明，未知的 stage 一律忽略而不顯示原始字串。
- `recommendation`：結果，取 `best_now`。
- `error`：後端自己的失敗（模型超時、答案不可用），`message` 直接當錯誤訊息。
- `done`：串流結束。

`lib/backend.ts` 會緩衝跨 chunk 的事件、跳過 keep-alive 註解與畸形 frame，並把整個串流收斂成一個結果。

`best_now` 對應後端的 `BestNow`：

```json
{
  "card": { "id": "…", "bank_name": "玉山", "name": "Unicard" },
  "sale_id": "…",
  "campaign_title": "一般消費回饋",
  "estimated_reward_twd": 77.4,
  "rate_display": "3%",
  "cap_description": "上限 500 點",
  "requires_registration": false,
  "registration_url": null,
  "reason": "一般消費 3% 回饋",
  "verification_status": "verified",
  "official_sources": [{ "title": "玉山銀行", "url": "https://…" }]
}
```

- 卡片以 `bank_name` + `name` 顯示（例如「玉山 Unicard」）；英文介面優先用 `issuer_en` / `name_en`，沒有翻譯時回退中文。**後端沒有卡號末四碼**，資料模型裡不存在該欄位。
- `candidate_type` 區分 `base_benefit`（卡片固定回饋）與 `campaign`（限時活動）；固定回饋會另外標示，避免被當成限時活動。對應的 id 只有一個有值：活動是 `sale_id`，固定回饋是 `benefit_id`。
- `verification_status` 為 `unverified` 時，面板明確標示活動內容未經官方頁面驗證，不把它當成確定的事實。
- `requires_registration` 為 true 且有 `registration_url` 時提供登錄連結，以新分頁開啟，不打斷正在進行的結帳。
- `best_now` 為 `null` 代表持有的卡片中沒有符合此購物條件的有效優惠；此時採用後端的 `explanation` 當說明，並視為「卡包沒有可用卡片」而不是失敗。
- `wait_suggestion` 目前不使用：它是「等活動開始更划算」的提議，且建立行事曆事件是另一個需要使用者確認的步驟。
- `requestId` 不送給後端，僅用於 content script 與 background 之間比對回應是否屬於當前結帳狀態；`lib/api.ts` 負責這層檢查。

### Timeout 層級

後端最壞情況是「即時查詢官方頁面」加上模型推理：`live_refresh.LOOKUP_TIMEOUT_SECONDS`（150s）+ `recommendation_agent.DEFAULT_TIMEOUT_SECONDS`（120s）= 270s。三層 timeout 必須遞增，否則會在後端仍可能回答時就放棄：

```
lookup 150s + model 120s = 270s  <  fetch abort 285s（lib/backend.ts）  <  runtime message 295s（lib/api.ts）
```

`live_card_lookup` 只在資料庫沒有該使用者持卡的現行候選時觸發，所以多數請求快得多；但只要觸發，時間就會拉到數分鐘，面板會在該階段明確說明。

付款方式只作為 extension 本地的觸發條件，不包含在 request。即使當前選中轉帳，只要仍有可用信用卡選項，就會推薦。沒有信用卡、信用卡停用、無法確認付款選項、商品空白、金額無效或有歧義時，不觸發。PChome 是明確例外：在付款方式尚未出現的購物車頁預設可刷卡並先提供推薦。

Content script 傳給 background 的 runtime message 只有 `requestId / platform / product / payable`，不接觸登入憑證。Background 驗證訊息來源後才取得 ID token 並轉成上述 body。

後端網址由 `lib/backend-config.ts` 決定，預設 `http://localhost:8000`，以建置時環境變數 `WXT_BACKEND_URL` 覆寫：

```sh
WXT_BACKEND_URL=https://api.example.com npm run build
```

同一個變數也會決定 `wxt.config.ts` 產生的 `host_permissions`；兩者必須一致，否則 `fetch` 會在執行期被瀏覽器阻擋。改預設值時要同時改 `lib/backend-config.ts` 的 `DEFAULT_BACKEND_URL` 與 `wxt.config.ts` 的 `backendOrigin()` fallback。

`host_permissions` 只含 Firebase Authentication 與設定的 backend 網域；沒有 Admin private key、持卡資料儲存或全站存取權限。

## 使用者設定：需登錄的活動

popup 在已登入時顯示一個 toggle，對應後端 `GET` / `PATCH /api/v1/me` 的
`registration_campaigns_enabled`（`backend/app/schemas/db.py` 的 `UserRead` / `UserUpdate`）。

這是**全域設定**，不是每次推薦的選項：後端一次只會考慮「需登錄」或「免登錄」其中一種
（`purchase_recommendation.py` 依此決定 `mode`），開啟後由後端負責在需要登錄時通知使用者。

實作上的三個取捨：

- **token 留在 background。** popup 不直接呼叫後端，而是透過 `settings:get` /
  `settings:set-registration-campaigns` 兩個訊息，和推薦走同一條認證路徑。
- **值未知時不顯示 toggle。** 預設關閉的 checkbox 會錯誤陳述帳號的實際設定，所以讀到
  之後才出現。
- **渲染伺服器回傳的值，而不是使用者點的值。** `PATCH` 會回完整的 `UserRead`，所以
  畫面反映實際儲存結果；失敗時把 checkbox 還原，因為伺服器端並未改變。

## Google Calendar scope

登入時的同意畫面會一併要求 `https://www.googleapis.com/auth/calendar.events`（`auth_host/public/extension-auth.js`）。

**但這還不足以連上行事曆。** 後端的 `POST /me/calendar/connect` 要的是 OAuth **authorization code**，由它自己去換 refresh token（`backend/app/services/google_calendar.py` 的 `exchange_code`）。extension 走的是 Firebase `signInWithRedirect`，拿到的是 id_token / access_token，**不會產生 authorization code**。

所以目前的狀態是：使用者已對 calendar.events 授權，但要真正建立行事曆提醒，還需要另外一段 code flow（例如 `chrome.identity.launchWebAuthFlow` 帶 `response_type=code&access_type=offline&prompt=consent`，再把 code 交給後端）。`wait_suggestion.calendar_draft` 目前也未使用。

## 多語系

介面支援 `zh-TW` 與 `en-US`，名稱為「最佳一刷」／「Swipe Right」。語言的決定順序：

1. **使用者在 popup 選的語言**（存在 `storage.local`，`storage` 權限即為此）。
2. **網頁自己的語言** —— 面板嵌在賣場頁面裡，讀該頁的 `<html lang>`。看中文結帳頁的人想要中文建議，即使瀏覽器設成英文。
3. **瀏覽器語言**（`navigator.languages`），當網頁沒宣告或宣告了我們不支援的語言。

任何 `zh-*` 對應 `zh-TW`，`en-*` 對應 `en-US`，其餘視為未知而往下一層。

`<html lang>` 只有 content script 讀得到（background 沒有 document），所以它隨內部訊息的 `pageLocale` 欄位傳遞；`isCheckoutRequest` 只放行支援的兩種語言或 `null`，後端因此不會收到它會拒絕的 locale。

面板與 popup 的文案集中在 `lib/i18n.ts`；manifest 的 `name` / `description` 是靜態欄位，改用 `public/_locales/{zh_TW,en}/messages.json` 搭配 `__MSG_` 佔位符。

**送給後端的 `locale` 必須與介面語言相同。** 後端會用它產生 `reason`、`cap_description` 與 `explanation`，而面板直接顯示這些字串；若兩者不一致，同一張卡片會混用兩種語言。`lib/backend.ts` 因此呼叫同一個 `resolveLocale()`，並有測試鎖住這個對應。

## 偵測與 UI

- `adapters/momo.ts`、`shopee.ts`、`pchome.ts`：各站 DOM 邏輯獨立；共用層不含網站 selector。
- `lib/controller.ts`：700 ms debounce、相同 context 去重、忽略過期回應與重試狀態。
- `lib/mount.ts`：將 Shadow DOM 卡片插在付款區域之後，僅觀察 adapter 指定的商品、金額與付款節點；每秒重新尋找可能被 SPA 取代的節點。
- `entrypoints/background.ts`：處理登入／登出、登入狀態變更廣播、驗證推薦訊息來源與四欄交易格式，在 background 取得 ID token 後呼叫後端 /recommendations/stream 取得推薦。

推薦卡片顯示在付款區域之後，不使用固定定位，並在上下保留間距，避免貼住分隔線或覆蓋結帳控制項。它不提供收合或關閉按鈕；錯誤與成功狀態可重新推薦，並支援 loading、success、error、unavailable。偵測到金額或商品變動時重新推薦；DOM 暫時消失後恢復成同一交易不會重複送 request。

## 手動驗收清單

驗收在支援平台的真實結帳頁進行；測試期間不得送出、確認或取消訂單。

1. 先在 popup 使用 Google 登入；未登入時面板顯示「尚未登入」且不得取得推薦。
2. 登入且帳號已有持卡：結帳頁出現載入中，再出現推薦；內部訊息四欄，backend body 不含 userId。
3. 登入但帳號沒有持卡：面板顯示「卡包還沒有信用卡」，兩種狀態不得混用同一段文案。
4. 保持結帳頁開著，在 popup 登入／登出：面板自動更新，不需手動重新整理頁面。
5. 頁面沒有可用信用卡選項：面板顯示 unavailable，不送出請求。
6. 選擇銀行轉帳但信用卡仍可用：照常推薦。
7. 金額或商品變動：700 ms 後只為最後一次的資料送出一次請求。
8. 確認卡片沒有收合或關閉按鈕，且與付款區塊下方分隔線保有間距。
9. 登出後重試：不得取得推薦；重新登入後可再次推薦。
10. Chrome、Zen 分別打包、安裝並重複以上步驟。

## 平台狀態

- momo：儲存的付款頁能辨識已勾選商品、`#paySum` 應付金額與可用信用卡；Chrome、Zen 快照測試各只送出一次 request 並顯示推薦卡片。
- 蝦皮：儲存的付款頁能辨識商品與總付款金額，但該訂單的信用卡／金融卡和分期按鈕都有 `aria-disabled="true"`，因此 Chrome、Zen 均顯示 unavailable 且不送 request。
- PChome：只在 `/fsrwd/cart` 購物車頁運作，從已勾選的購物車品項取得商品名稱，搭配「結帳金額」推薦，並依產品決定預設下一頁可使用信用卡。`/fsrwd/cart/payinfo` 付款頁不再觸發，因此不需跨頁暫存商品資料。

儲存 HTML 無法驗證 SPA 導航與互動後節點替換；正式接後端前仍需在即時網站重測優惠券／金額變更、付款選項變更和結帳完成頁。網站改版、iframe 與手機版頁面尚未驗證；辨識不足時會顯示 unavailable 並跳過推薦。

## 已知限制：活動資料新鮮度

`best_now` 為 `null` 常見的原因不是 extension 或後端壞掉，而是 `sales` 裡的活動已過期。以 2026-09-19 的資料庫為例：43 筆活動中有 39 筆的 `campaign_end` 早於今日，剩下 4 筆免登錄規則都不含 momo/蝦皮/PChome 平台，因此任何購物頁都會得到 `best_now: null`。

這是預期行為，面板會顯示後端的 `explanation`。要實際看到推薦，需要先更新活動資料（crawler + `import_sales`）。判斷時可以用這個順序：

1. 面板顯示「尚未登入」→ 登入問題。
2. 顯示「卡包還沒有信用卡」且帳號確實有持卡 → 檢查活動資料是否過期。
3. 顯示「暫時無法取得推薦」→ 連線或後端錯誤，看 background console。

另外 `registration_campaigns_enabled`（使用者設定）決定後端只看「需登錄」或「免登錄」活動，兩者不會混在同一次推薦裡。

## 自動測試範圍

`tests/checkout.test.ts` 使用從三站儲存頁面歸納出的去識別化 fixtures，覆蓋商品、金額、付款 gate、cart/payment 區分與四欄內部訊息；`tests/controller.test.ts` 覆蓋 debounce、去重、商品／金額變更、暫時 DOM 消失、舊回應與重試；`tests/api.test.ts` 覆蓋 `CheckoutContext` 到 `RecommendationRequest` 的欄位對應、`product_name` 截斷、`Authorization` 與 `Accept` header、body 不含 userId、SSE 事件解析（跨 chunk 切斷、keep-alive 註解、畸形 frame、`error` 事件、串流未給結果）、401／5xx、`best_now` 為 null 時保留 `explanation`、畸形 payload 與 requestId 不符。共 54 個測試已通過。

Firefox manifest 宣告 `data_collection_permissions.required: ["authenticationInfo"]`，因為 Google 登入會向 Firebase 傳輸帳戶驗證資訊。接上推薦 backend 時，必須再依實際傳輸的商品與金額資料更新 AMO 資料揭露。
