# ChargeOps

Hệ thống demo cảnh báo sớm bất thường nhiệt connector: telemetry → phát hiện → điều tra → operator duyệt giảm tải → bảo trì mô phỏng → kiểm tra phục hồi.

## Trạng thái ngày 14/10/2026

**Đã viết luồng tối thiểu theo yêu cầu bắt đầu ngay của người dùng ngày 14/10:** một connector tổng hợp → ingest FastAPI → MongoDB time series/aggregation → metrics Datadog → webhook → worker tạo incident. Có Compose, lockfile và kiểm thử logic/API. **Chưa kiểm chứng container/MongoDB/Datadog thật** vì Docker Engine chưa chạy và keys chưa được cấu hình trong môi trường dự án. Phần viết trước 17/10 được khai báo trong DISCLOSURE.md.

- Local-first, mục tiêu chi phí dịch vụ 0 đồng; không EC2, Atlas, domain hay AI trả phí.
- GitHub private; cấp quyền giám khảo khi BTC cung cấp tài khoản.
- Datadog trial dùng trong thời gian thi; nghiệm thu cuộc thi phải có dashboard/monitor/logs hoặc APM thật.
- Dữ liệu công khai làm nền nếu đạt kiểm định; fault thiết bị và phản ứng can thiệp được mô phỏng, ghi rõ provenance.

## Tài liệu

- [tasks.md](tasks.md): checklist theo thể lệ cuộc thi, ưu tiên, phụ thuộc và bằng chứng nghiệm thu.
- [PLAN.md](PLAN.md): phạm vi, triển khai và nghiệm thu đã duyệt.
- [Dependencies](docs/DEPENDENCIES.md): công cụ và package dự kiến, cách khóa phiên bản khi build.
- [Contract](docs/CONTRACT.md): hợp đồng cho frontend/backend/simulator.
- [Implementation](docs/IMPLEMENTATION.md): phân công và thứ tự sub-agent.
- [Data card](docs/DATA_CARD.md): nguồn, giấy phép, giới hạn bằng chứng.
- [Environment](docs/ENVIRONMENT.md): trạng thái môi trường đã kiểm tra.
- [Disclosure](docs/DISCLOSURE.md): khai báo phần chuẩn bị trước thời gian build và AI.
- [Runbook luồng tối thiểu](docs/MINIMAL_LOOP.md): cách chạy, thiết lập Datadog, kiểm chứng và giới hạn hiện tại.

## Khởi chạy luồng tối thiểu

Cần Docker Linux Engine đang chạy. Tạo `.env` nếu chưa có; script không ghi đè file cũ và không in secrets. Chạy local trước để kiểm tra hạ tầng:

```powershell
python scripts/init_env.py
docker compose up --build -d
docker compose exec api python -m chargeops.check_mongo
docker compose exec api python -m chargeops.cli start --mode local
docker compose exec api python -m chargeops.cli status
```

API/OpenAPI tại `http://localhost:8080/docs`, chưa có frontend. Kịch bản fault bắt đầu tăng nhiệt sau 120 giây; mỗi run tự dừng sau 15 phút. Mode local không thay nghiệm thu Datadog; xem runbook để thiết lập dashboard/monitor/webhook và chạy mode datadog. Không có command giảm tải/bảo trì/retest trong bản đầu này.

Kiểm thử: `uv sync --frozen`, `uv run pytest -q`, `uv run ruff check chargeops scripts tests`. Tests dùng MongoDB giả lập chỉ chứng minh logic; `chargeops.check_mongo` là kiểm tra riêng cần MongoDB thật. Image tags đã chọn nhưng chưa build/pull kiểm chứng. Video/benchmark chỉ được tạo từ hệ thống thực đã chạy, không thay kết quả thật bằng fixture.
