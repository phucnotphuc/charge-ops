# Kiểm tra ChargeOps từ clone sạch đến trọn vòng sự cố

Hướng dẫn cho người mới checkout nhánh `feat/station-observability`. Lệnh dùng được trong PowerShell hoặc Git Bash, chạy ở thư mục gốc repo. Mục 3–5 không cần Datadog; mục 7 cần trial Datadog. Ngày cập nhật: 28/10/2026.

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

Script hỏi mật khẩu operator, tạo secret ngẫu nhiên, không in secret và không ghi đè `.env` đã có. Mặc định đã đủ cho mode local: Compose đặt `SIM_REALISM=true` (nhiệt môi trường theo giờ, nhiễu cảm biến, khoảng 1% mẫu mất/trùng/đến muộn) và `DD_METRICS_ENABLED=false`.

Quy tắc khi sửa `.env` bằng tay:

- Mỗi biến chỉ có **một** dòng; nếu trùng, dòng cuối thắng.
- `OPERATOR_PASSWORD_HASH` đặt trong nháy đơn `'scrypt$…'`, không viết `$$`.
- Không commit `.env` và không đưa key vào ảnh chụp.

## 3. Chạy local (không cần Datadog)

```bash
docker compose up --build -d
docker compose ps
```

Kỳ vọng: `mongo` và `api` ở trạng thái **healthy**; `worker`, `simulator`, `web` ở trạng thái **running**. Lần build đầu mất vài phút.

Kiểm tra MongoDB thật (time series, aggregation, window functions):

```bash
docker compose exec api python -m chargeops.check_mongo
```

Kỳ vọng: có dòng **`$setWindowFields ok`**.

## 4. Kiểm tra giao diện

1. http://localhost:8080: trang Tổng quan hiện, không có lỗi.
2. http://localhost:8080/stations/station-1 và `/stations/station-2`: màn hình trạm cho người dùng, không cần đăng nhập. Khi chưa có run, các đầu sạc hiện "Tạm mất kết nối"; đó là đúng.
3. Bấm **Đăng nhập operator**, nhập `operator` và mật khẩu ở bước 2. Phải vào bằng `localhost:8080` hoặc `127.0.0.1:8080`; origin khác sẽ bị từ chối (403).

## 5. Chạy trọn một vòng sự cố (mode local)

1. **Điều khiển demo**: tạo run với mode `local`, scenario `thermal_fault`, seed bất kỳ, rồi bấm **Bắt đầu**.
2. Mở song song:
   - màn hình người dùng `/stations/station-1` hoặc `station-2`;
   - mục **Trạm**, tức màn hình vận hành (cần đăng nhập).
3. Trong 1–2 phút đầu: 6 đầu sạc hiện pin %, công suất, dòng điện, nhiệt độ đầu sạc và thời gian đến khi đầy. Pin % tăng dần.
4. Trong khoảng 2–7 phút: một đầu sạc nóng bất thường. Mục **Sự cố** có đúng **1** sự cố mới, đúng connector đó.
5. Mở sự cố và thao tác lần lượt:
   1. **Xác nhận**.
   2. **Giảm tải**. Màn hình người dùng chuyển đầu sạc đó sang "Đang sạc chậm để bảo vệ đầu sạc", pin % tăng chậm lại.
   3. Chờ đến khi nút **Bảo trì** sáng, rồi bấm.
   4. **Kiểm tra phục hồi**. Chờ khoảng 2 phút tới trạng thái **Đã giải quyết**.
6. Màn hình vận hành hiện residual, xu hướng (MongoDB `$setWindowFields`), phễu trạng thái và MTTA/MTTR.

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

Kỳ vọng: `230 passed, 1 skipped` (test bị skip cần MongoDB thật qua `MONGODB_TEST_URI`), ruff sạch, đánh giá in ra `(120 runs, …)`. Nếu lock không còn hợp lệ, lệnh đánh giá sẽ từ chối chạy.

```bash
cd web
npm ci
npm test -- --run
npm run build
npm run check:api
```

Kỳ vọng: `32 passed`, build thành công, `check:api` không báo lệch kiểu API.

Các test này dùng MongoDB giả lập (mongomock), chỉ chứng minh logic, không thay cho mục 3–5 và 7.

## 7. Mode Datadog (cần trial Datadog)

1. Trong `.env`, mỗi biến một dòng:
   - `DD_API_KEY`, `DD_APP_KEY`: key của trial;
   - `DD_SITE`: đúng site của tài khoản, ví dụ `ap1.datadoghq.com`;
   - `DD_TRACE_ENABLED=true`;
   - `DD_METRICS_ENABLED=true`.
2. Chạy stack có Datadog Agent, webhook gateway và tunnel:
   ```bash
   docker compose --profile observability up --build -d
   docker compose --profile observability exec datadog agent status
   ```
   Kỳ vọng: `API Key status: API Key valid` và đúng site.
3. Lấy URL tunnel, tức dòng `https://….trycloudflare.com` mới nhất:
   ```bash
   docker compose logs tunnel
   ```
4. Tạo hoặc cập nhật 2 dashboard (kỹ sư trạm, IT/SRE), các monitor và webhook:
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

Lưu ý:

- Tunnel đổi URL mỗi lần khởi động lại, nên phải chạy lại bước 4.
- Monitor "ChargeOps telemetry stale" vào trạng thái no-data khoảng 4 phút sau khi run kết thúc.
- Monitor "ChargeOps worker heartbeat" báo khi `DD_METRICS_ENABLED=false`.
- Hai monitor này không gọi webhook và không tạo sự cố.
- Recovery từ Datadog không tự đóng sự cố; chỉ worker đóng sự cố sau retest 120 giây.

## 8. Khi gặp lỗi

| Hiện tượng | Kiểm tra |
|---|---|
| Container không lên hoặc restart liên tục | `docker compose logs --tail 50 <service>` với `api`, `worker`, `simulator`, `web`, `mongo` |
| Đăng nhập báo sai | Hash trong `.env` có nằm trong nháy đơn không; có đang vào bằng `localhost:8080` không |
| Datadog không có dữ liệu | `DD_SITE` sai site, key không hợp lệ, run đang ở mode `local`, hoặc `.env` có biến trùng dòng |
| Không có sự cố | Scenario có phải `thermal_fault` không; xem `docker compose logs worker` |
| `setup_datadog.py` báo thiếu module | `python -m pip install httpx python-dotenv` |

Dọn dẹp, kể cả xóa dữ liệu MongoDB:

```bash
docker compose --profile observability down -v
```

## Những gì đã kiểm chứng và chưa

- **Đã kiểm chứng:** test Python và web, khóa đánh giá v3.1, `docker compose config`. Ngày 20/10 đã chạy live luồng **1 connector** với MongoDB và Datadog thật (xem [MINIMAL_LOOP.md](MINIMAL_LOOP.md)).
- **Chưa chạy live trên nhánh này:** fleet 6 connector, màn hình trạm, dashboard/monitor mới, simulator realism. Kết quả sẽ được ghi vào MINIMAL_LOOP.md sau lần chạy live đầu tiên.
