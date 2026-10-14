# ChargeOps

Hệ thống demo cảnh báo sớm bất thường nhiệt connector: telemetry → phát hiện → điều tra → operator duyệt giảm tải → bảo trì mô phỏng → kiểm tra phục hồi.

## Trạng thái ngày 14/10/2026

**Chỉ chuẩn bị tài liệu và repository. Chưa có ứng dụng, Docker Compose, dependencies cài cho dự án, hoặc kết quả kiểm thử sản phẩm.** Theo quyết định của nhóm, phần cốt lõi bắt đầu từ 17/10/2026. Không tuyên bố repo hiện chạy được.

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

## Khởi chạy sau khi triển khai

Hai lệnh mục tiêu dưới đây **chưa khả dụng trong repo chuẩn bị**:

```powershell
docker compose up --build
docker compose --profile observability up --build
```

Giao diện dự kiến `http://localhost:8080`. Sau khi build lần đầu tải đủ image/dependency, local mode phải chạy không cần Internet. Datadog mode cần Internet và trial còn hiệu lực. Video, slide, benchmark và ảnh demo chỉ được tạo từ hệ thống thực đã chạy; không có kết quả minh họa giả làm kết quả đo.
