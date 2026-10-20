# Luồng tối thiểu — phiên bản đầu ngày 14/10/2026

## Phạm vi và bằng chứng

Đã viết API FastAPI, worker, simulator một connector, schema/index/time-series MongoDB, aggregation deduplicate/residual, inbox webhook, incident idempotent, Compose, cấu hình Agent/Caddy và script tạo tài nguyên Datadog. Có local mode riêng, không tự fallback khi Datadog lỗi. Đã khóa dependencies Python và chạy tests logic/API bằng mongomock; Compose validate đạt. **Chưa chạy container, MongoDB thật hoặc Datadog thật; gate chưa đạt.**

Người dùng yêu cầu bắt đầu ngay trước 17/10; xem DISCLOSURE.md. Không commit/push trong lượt này. Bộ test không được sử dụng làm video/bằng chứng tích hợp thật.

Kết quả kiểm tra ngày 14/10: **13 tests passed** (logic/API với mongomock), Ruff đạt, Python compile đạt và `docker compose --profile observability config --quiet` đạt. Một cảnh báo deprecation TestClient/httpx được ghi ở cuối tài liệu. `chargeops.check_mongo` và setup Datadog chưa chạy vì Engine/keys chưa sẵn sàng.

## Những phần cố ý chưa triển khai

- Chỉ một connector, hai scenario `healthy`/`thermal_fault`, tải cố định 60 A/400 V. Nhiệt ban đầu ở cân bằng; baseline `ambient + 0.004 × current²` chỉ hợp lệ cho gate tải cố định này. Baseline động/tuning/đánh giá 20+20 seed chưa có.
- Chưa có UI React, operator login, command giảm tải/bảo trì/retest hoặc endpoint quản lý run qua HTTP. CLI quản trị chỉ chạy local trong container, không public. Incident hiện chỉ mở; recovery Datadog không đóng incident.
- Ingest hiện dùng schema nhỏ của `Sample` (xem `/docs`); station/charger/provenance do API gán. Session chưa có. Không tuyên bố đã hoàn tất toàn bộ CONTRACT.md.
- Chỉ chạy **một worker**; chưa hỗ trợ nhiều replica hoặc leader election. Windows hiện lưu lâu dài cùng evidence; cần thêm retention cho windows ở giai đoạn sau.
- Agent gửi metrics bằng DogStatsD/UDP, không bảo đảm delivery. Worker đánh dấu window trước gửi để tránh nhân đôi block khi restart; crash giữa ghi và gửi có thể mất block. Cần đối chiếu số mẫu trên Datadog và kiểm chứng missing-data trước nghiệm thu. Không coi log `metrics_emitted` là bằng chứng Datadog nhận thành công.

## 1. Chuẩn bị local

Yêu cầu: Python 3.12/uv cho scripts/tests, Docker Desktop Linux Engine đang chạy, `.env` local. Không cần Node cho gate này.

```powershell
# Chỉ chạy nếu .env chưa tồn tại; script từ chối ghi đè.
python scripts/init_env.py
uv sync --frozen
docker compose up --build -d
docker compose exec api python -m chargeops.check_mongo
```

Script check_mongo dùng một database `chargeops_smoke_<uuid>` riêng và xóa đúng database đó khi kết thúc. Kiểm tra thực time-series/TTL, aggregation khử trùng/residual, unique run và incident. Không đụng database ứng dụng.

API chỉ bind localhost:8080, MongoDB không publish port host. `http://localhost:8080/docs` có OpenAPI. `GET /api/v1/ready` kiểm tra MongoDB. Image tag cụ thể đã chọn trong Compose/Dockerfile nhưng chưa build/pull xác minh.

Thử local (không đáp ứng gate Datadog):

```powershell
docker compose exec api python -m chargeops.cli start --mode local --scenario thermal_fault
docker compose exec api python -m chargeops.cli status
docker compose exec api python -m chargeops.cli stop
```

Worker nhận start/stop bất đồng bộ. Fault tăng nhiệt sau 120 giây, mẫu mỗi 5 giây, cửa sổ aggregation 60 giây đóng mỗi 15 giây. Run tự dừng sau 900 giây; stop giữ nguyên incident chưa resolved. CLI chỉ cho một pending/active run. Không mở run mới trước khi run cũ đã stopped.

## 2. Datadog thật

1. Điền `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` vào `.env` trên máy, không gửi keys vào chat/repo. App key cần quyền quản lý monitor/dashboard/webhook. App key chỉ dùng script setup, không truyền vào app/Agent containers. Nếu shell có biến DD_* cũ, kiểm tra để không ghi đè nhầm cấu hình `.env`.
2. Khởi động profile; lấy HTTPS URL từ logs tunnel:

```powershell
docker compose --profile observability up --build -d
docker compose logs tunnel
```

3. Chạy script với URL thực của tunnel:

```powershell
uv run python scripts/setup_datadog.py --webhook-url https://YOUR-TUNNEL.trycloudflare.com/api/v1/webhooks/datadog
```

Script validate query qua Datadog API trước khi tạo tài nguyên. Tạo/cập nhật webhook tên `chargeops-minimal`, hai monitor tín hiệu, composite monitor và dashboard. Lưu IDs tại `artifacts/datadog-resources.json` (gitignored); giữ file này để chạy lại không tạo trùng monitor/dashboard. Không dùng tên webhook này cho dự án khác. Nếu HTTP lỗi, script báo status và dừng, không in response chứa secrets; có thể kiểm tra cấu hình/quyền trong Datadog và chạy lại. Nếu mất state file, kiểm tra tài nguyên cũ trước khi provision lại.

4. Đặt `DD_MONITOR_ID` bằng ID composite script in ra, giữ cùng `DD_TRIAL_ID` suốt trial, rồi:

```powershell
docker compose --profile observability up -d --force-recreate worker
docker compose exec api python -m chargeops.cli start --mode datadog --scenario thermal_fault
docker compose exec api python -m chargeops.cli status
docker compose logs --tail 50 worker
```

Thiếu monitor ID thì worker từ chối start, không tiêu run budget. Tối đa 20 unique run/trial, lưu trong MongoDB; offline/local không tiêu budget. Không xóa volume hoặc đổi trial ID để vượt trần. Tunnel đổi URL sau restart thì chạy lại setup với URL mới.

## 3. Cách quan sát

- Dashboard: residual, số mẫu, quality theo run/connector.
- Monitor nhiệt: mỗi block 15 giây gửi tổng residual dương, trị tuyệt đối residual âm và số mẫu; `sum(last_1m)` cùng `.as_count()` tính `(positive - negative) / samples`. Không gửi rolling mean rồi lấy mean lần nữa. Việc alignment/flush của Agent và cửa sổ Datadog phải kiểm chứng thật, không suy từ unit tests.
- Monitor quality là gate kỹ thuật: ALERT của monitor này nghĩa là dữ liệu hợp lệ. Chỉ composite `thermal && quality` gửi webhook sự cố. Khi quality mất, composite recovery không chứng minh connector đã sửa.
- Logs từ `/logs/*.log` được Agent thu thập; tìm `run_id`, `event_id`, `webhook_received` và `webhook_processed`. API không log secret/header/raw webhook. Chưa có APM.
- Webhook dùng `$ID`, `$ALERT_ID`, `$ALERT_SCOPE`, `$DATE`, `$ALERT_TRANSITION`; API chuẩn hóa thành event/run/connector/timestamp/status, thiếu identity thì quarantine. Worker chỉ nhận đúng composite ID đã cấu hình, đúng run đang active và evidence hợp lệ gần thời điểm alert.
- Incident có snapshot ngoài TTL telemetry, timestamps Datadog/nhận webhook/mở incident, `webhook_delivery_seconds` và `evidence_to_incident_seconds`. Đây là độ trễ từng đoạn; **không phải MTTD từ lúc fault injection**. Chưa có benchmark MTTD.
- Tunnel chỉ forward POST `/api/v1/webhooks/datadog`, mọi đường khác trả 404. Endpoint cần secret riêng.

## 4. Checklist nghiệm thu thật (chưa thực hiện)

1. check_mongo đạt trên MongoDB thật; healthy run không mở incident.
2. Run datadog có metrics/logs thật, monitor thực sự Triggered, webhook vào inbox, đúng một incident `source=datadog`.
3. So sánh cửa sổ count/mean local với Datadog, gồm residual âm, mất mẫu và độ trễ gửi; kiểm tra worker không chấp nhận evidence thiếu chất lượng.
4. Retry cùng webhook không tạo incident thứ hai; event run đã dừng/đến sai thứ tự không tạo incident mới.
5. Recovery không đóng incident; dừng simulator/mất dữ liệu không được coi đã phục hồi.
6. Restart API/worker giữ inbox/incident; lưu logs, ảnh dashboard/monitor và timestamps đã che thông tin nhạy cảm vào artifacts. Không đưa URL tunnel/secrets vào repo.

Kiểm thử logic:

```powershell
uv run pytest -q
uv run ruff check chargeops scripts tests
docker compose --profile observability config --quiet
```

Tests mongomock không thay thế kiểm thử MongoDB time series/index thật. TestClient hiện có cảnh báo deprecation httpx từ Starlette; tests vẫn chạy, chưa nâng transport ngoài phạm vi gate.

## Nguồn triển khai

- [MongoDB time-series indexes](https://www.mongodb.com/docs/manual/core/timeseries/timeseries-index/): time-series không có unique index; deduplicate trong aggregation, unique incident ở collection thường.
- [Datadog monitor evaluation](https://docs.datadoghq.com/monitors/guide/as-count-in-monitor-evaluations/): chọn thứ tự tổng hợp trước phép chia.
- [Datadog webhook variables](https://docs.datadoghq.com/integrations/webhooks/): mapping payload và custom headers.
- [Datadog webhook API](https://docs.datadoghq.com/api/latest/webhooks-integration/create-a-webhooks-integration/): cấu hình webhook qua API.
