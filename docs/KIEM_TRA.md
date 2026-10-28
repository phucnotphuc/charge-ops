# Hướng dẫn chạy và kiểm tra ChargeOps từ clone sạch

Hướng dẫn cho người mới checkout nhánh `feat/station-observability`. Lệnh chạy trong PowerShell hoặc Git Bash, ở thư mục gốc repo. Mục 3–5 không cần Datadog; mục 7–8 cần tài khoản Datadog (trial). Ngày cập nhật: 28/10/2026.

## 0. Chuẩn bị máy

| Cần | Kiểm tra |
|---|---|
| Git, quyền đọc repo private `phucnotphuc/charge-ops` | `git --version` |
| Docker Desktop (Linux containers) **đang chạy** | `docker info` không báo lỗi |
| Python ≥ 3.12 trên host (chỉ cho `scripts/init_env.py` và `scripts/setup_datadog.py`) | `python --version` |
| Thư viện cho `setup_datadog.py` (chỉ khi chạy Datadog) | `python -m pip install httpx python-dotenv` |
| Không bắt buộc, để chạy test: `uv`, Node 22 | `uv --version`, `node --version` |

Không cần Node trên host để chạy ứng dụng: image `web` tự build giao diện.

## 1. Lấy code

```bash
git clone https://github.com/phucnotphuc/charge-ops.git
cd charge-ops
git switch feat/station-observability
```

## 2. Tạo `.env`

```bash
python scripts/init_env.py --operator-user operator
```

Script hỏi mật khẩu operator, tạo các secret ngẫu nhiên (gồm `DATADOG_ACTION_SECRET` cho Datadog App), không in secret và không ghi đè `.env` đã có.

Mặc định đã đủ cho mode local. Compose đặt sẵn:

- `SIM_REALISM=true`: nhiệt môi trường theo giờ, nhiễu cảm biến, khoảng 1% mẫu mất/trùng/đến muộn.
- `DD_METRICS_ENABLED=false`.

Nếu `.env` tạo từ trước ngày 28/10 và thiếu `DATADOG_ACTION_SECRET`, thêm một dòng với giá trị ngẫu nhiên ≥ 24 ký tự. Có thể tạo bằng:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Quy tắc khi sửa `.env` bằng tay:

- Mỗi biến chỉ có **một** dòng; nếu trùng, dòng cuối thắng.
- `OPERATOR_PASSWORD_HASH` đặt trong nháy đơn `'scrypt$…'`, không viết `$$`.
- Không commit `.env` và không để key lọt vào ảnh chụp.

## 3. Chạy local (không cần Datadog)

```bash
docker compose up --build -d
docker compose ps
```

Kỳ vọng: `mongo` và `api` ở trạng thái **healthy**; `worker`, `simulator`, `web` ở trạng thái **running**. Lần build đầu mất vài phút.

Kiểm tra MongoDB thật (time series, aggregation, window functions, postmortem):

```bash
docker compose exec api python -m chargeops.check_mongo
```

Kỳ vọng: có hai dòng **`$setWindowFields ok`** và **`postmortem $merge ok`**.

## 4. Kiểm tra giao diện

1. http://localhost:8080: trang Tổng quan hiện, không có lỗi.
2. http://localhost:8080/stations/station-1 và `/stations/station-2`: màn hình trạm cho người dùng, không cần đăng nhập. Khi chưa có run, các đầu sạc hiện "Tạm mất kết nối"; đó là đúng.
3. Bấm **Đăng nhập operator**, nhập `operator` và mật khẩu ở bước 2. Phải vào bằng `localhost:8080` hoặc `127.0.0.1:8080`; origin khác sẽ bị từ chối (403).

## 5. Chạy trọn một vòng sự cố (mode local)

1. **Điều khiển demo**: tạo run với mode `local`, scenario `thermal_fault`, seed bất kỳ, rồi bấm **Bắt đầu**.
2. Mở song song:
   - màn hình người dùng `/stations/station-1` hoặc `station-2`;
   - mục **Trạm**, tức màn hình vận hành (cần đăng nhập).
3. Trong 1–2 phút đầu: 6 đầu sạc hiện pin % (SoC thật từ dữ liệu Autosun), công suất, dòng điện, nhiệt độ đầu sạc và thời gian đến khi đầy. Pin % tăng dần.
4. Trong khoảng 2–7 phút: một đầu sạc nóng bất thường. Mục **Sự cố** có đúng **1** sự cố mới, đúng connector đó.
5. Mở sự cố và thao tác lần lượt:
   1. **Xác nhận**.
   2. **Giảm tải**. Màn hình người dùng chuyển đầu sạc đó sang "Đang sạc chậm để bảo vệ đầu sạc", pin % tăng chậm lại.
   3. Chờ đến khi nút **Bảo trì** sáng, rồi bấm.
   4. **Kiểm tra phục hồi**. Chờ khoảng 2 phút tới trạng thái **Đã giải quyết**.
6. Màn hình vận hành hiện residual, xu hướng (MongoDB `$setWindowFields`), phễu trạng thái và MTTA/MTTR. Danh sách lệnh của sự cố có cột **Nguồn**, ở đây là "Operator".

**Đạt khi:**

- có đúng 1 sự cố, đúng connector lỗi, và nó đi hết tới "Đã giải quyết";
- năm connector còn lại không có sự cố;
- màn hình người dùng không bao giờ hiện từ "residual", "sự cố", mã lỗi hay mã run.

Mỗi run tự dừng sau 30 phút. Dừng run không tự giải quyết sự cố.

## 6. Test tự động (không bắt buộc)

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check chargeops scripts tests
uv run python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out .pytest-tmp/eval
```

Kỳ vọng:

- `325 passed, 2 skipped`. Hai test bị bỏ qua cần MongoDB thật qua `MONGODB_TEST_URI`.
- ruff sạch.
- Lệnh đánh giá in ra `(120 runs, …)`. Nếu lock không còn hợp lệ, lệnh sẽ từ chối chạy.

```bash
cd web
npm ci
npm test -- --run
npm run build
npm run check:api
```

Kỳ vọng: `38 passed`, build thành công, `check:api` không báo lệch kiểu API. Luôn chạy `npm ci` sau khi pull, vì thiếu package mới (ví dụ `@datadog/browser-rum`) thì một số file test không chạy.

Các test này dùng MongoDB giả lập (mongomock) và HTTP giả lập, chỉ chứng minh logic, không thay cho mục 3–5 và 7–8.

## 7. Mode Datadog: phát hiện, dashboard, monitor

1. Trong `.env`, mỗi biến một dòng:
   - `DD_API_KEY`, `DD_APP_KEY`: key của tài khoản;
   - `DD_SITE`: đúng site của tài khoản, ví dụ `ap1.datadoghq.com`;
   - `DD_TRACE_ENABLED=true`;
   - `DD_METRICS_ENABLED=true`.

   `DD_APP_KEY` cần quyền dashboards, monitors, SLO, incidents, notebooks. Nếu dùng Datadog App (mục 8) thì thêm `apps_write`, `connections_resolve`, `workflows_run`.
2. Chạy stack đầy đủ: Agent, webhook gateway, tunnel, và `datadog-sync`.
   ```bash
   docker compose --profile observability up --build -d
   docker compose --profile observability exec datadog agent status
   ```
   Kỳ vọng: `API Key status: API Key valid` và đúng site.
3. Lấy URL tunnel, tức dòng `https://….trycloudflare.com` mới nhất:
   ```bash
   docker compose logs tunnel
   ```
4. Tạo hoặc cập nhật webhook, monitor, 2 SLO và 2 dashboard (kỹ sư trạm, IT/SRE):
   ```bash
   python scripts/setup_datadog.py --webhook-url https://YOUR-TUNNEL.trycloudflare.com/api/v1/webhooks/datadog
   ```
   Chép `DD_MONITOR_ID=…` mà script in ra vào `.env`, rồi khởi động lại worker:
   ```bash
   docker compose --profile observability up -d worker
   ```
   Script lưu id tài nguyên vào `artifacts/datadog-resources.json` (git-ignored). Chạy lại sẽ cập nhật đúng tài nguyên đó, không tạo trùng.
5. Lặp lại mục 5, nhưng chọn mode **`datadog`**. Mỗi run Datadog dùng 1 lượt trong trần 20 run của trial.
6. Trên Datadog, mở link dashboard mà script in ra và kiểm tra:
   - composite monitor "ChargeOps thermal incident" báo đúng 1 connector, và webhook mở đúng 1 sự cố trong ứng dụng;
   - link "Điều tra" trong tin nhắn cảnh báo mở dashboard kỹ sư, lọc theo run/connector;
   - biểu đồ residual có vạch 8 °C / 3 °C và event `opened` đánh dấu;
   - các bảng "Sự cố đang mở theo connector" và "Thời gian ở trạng thái hiện tại" có số liệu;
   - dashboard IT/SRE có "Worker heartbeat";
   - từ một dòng log mở được trace APM.

## 8. Mode Datadog: điều tra, xử lý và hậu kiểm trên Datadog

Chạy trong cùng run Datadog ở mục 7.

1. **Datadog Incident.** Do `datadog-sync` tạo. Trong vòng khoảng 10 giây sau khi sự cố mở trong ChargeOps, Datadog > Incidents có một Incident tên "ChargeOps connector-N quá nhiệt [mã sự cố]". Mỗi bước (xác nhận, giảm tải, bảo trì…) hiện thành một todo đã hoàn thành. Khi ChargeOps "Đã giải quyết", Incident chuyển `resolved`.
2. **Notebook điều tra.** Incident có link "Notebook điều tra". Trang sự cố trong ChargeOps cũng có link "Mở trên Datadog" và "Notebook điều tra". Notebook có biểu đồ residual, nhiệt đo/kỳ vọng, dòng/giới hạn, đã lọc đúng run và connector, kèm luồng log của sự cố.
3. **Postmortem.** Sau khi giải quyết, hoặc khi dừng run mà sự cố chưa giải quyết, Notebook có thêm ô "Postmortem" và Incident có ghi chú tóm tắt. Nội dung:
   - thời gian từng bước;
   - residual đỉnh;
   - số lần retest bị reset;
   - năng lượng trong lúc giảm tải (ghi rõ "Dữ liệu mô phỏng").

   Báo cáo được tính bằng MongoDB `$lookup` + `$dateDiff` + `$merge` vào collection `incident_reports`.
4. **SLO.** Dashboard kỹ sư có 2 widget SLO: "Giảm tải có tác dụng trong 5 phút" và "Phút sạc không gián đoạn" (mục tiêu 95%, 7 và 30 ngày). Giá trị thay đổi sau run.
5. **RUM (tùy chọn).**
   1. Tạo một RUM application loại JavaScript trên Datadog.
   2. Điền `DD_RUM_APPLICATION_ID` và `DD_RUM_CLIENT_TOKEN` vào `.env`.
   3. Build lại web:
      ```bash
      docker compose --profile observability up --build -d web
      ```
   4. Mở một sự cố và gửi một lệnh. Trên RUM Explorer, service `chargeops-web` phải có action `command_sent`, kèm thời gian từ lúc thấy sự cố tới lúc gửi lệnh.
6. **Datadog App: thao tác ngay trong Datadog (tùy chọn).**
   1. Đảm bảo `.env` có `DATADOG_ACTION_SECRET` (mục 2). Tạo lại api:
      ```bash
      docker compose --profile observability up -d api
      ```
   2. Chạy lại bước 4 của mục 7, thêm `--with-app`:
      ```bash
      python scripts/setup_datadog.py --webhook-url https://YOUR-TUNNEL.trycloudflare.com/api/v1/webhooks/datadog --with-app
      ```
      Script in link app (Datadog > Apps). Nếu script in hướng dẫn "Import JSON", làm theo từng bước, vì định nghĩa app (`scripts/datadog_app.json`) chưa được thử trên tài khoản thật.
   3. Mở app "ChargeOps - xu ly su co", chọn một sự cố `open`, bấm **Xác nhận**.

   Kỳ vọng:
   - sự cố chuyển `acknowledged` trong ChargeOps;
   - danh sách lệnh trong ChargeOps hiện nguồn "Datadog App";
   - nút không hợp lệ bị tắt.
7. **Kiểm tra gateway.** Tunnel chỉ mở 3 đường; mọi đường khác trả 404:
   ```bash
   curl -i https://YOUR-TUNNEL.trycloudflare.com/api/v1/incidents
   ```
   Kỳ vọng `404`. Thử thêm với chữ hoa (`/API/V1/INTEGRATIONS/DATADOG/INCIDENTS`) hoặc `//`, cũng phải là `404`.

Lưu ý:

- Tunnel đổi URL mỗi lần khởi động lại. Khi đó phải chạy lại bước 4 của mục 7 (kèm `--with-app` nếu dùng app).
- Monitor "ChargeOps telemetry stale" vào trạng thái no-data khoảng 4 phút sau khi run kết thúc.
- Monitor "ChargeOps worker heartbeat" báo khi `DD_METRICS_ENABLED=false`. Hai monitor này không gọi webhook và không tạo sự cố.
- Recovery từ Datadog không tự đóng sự cố; chỉ worker đóng sự cố sau retest 120 giây.
- Bấm đúp một nút trong app có thể tạo lệnh thứ hai; worker từ chối lệnh đó (`command_rejected`).
- Lỗi Datadog API, ví dụ thiếu quyền key, không ảnh hưởng phát hiện hay lệnh. Xem `docker compose logs datadog-sync` (sự kiện `datadog_sync_failed`, `datadog_notebook_failed`, `datadog_postmortem_failed`).

## 9. Khi gặp lỗi

| Hiện tượng | Kiểm tra |
|---|---|
| Container không lên hoặc restart liên tục | `docker compose logs --tail 50 <service>` với `api`, `worker`, `simulator`, `web`, `mongo`, `datadog-sync` |
| Đăng nhập báo sai | Hash trong `.env` có nằm trong nháy đơn không; có đang vào bằng `localhost:8080` không |
| Datadog không có dữ liệu | `DD_SITE` sai site, key không hợp lệ, run đang ở mode `local`, hoặc `.env` có biến trùng dòng |
| Không có Datadog Incident / Notebook | `docker compose logs datadog-sync`. HTTP 403 nghĩa là `DD_APP_KEY` thiếu quyền incidents/notebooks |
| Không có sự cố | Scenario có phải `thermal_fault` không; xem `docker compose logs worker` |
| App trả 503 `actions_disabled` | `.env` thiếu `DATADOG_ACTION_SECRET`, hoặc chưa tạo lại api sau khi thêm |
| `setup_datadog.py` báo thiếu module | `python -m pip install httpx python-dotenv` |

Dọn dẹp, kể cả xóa dữ liệu MongoDB:

```bash
docker compose --profile observability down -v
```

## Những gì đã kiểm chứng và chưa

- **Đã kiểm chứng:**
  - 325 test Python, 38 test web, `check:api`.
  - Khóa đánh giá v3.1 vẫn được chấp nhận.
  - `docker compose config` hợp lệ, `caddy validate` cho gateway.
  - Ngày 20/10 đã chạy live luồng **1 connector** với MongoDB và Datadog thật (xem [MINIMAL_LOOP.md](MINIMAL_LOOP.md)).
- **Chưa chạy live trên nhánh này:**
  - fleet 6 connector, màn hình trạm, simulator realism;
  - dashboard/monitor/SLO mới;
  - `datadog-sync` (Incident, Notebook, postmortem trên MongoDB 8 thật);
  - RUM;
  - Datadog App: định nghĩa app và placeholder header `{{ actionSecret }}` chỉ kiểm được khi import thật.

  Kết quả sẽ được ghi vào MINIMAL_LOOP.md sau lần chạy live đầu tiên.
