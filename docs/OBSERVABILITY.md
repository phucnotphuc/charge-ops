# Quan sát hệ thống (Datadog)

Tài liệu này mô tả các tín hiệu, dashboard, monitor và cách điều tra một sự cố nhiệt. Tất cả được tạo bởi `scripts/setup_datadog.py` (chạy lại là cập nhật tại chỗ theo id trong `artifacts/datadog-resources.json`).

## 1. Tín hiệu theo chế độ chạy

| Tín hiệu | Chế độ local | Chế độ `datadog` |
| --- | --- | --- |
| Log JSON (`service:chargeops`) | có | có |
| Metric telemetry (`chargeops.samples`, `residual_*`, `quality`, nhiệt, dòng sạc) | không | có |
| Metric sự cố `chargeops.incident.open`, `chargeops.incident.state_age_s` (tag `state:<trạng thái>`) | không | có |
| Event Datadog gắn tag `source:chargeops` (có thể mang `incident_id`) | không | có |
| Heartbeat `chargeops.worker.tick` | chỉ khi `DD_METRICS_ENABLED=true` | có nếu `DD_METRICS_ENABLED=true` |

`DD_METRICS_ENABLED` mặc định là `false`. Đặt `DD_METRICS_ENABLED=true` trong `.env` rồi tạo lại worker để bật heartbeat. Metric chỉ dùng tag `run_id`, `connector_id`, `station_id`, `env:hackathon` và `state:<trạng thái>` (riêng `state_age_s`); không có `incident_id` hay `session_id` trong tag metric.

## 2. Dashboard

### Kỹ sư điện / vận hành trạm

| Widget | Truy vấn | Trả lời câu hỏi |
| --- | --- | --- |
| Trạng thái cảnh báo | danh sách monitor `ChargeOps` | Monitor nào đang cảnh báo? |
| Residual trung bình theo connector | `(residual_positive - residual_negative) / samples` | Connector nào nóng bất thường nhất? |
| Residual nhiệt (°C, trung bình theo mẫu) | cùng công thức, vạch 8 °C và 3 °C, lớp phủ event `tags:source:chargeops` | Residual vượt ngưỡng lúc nào, sự cố mở/đóng lúc nào? |
| Nhiệt đo được và nhiệt kỳ vọng theo tải (°C) | `connector_temperature_c`, `expected_temperature_c` | Nhiệt thực tế lệch bao nhiêu so với kỳ vọng? |
| Dòng sạc và giới hạn dòng (A) | `current_a`, `current_limit_a`, lớp phủ event | Giảm tải đã được áp dụng chưa? |
| Nhiệt độ môi trường (°C) | `ambient_temperature_c` | Nhiệt độ cao do môi trường hay do thiết bị? |
| Sự cố đang mở theo connector | `sum:chargeops.incident.open by {connector_id}`, lớp phủ event | Connector nào đang có sự cố mở? |
| Thời gian ở trạng thái hiện tại (s) | `max:chargeops.incident.state_age_s by {connector_id,state}`, vạch 600 s | Sự cố kẹt ở một trạng thái bao lâu? |
| Webhook và lệnh vận hành | log `webhook_*`, `command_*` | Hệ thống đã gửi/áp dụng/từ chối lệnh nào? |

### IT / SRE nền tảng

| Widget | Truy vấn | Trả lời câu hỏi |
| --- | --- | --- |
| Trạng thái monitor | danh sách monitor | Monitor nào đang cảnh báo? |
| API: request, độ trễ p50/p95, nhóm mã HTTP | `trace.fastapi.request*` | API có khỏe không? |
| Simulator → API | `trace.http.request.hits.by_http_status` | Simulator gửi request có thành công không? |
| Worker: số vòng xử lý và độ trễ p95 | `trace.worker.tick*` | Worker có chạy đều không? |
| Mẫu telemetry nhận theo connector | `chargeops.samples` | Dữ liệu có đến đều không? |
| Chất lượng cửa sổ telemetry | `chargeops.quality` | Cửa sổ dữ liệu hợp lệ hay thiếu/cũ? |
| Log theo sự kiện | log `service:chargeops` nhóm theo `@event` | Hệ thống đang ghi nhận loại sự kiện nào? |
| Lỗi, retry và lệnh bị từ chối | log lỗi, `ingest_retry`, `worker_retry`, `command_rejected` | Có lỗi hoặc retry nào không? |
| Worker heartbeat (tick / phút) | `sum:chargeops.worker.tick{env:hackathon}.as_rate()` nhân 60 | Worker còn sống không? |

## 3. Monitor

| Monitor | Truy vấn | Ngưỡng | Webhook |
| --- | --- | --- | --- |
| `ChargeOps thermal residual` (thermal) | residual trung bình theo mẫu, `last_1m` | > 8 °C, phục hồi 3 °C | không |
| `ChargeOps valid telemetry window` (quality) | `min:chargeops.quality`, `last_1m` | > 0.5 | không |
| `ChargeOps thermal incident` (composite) | `thermal && quality` | cả hai cùng cảnh báo | **có** (`@webhook-chargeops-minimal`), duy nhất |
| `ChargeOps telemetry stale` (stale) | `sum:chargeops.samples` theo `run_id,connector_id`, `last_2m` | < 12 mẫu; `notify_no_data` bật (4 phút) | không |
| `ChargeOps worker heartbeat` (worker) | `sum:chargeops.worker.tick`, `last_2m` | < 1; `notify_no_data` bật (4 phút) | không |
| `ChargeOps derating not resolving` (stuck) | `max:chargeops.incident.state_age_s{state:mitigating}`, `last_5m` | > 600 s | không |

Lưu ý: monitor `stale` cũng chuyển sang cảnh báo no-data khoảng 4 phút sau khi một run kết thúc (bình thường, không gọi webhook). Monitor `worker` sẽ cảnh báo bất cứ khi nào `DD_METRICS_ENABLED=false`, vì khi đó không có heartbeat.

Chỉ monitor composite gọi webhook; các monitor còn lại chỉ có link điều tra trong nội dung cảnh báo.

## 4. Lộ trình điều tra một sự cố

1. Monitor composite cảnh báo. Nội dung chứa link dashboard kỹ sư dự kiến lọc sẵn (chưa kiểm chứng) theo `run_id` và `connector_id` (template variable) và link Logs.
2. Mở dashboard kỹ sư: xem biểu đồ "Residual nhiệt" với vạch 8 °C / 3 °C và các event `source:chargeops` phủ lên để biết sự cố mở, giảm tải, phục hồi lúc nào.
3. Xem "Dòng sạc và giới hạn dòng": dòng có bị hạ xuống giới hạn mới sau lệnh giảm tải không.
4. Xem "Thời gian ở trạng thái hiện tại" và "Sự cố đang mở": sự cố có kẹt ở trạng thái giảm tải quá 600 s không (monitor `stuck`).
5. Xem log stream "Webhook và lệnh vận hành" để thấy lệnh đã gửi, được áp dụng hay bị từ chối.
6. Mở link Logs trong cảnh báo để lọc toàn bộ log của `run_id`.
7. Từ một dòng log, lấy `dd.trace_id` và mở trace APM tương ứng trên dashboard IT/SRE để xem độ trễ và lỗi của request hoặc vòng worker.

## 5. Chưa kiểm chứng thật

Các điểm sau chưa được chạy trên Datadog thật tại thời điểm viết tài liệu này; chúng sẽ được kiểm chứng ở Task 8:

- Truy vấn lớp phủ event `tags:source:chargeops`.
- Link template variable (`tpl_var_run_id`, `tpl_var_connector_id`) trong nội dung monitor composite.
- Widget heartbeat dùng `as_rate()`.
- Hành vi `no_data` của monitor worker.
