# ChargeOps

Cảnh báo sớm quá nhiệt connector trạm sạc xe điện và xử lý có kiểm chứng, với **MongoDB** làm nền tảng dữ liệu vận hành và **Datadog** để quan sát, phát hiện, điều tra và phản ứng.

Telemetry → phát hiện theo residual nhiệt → điều tra → operator giảm tải (từ web hoặc Datadog App) → kiểm chứng dòng giảm và nhiệt hồi phục → bảo trì mô phỏng → kiểm tra phục hồi 120 giây.

Bài dự thi MUGVN Mini Hackathon 2026. Hồ sơ đầy đủ: **[docs/SUBMISSION.md](docs/SUBMISSION.md)**.

## Điểm chính

- **Phát hiện theo vật lý, không theo ngưỡng cố định.** Residual = nhiệt đo − nhiệt kỳ vọng từ nhiệt môi trường, dòng sạc (k·I²) và quán tính nhiệt. Báo động khi trung bình cửa sổ 60 giây hợp lệ > 8 °C; cửa sổ thiếu hoặc cũ bị chặn.
- **Xử lý phải có bằng chứng.** Xác nhận hay gửi lệnh chưa đủ; sự cố chỉ `mitigated` khi telemetry chứng minh dòng thực tế giảm và residual hồi phục, chỉ `resolved` sau retest 120 giây ở tải gần như ban đầu.
- **Kết quả đánh giá offline** (120 lượt mô phỏng, protocol khóa trước, `data/evaluation/b1-eval-v3.1/`): phát hiện trước trip **20/20** (ngưỡng cố định 8/20), **0** báo nhầm (ngưỡng cố định 0,9 lần/connector-giờ), trip giảm từ **14 xuống 1** so với chỉ phát hiện mà không xử lý. Số mô phỏng, không phải số trạm thật.
- **MongoDB:** Time Series + TTL, aggregation cửa sổ residual, `$setWindowFields`, `$facet`, `$lookup`/`$dateDiff`/`$merge`, Change Streams có resume token, unique partial index, idempotency. Xem [DATA_MODEL.md](docs/DATA_MODEL.md).
- **Datadog:** 2 dashboard, composite monitor gọi webhook và 3 monitor vận hành, Logs ↔ APM ↔ RUM, Datadog Incidents với todo cho từng bước, Notebook điều tra, postmortem, 2 SLO, Datadog App gửi lệnh có kiểm soát. Xem [OBSERVABILITY.md](docs/OBSERVABILITY.md).

## Kiến trúc

| Service (Docker Compose) | Vai trò |
|---|---|
| `mongo` | MongoDB 8.0.5, replica set một node |
| `api` | FastAPI `/api/v1`: telemetry, sự cố, lệnh, webhook, đăng nhập operator |
| `worker` | Writer duy nhất của trạng thái sự cố: cửa sổ, detector, vòng đời, kiểm chứng giảm tải, metric Datadog |
| `simulator` | 2 trạm × 3 connector, replay đường tải 50 phiên Autosun–PolyU v3, mô hình nhiệt, lỗi có seed |
| `web` | Giao diện tiếng Việt (React), proxy `/api` cùng origin ở `:8080` |
| `datadog`, `webhook-gateway`, `tunnel`, `datadog-sync` | Profile `observability`: Agent, cổng chỉ mở 3 route, quick tunnel, đồng bộ Datadog Incidents/Notebooks |

Sơ đồ và luồng dữ liệu: [SUBMISSION.md §3](docs/SUBMISSION.md), [DATA_MODEL.md §2](docs/DATA_MODEL.md), [OBSERVABILITY.md §0](docs/OBSERVABILITY.md).

## Yêu cầu môi trường

- Docker Engine (Linux containers) và Docker Compose v2. Không cần Node hay Python trên host để chạy: image tự build UI và backend. Đã chạy trên Windows 11 với Docker Desktop (WSL2).
- Python ≥ 3.12 trên host chỉ để chạy `scripts/init_env.py` và `scripts/setup_datadog.py` (cần `httpx`, `python-dotenv`).
- Mode Datadog: tài khoản Datadog (trial), API key, App key có quyền dashboards, monitors, SLO, incidents, notebooks (thêm App Builder nếu dùng Datadog App), seat Incident Management cho user sở hữu App key.
- Để chạy test: `uv` (Python 3.12) và Node 22 + npm.

## Chạy nhanh (mode local, không cần Datadog)

```powershell
git clone <repo> chargeops; cd chargeops
python scripts/init_env.py --operator-user operator   # hỏi mật khẩu; tạo .env với secret ngẫu nhiên, không ghi đè .env cũ
docker compose up --build -d
docker compose exec api python -m chargeops.check_mongo   # kỳ vọng: "$setWindowFields ok", "postmortem $merge ok"
```

1. Mở http://localhost:8080, đăng nhập operator.
2. **Điều khiển demo**: tạo run mode `local`, scenario `thermal_fault`, seed bất kỳ, bấm **Bắt đầu**.
3. Trong 2–7 phút có đúng 1 sự cố. Mở sự cố: **Xác nhận** → **Giảm tải** → **Bảo trì** → **Kiểm tra phục hồi**, chờ "Đã giải quyết".
4. Màn hình người lái: http://localhost:8080/stations/station-1 (không cần đăng nhập).

Hướng dẫn đầy đủ từng bước, có kết quả kỳ vọng: [docs/KIEM_TRA.md](docs/KIEM_TRA.md).

## Mode Datadog

```powershell
docker compose --profile observability up --build -d
docker compose logs tunnel        # lấy URL https://….trycloudflare.com
python scripts/setup_datadog.py --webhook-url https://<tunnel>/api/v1/webhooks/datadog --with-app
```

Chép `DD_MONITOR_ID` script in ra vào `.env`, tạo lại worker, rồi chạy run mode `datadog`. Mỗi trial cho phép tối đa 20 run Datadog. Các bước điều tra, Datadog Incident, Notebook, SLO, RUM và Datadog App: [KIEM_TRA.md §7–8](docs/KIEM_TRA.md).

## Cấu hình (`.env`, xem `.env.example`)

| Biến | Ý nghĩa |
|---|---|
| `SIMULATOR_SECRET`, `WEBHOOK_SECRET`, `OPERATOR_SECRET` | Secret riêng cho simulator, webhook Datadog, CLI operator (`init_env.py` sinh ngẫu nhiên) |
| `OPERATOR_USERNAME`, `OPERATOR_PASSWORD_HASH` | Đăng nhập operator; hash scrypt đặt trong nháy đơn |
| `ALLOWED_ORIGINS` | Origin được dùng session cookie (mặc định `localhost:8080`, `127.0.0.1:8080`, `localhost:5173`) |
| `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` | Datadog; `DD_SITE` phải đúng site tài khoản |
| `DD_MONITOR_ID` | Id composite monitor do `setup_datadog.py` in ra |
| `DD_TRACE_ENABLED`, `DD_METRICS_ENABLED` | Bật APM và heartbeat worker (dùng với profile `observability`) |
| `DATADOG_ACTION_SECRET` | Secret cho route Datadog App (≥ 24 ký tự; để trống là tắt) |
| `DD_RUM_APPLICATION_ID`, `DD_RUM_CLIENT_TOKEN` | Browser RUM; ghi vào bundle lúc build, đổi giá trị phải build lại `web` |
| `SIM_REALISM` | Nhiệt môi trường theo ngày, nhiễu cảm biến, mất/trùng/trễ mẫu (mặc định `true`) |
| `APP_VERSION`, `DD_TRIAL_ID` | Tag phiên bản; khóa bộ đếm 20 run Datadog |

## Kiểm thử

```powershell
uv sync --frozen; uv run pytest -q; uv run ruff check chargeops scripts tests
uv run python -m chargeops.evaluation run --protocol b1-eval-v3.1 --out .pytest-tmp/eval
cd web; npm ci; npm test -- --run; npm run build; npm run check:api
```

Kết quả ngày 28/10/2026: 333 passed, 2 skipped (cần MongoDB thật qua `MONGODB_TEST_URI`); 38 test web. Test dùng mongomock và HTTP giả lập, chỉ chứng minh logic; `chargeops.check_mongo` và các run thật mới là bằng chứng chạy trên MongoDB/Datadog.

## Trạng thái kiểm chứng (28/10/2026)

- **Vòng trọn vẹn trên MongoDB và Datadog thật:** run `39207f81…` ngày 20/10 (một connector): monitor → webhook → sự cố t+425 s → giảm tải 200 A/60 A → 30 A → `mitigated` t+612 s → `resolved` t+776 s.
- **Code hiện tại trên Datadog US1:** fleet 6 connector; `setup_datadog.py --with-app` tạo đủ tài nguyên; `datadog-sync` tạo 6 Datadog Incident với todo, Notebook, postmortem, không trùng; run `5e2bd546…` mở đúng 1 sự cố `source=datadog`; RUM nhận session thật.
- **Chưa kiểm chứng live:** vòng giảm tải → phục hồi trên code hiện tại, lệnh từ Datadog App vào sự cố đang mở, RUM → APM sau khi bật `trackResources`.

Chi tiết: [OBSERVABILITY.md §6](docs/OBSERVABILITY.md), [KIEM_TRA.md](docs/KIEM_TRA.md).

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [SUBMISSION.md](docs/SUBMISSION.md) | Hồ sơ dự thi: bài toán, giải pháp, kiến trúc, kịch bản sự cố, kết quả, đối chiếu yêu cầu |
| [DATA_MODEL.md](docs/DATA_MODEL.md) | Kiến trúc dữ liệu, collection, index, aggregation pipeline, bảo đảm nhất quán |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | Kiến trúc quan sát, dashboard, monitor, SLO, RUM, Datadog App, lộ trình điều tra |
| [KIEM_TRA.md](docs/KIEM_TRA.md) | Kiểm tra từ clone sạch, mode local và Datadog, xử lý lỗi |
| [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Kịch bản video demo, ảnh cần chụp, dàn ý slide |
| [CONTRACT.md](docs/CONTRACT.md) | API, detector, vòng đời sự cố, quy tắc giảm tải và kiểm chứng |
| [EVALUATION_PROTOCOL.md](docs/EVALUATION_PROTOCOL.md) | Protocol đánh giá A/B/C |
| [DATA_CARD.md](docs/DATA_CARD.md) | Dataset Autosun–PolyU v3: nguồn, license, kiểm định, giới hạn |
| [DISCLOSURE.md](docs/DISCLOSURE.md) | Phần làm trước 17/10, AI, thành phần tái sử dụng và license |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Seam mở rộng và lộ trình sau cuộc thi |
| [MINIMAL_LOOP.md](docs/MINIMAL_LOOP.md), [PROGRESS.md](docs/PROGRESS.md) | Nhật ký kiểm chứng live và tiến độ |

## Dữ liệu và giới hạn

- Đường tải (dòng, điện áp) replay từ dataset công khai [Autosun–PolyU v3](https://data.mendeley.com/datasets/c7gg94tmvz/3) (CC BY 4.0) sau kiểm định; nhiệt độ, lỗi và phản ứng sau lệnh là mô phỏng. Không dùng dữ liệu cá nhân hay dữ liệu vận hành thật.
- Ngưỡng 8 °C/3 °C và chính sách giảm tải là cấu hình demo, không phải chuẩn an toàn thiết bị.
- Chạy local-first: không EC2, Atlas hay dịch vụ trả phí; Datadog dùng bản trial.
