# 刷哪張 Browser Extension

WXT + Vue + TypeScript，設定輸出 Chrome / Firefox Manifest V3。使用者先透過 Firebase Authentication 使用 Google 登入；偵測支援頁面後，extension 取得商品和正數金額，並在可確認時檢查信用卡付款選項，再透過 background 取得推薦。Google 登入會連線到 Firebase，推薦則由 background 帶 Firebase ID token 呼叫後端取得。PChome 依產品決定在購物車頁預設可刷卡；其他平台仍必須先確認信用卡可用。

**原型已通過型別檢查、44 個單元測試與 Chrome / Firefox MV3 打包；購物頁偵測曾通過 Chrome / Zen 驗證。** Firebase 登入 helper 已部署，端到端登入尚待瀏覽器實測；推薦已對接後端既有的 `POST /api/v1/search`，但尚未與實際後端連線驗證。驗證環境為 Node.js 24.21.0、npm 11.19.0。Adapters 依使用者登入後儲存的三站購物車／付款頁 DOM 建立；測試 fixtures 只保留去識別化的結構和合成商品資料，原始 HTML 未加入 repository。

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

## 後端契約：POST /api/v1/search

推薦來自後端既有的 `POST /api/v1/search`（`backend/app/api/v1/routes/search.py`），frontend 的推薦頁也使用同一支 API。Request body 對應後端的 `SearchRequest`：

```json
{
  "price": 2580,
  "platform": "shopee",
  "category": "無線耳機、手機保護殼",
  "currency": "TWD",
  "include_unowned": false
}
```

- `price: number`：畫面上的最終應付金額，必須大於零。
- `platform: "momo" | "shopee" | "pchome"`：後端以小寫完全比對活動適用平台。
- `category: string`：後端接受分類名（`dining`）或品項名，並以子字串比對自己的分類詞彙；比對不到時只套用平台與通用規則。結帳頁只拿得到商品名稱，所以多品項字串可能比對不到分類。
- `currency`：固定 `TWD`。
- `include_unowned: false`：只排序使用者實際持有的卡，推薦一張還沒申辦的卡在結帳頁幫不上忙。

**request body 不含 `userId`。** 後端的 `get_current_user`（`backend/app/api/deps.py`）從已驗證 ID token 的 `sub` claim 取得使用者，不讀 body，所以沒有可偽造的身分欄位。每次 request 必須帶：

```http
Authorization: Bearer <Firebase ID token>
```

後端用 `google.oauth2.id_token.verify_firebase_token` 驗證，audience 為 `firebase_project_id`（預設 `meichu-2026`，與 `lib/firebase-config.ts` 的 `projectId` 相同）；驗證失敗回 401。

Response 取 `SearchResponse` 的 `best`：

```json
{
  "best": {
    "owned": true,
    "card": { "id": "…", "bank_name": "玉山", "name": "Unicard" },
    "estimated_reward": { "amount": 77.4, "rate": 0.03, "requires_registration": false },
    "reason": "一般消費 3% 回饋"
  },
  "alternatives": [],
  "resolved_category": null,
  "considered_card_count": 1
}
```

- 卡片以 `bank_name` + `name` 顯示（例如「玉山 Unicard」）。**後端沒有卡號末四碼**，資料模型裡不存在該欄位，UI 因此顯示卡片名稱而非末四碼。
- `estimated_reward` 為 `null` 代表該卡活動條件無法自動計算金額；這種卡仍會被推薦並排在後面，UI 顯示說明而不是假的金額。
- `best` 為 `null`（使用者尚未新增任何持有卡片）時不顯示推薦卡片，視為無法推薦。
- `requestId` 不送給後端，僅用於 content script 與 background 之間比對回應是否屬於當前結帳狀態；`lib/api.ts` 負責這層檢查。

付款方式只作為 extension 本地的觸發條件，不包含在 request。即使當前選中轉帳，只要仍有可用信用卡選項，就會推薦。沒有信用卡、信用卡停用、無法確認付款選項、商品空白、金額無效或有歧義時，不觸發。PChome 是明確例外：在付款方式尚未出現的購物車頁預設可刷卡並先提供推薦。

Content script 傳給 background 的 runtime message 只有 `requestId / platform / product / payable`，不接觸登入憑證。Background 驗證訊息來源後才取得 ID token 並轉成上述 body。

後端網址由 `lib/backend-config.ts` 決定，預設 `http://localhost:8000`，以建置時環境變數 `WXT_BACKEND_URL` 覆寫：

```sh
WXT_BACKEND_URL=https://api.example.com npm run build
```

同一個變數也會決定 `wxt.config.ts` 產生的 `host_permissions`；兩者必須一致，否則 `fetch` 會在執行期被瀏覽器阻擋。改預設值時要同時改 `lib/backend-config.ts` 的 `DEFAULT_BACKEND_URL` 與 `wxt.config.ts` 的 `backendOrigin()` fallback。

`host_permissions` 只含 Firebase Authentication 與設定的 backend 網域；沒有 Admin private key、持卡資料儲存或全站存取權限。

## 偵測與 UI

- `adapters/momo.ts`、`shopee.ts`、`pchome.ts`：各站 DOM 邏輯獨立；共用層不含網站 selector。
- `lib/controller.ts`：700 ms debounce、相同 context 去重、忽略過期回應與重試狀態。
- `lib/mount.ts`：將 Shadow DOM 卡片插在付款區域之後，僅觀察 adapter 指定的商品、金額與付款節點；每秒重新尋找可能被 SPA 取代的節點。
- `entrypoints/background.ts`：處理登入／登出、驗證推薦訊息來源與四欄交易格式，在 background 取得 ID token 後呼叫後端 /search 取得推薦。

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

## 自動測試範圍

`tests/checkout.test.ts` 使用從三站儲存頁面歸納出的去識別化 fixtures，覆蓋商品、金額、付款 gate、cart/payment 區分與四欄內部訊息；`tests/controller.test.ts` 覆蓋 debounce、去重、商品／金額變更、暫時 DOM 消失、舊回應與重試；`tests/api.test.ts` 覆蓋 `CheckoutContext` 到 `SearchRequest` 的欄位對應、`Authorization` header、body 不含 userId、401／5xx、`best` 為 null、無法估算回饋的卡片、畸形 payload 與 requestId 不符。共 44 個測試已通過。

Firefox manifest 宣告 `data_collection_permissions.required: ["authenticationInfo"]`，因為 Google 登入會向 Firebase 傳輸帳戶驗證資訊。接上推薦 backend 時，必須再依實際傳輸的商品與金額資料更新 AMO 資料揭露。
