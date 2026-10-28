# ChargeOps

Hệ thống demo cảnh báo sớm bất thường nhiệt connector: telemetry → phát hiện → điều tra → operator duyệt giảm tải → bảo trì mô phỏng → kiểm tra phục hồi.

## Trạng thái ngày 21/10/2026

**Đã viết và kiểm thử logic (mongomock/vitest):** fleet 2 trạm × 3 connector (mỗi connector replay một phiên Autosun–PolyU v3 đã kiểm định, lỗi gieo theo seed, 4 kịch bản), API FastAPI + đăng nhập operator, worker và vòng đời incident (giảm tải → bảo trì → kiểm tra phục hồi), quan sát Datadog (metrics/logs/APM, monitor/webhook), giao diện web React, đánh giá offline A/B/C (`b1-eval-v3.1`, kết quả mô phỏng trong `data/evaluation/`).

**Kiểm chứng thật:** gate ngày 20/10 chạy container, MongoDB thật và Datadog thật nhưng **chỉ với một connector** (xem [MINIMAL_LOOP](docs/MINIMAL_LOOP.md)). Fleet 6 connector, giao diện web, đăng nhập, Caddy/web image và endpoint đánh giá trên nhánh này **chưa chạy thật**. Phần viết trước 17/10 được khai báo trong DISCLOSURE.md.

- Local-first, mục tiêu chi phí dịch vụ 0 đồng; không EC2, Atlas, domain hay AI trả phí.
- GitHub private; cấp quyền giám khảo khi BTC cung cấp tài khoản.
- Datadog trial dùng trong thời gian thi; nghiệm thu cuộc thi phải có dashboard/monitor/logs hoặc APM thật.
- Dữ liệu công khai làm nền nếu đạt kiểm định; fault thiết bị và phản ứng can thiệp được mô phỏng, ghi rõ provenance.

## Tài liệu

- [Kiểm tra từ clone sạch](docs/KIEM_TRA.md): từng bước từ checkout tới trọn vòng sự cố, mode local và Datadog, kèm kết quả kỳ vọng.
- [tasks.md](tasks.md): checklist theo thể lệ cuộc thi, ưu tiên, phụ thuộc và bằng chứng nghiệm thu.
- [PLAN.md](PLAN.md): phạm vi, triển khai và nghiệm thu đã duyệt.
- [Dependencies](docs/DEPENDENCIES.md): công cụ và package dự kiến, cách khóa phiên bản khi build.
- [Contract](docs/CONTRACT.md): hợp đồng cho frontend/backend/simulator.
- [Architecture](docs/ARCHITECTURE.md): seams lõi/adapter, đường mở rộng tải và lộ trình sau cuộc thi.
- [Implementation](docs/IMPLEMENTATION.md): phân công và thứ tự sub-agent.
- [Data card](docs/DATA_CARD.md): nguồn, giấy phép, giới hạn bằng chứng.
- [Environment](docs/ENVIRONMENT.md): trạng thái môi trường đã kiểm tra.
- [Disclosure](docs/DISCLOSURE.md): khai báo phần chuẩn bị trước thời gian build và AI.
- [Runbook luồng tối thiểu](docs/MINIMAL_LOOP.md): cách chạy, thiết lập Datadog, kiểm chứng và giới hạn hiện tại.

## Khởi chạy từ clone sạch

Hướng dẫn đầy đủ, có kết quả kỳ vọng từng bước: [docs/KIEM_TRA.md](docs/KIEM_TRA.md).

Cần Docker Linux Engine đang chạy (không cần Node trên máy host: image `web` tự build UI). Script `init_env.py` không ghi đè `.env` cũ và không in secrets.

```powershell
git clone <repo> chargeops; cd chargeops
python scripts/init_env.py --operator-user operator   # hỏi mật khẩu operator; hash được ghi trong dấu nháy đơn
docker compose up --build -d
```

1. Mở http://localhost:8080 (giao diện web; `/api/*`, `/docs`, `/redoc`, `/openapi.json` được proxy tới API trong cùng origin).
2. Đăng nhập operator bằng user/mật khẩu vừa đặt. Không cần profile `observability` cho chế độ local.
3. Kiểm tra hạ tầng: `docker compose exec api python -m chargeops.check_mongo`; CLI: `docker compose exec api python -m chargeops.cli start --mode local`.

Cấu hình trong `.env` (xem `.env.example`): `OPERATOR_USERNAME`, `OPERATOR_PASSWORD_HASH` (nếu tự dán hash từ `python -m chargeops.auth hash`, giữ nguyên và đặt trong dấu nháy đơn, ví dụ `OPERATOR_PASSWORD_HASH='scrypt$16384$8$1$…'`; Compose và pydantic-settings đều đọc giá trị nháy đơn nguyên văn nên cùng một dòng dùng được cho container và khi chạy trên host; không viết `$$` vì khi chạy trên host giá trị bị đọc nguyên `$$` và đăng nhập thất bại. Đã kiểm bằng `docker compose config` và python-dotenv, chưa kiểm bằng container chạy thật), `ALLOWED_ORIGINS` (mặc định `http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173`), `DD_TRACE_ENABLED`, `APP_VERSION`. Có thể truyền mật khẩu không tương tác: `python scripts/init_env.py --operator-user operator --operator-password-stdin`.

Kịch bản `thermal_fault` gieo lỗi trên một connector theo seed, bắt đầu từ giây 120–300; mỗi run tự dừng sau 30 phút (`RUN_MAX_S = 1800`). `POST /auth/login` và `/auth/logout` từ chối Origin lạ; client không gửi Origin (curl, CLI) vẫn dùng được. Mode local không thay nghiệm thu Datadog; xem runbook để thiết lập dashboard/monitor/webhook và chạy mode datadog (`docker compose --profile observability up`). Gateway webhook chỉ mở `/api/v1/webhooks/datadog`.

Kiểm thử:

```powershell
uv sync --frozen; uv run pytest -q; uv run ruff check chargeops scripts tests
cd web; npm ci; npm test -- --run; npm run build
```

Khi API đổi: `uv run python scripts/export_openapi.py` rồi `cd web; npm run gen:api` (hoặc `npm run check:api` để kiểm tra `schema.d.ts` không lệch). Tests Python dùng MongoDB giả lập chỉ chứng minh logic; `chargeops.check_mongo` cần MongoDB thật.

**Chưa chạy thật:** `docker compose up --build`, build image `web`/Caddy, đăng nhập qua http://localhost:8080 và MongoDB/Datadog thật (Docker Engine chưa chạy). Mới xác nhận `docker compose config` hợp lệ cho profile mặc định và `observability`, cùng pytest/ruff/vitest/`npm run build`. Video/benchmark chỉ được tạo từ hệ thống thực đã chạy, không thay kết quả thật bằng fixture.
