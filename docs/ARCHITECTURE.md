# ChargeOps — Kiến trúc mở rộng sau cuộc thi

Cập nhật 17/10/2026. Tài liệu này tách **lõi sản phẩm** khỏi **phần riêng cho cuộc thi**, để code viết trong 17–31/10 dùng tiếp được dù kết quả thi thế nào. Đây là ràng buộc thiết kế, không phải yêu cầu xây thêm tính năng trong khung build: mỗi seam dưới đây chỉ cần một interface và **một** implementation cho bản thi.

## 1. Nguyên tắc

- Lõi miền (telemetry, baseline, detector, incident state machine, command) không import simulator, Datadog, Caddy hay tunnel. Phụ thuộc chỉ đi từ adapter vào lõi.
- Mọi thứ gắn với cuộc thi (trial Datadog, trần 20 `run_id`, Quick Tunnel, kịch bản demo, A/B/C evaluation) nằm trong adapter hoặc cấu hình, xóa được mà không sửa lõi.
- Không thêm hạ tầng mới (Kafka, Redis, Kubernetes) trước khi có số đo chứng minh MongoDB + một worker không đủ.
- Mỗi quyết định khó đảo ngược ghi thành ADR ngắn trong `docs/adr/` (bối cảnh, quyết định, hệ quả).

## 2. Seams bắt buộc ngay trong bản thi

| Seam | Interface lõi | Implementation bản thi | Implementation sau thi |
|---|---|---|---|
| Nguồn telemetry | `TelemetrySource` → mẫu chuẩn hóa theo CONTRACT §1 | Simulator HTTP batch | Replay dataset; gateway OCPP `MeterValues` (measurand `Current.Import`, `Voltage`, `Power.Active.Import`, `Temperature`) |
| Lệnh tới thiết bị | `ChargerCommandSink` với `limit_current(connector, target_a, command_id)` | Simulator poll/ACK | OCPP `SetChargingProfile` (giới hạn dòng theo A), `ClearChargingProfile` khi khôi phục |
| Detector | `Detector.evaluate(window) → signal` có `model_version` | Residual tuyến tính theo tải/ambient | Mô hình học máy hoặc hệ số theo từng model connector; chạy song song shadow mode |
| Nguồn alert | `AlertSource` ghi event vào inbox | Local detector; Datadog webhook | Bất kỳ hệ alert nào gửi webhook chuẩn hóa |
| Metrics/traces | `MetricsSink`, tracing qua thư viện chuẩn | DogStatsD + `ddtrace` | Datadog trả phí, hoặc OpenTelemetry → Prometheus/Grafana; Datadog Agent cũng nhận OTLP |
| Logs | JSON một dòng ra stdout, có `run_id`, `connector_id`, `incident_id`, `trace_id` | Datadog Log Management qua Agent | Bất kỳ collector nào đọc stdout |
| Xác thực | `Identity` với vai trò `viewer`/`operator` | User cấu hình local, session cookie | OIDC/SSO; thêm `org_id` vào session |

**Trạng thái 21/10:** bảng trên là thiết kế đích, chưa được hiện thực thành interface. Hiện có ngầm định: nguồn telemetry là HTTP batch (`POST /telemetry/batch`, bất kỳ client nào gửi đúng CONTRACT §1 đều dùng được) và metrics qua một đối tượng `stats` duck-typed (worker nhận `DogStatsd` hoặc `Mock` trong test). **Chưa hiện thực:** `ChargerCommandSink` và ánh xạ OCPP (simulator poll `/simulator/commands` trực tiếp, giá trị lệnh nằm trong `runs.sim`); abstraction `Detector` (residual cài thẳng trong `domain.py`/`worker.py`); `AlertSource` qua inbox cho alert local (detector local mở incident trực tiếp, chỉ webhook Datadog đi qua inbox); `Identity` có vai trò (chỉ có `operator`, GET ẩn danh). Lõi import trực tiếp `ddtrace` (`chargeops/logging.py`, `worker.py`) và `DogStatsd` (`worker.py`), nên chưa tách khỏi Datadog.

Simulator và OCPP đều phải đi qua cùng `ChargerCommandSink`: lệnh giảm tải tuyệt đối `target_current_a` trong CONTRACT §3 ánh xạ trực tiếp sang charging profile, nên luồng giảm tải → kiểm chứng bằng telemetry giữ nguyên khi thay simulator bằng trụ thật.

## 3. Mô hình dữ liệu để mở rộng

- Định danh phân cấp `org_id → site_id(station) → charger_id → connector_id`. Bản thi dùng một `org_id` cố định trong cấu hình; mọi index và metaField time series có chỗ cho nó ngay từ đầu để không phải migrate dữ liệu sau.
- `run_id` là khái niệm demo/evaluation. Ở chế độ vận hành thật, telemetry không cần `run_id`; giữ trường nullable và không dùng làm khóa duy nhất của incident ngoài chế độ demo.
- Mọi document có `schema_version`. Thay đổi schema theo kiểu thêm trường; đọc được version cũ.
- Cấu hình baseline/detector/policy lưu thành document bất biến có version; incident và evaluation tham chiếu version đã dùng.

## 4. Đường mở rộng tải

Bản thi: 6 connector × mẫu 5 giây ≈ 1,2 mẫu/giây. Thứ tự nâng cấp khi có số đo thực tế cho thấy nghẽn:

1. **Polling → Change Streams** trên collection thường (`incident_events`, `commands`, `webhook_inbox`) để worker nhận việc theo sự kiện. Change Streams không hỗ trợ time series collection; telemetry vẫn đọc bằng aggregation theo cửa sổ. Change Streams cần replica set, nên Compose chạy MongoDB một node replica set ngay từ bản thi.
2. **Nhiều worker** chia theo `hash(connector_id)`; vẫn chỉ một writer cho mỗi incident nhờ unique partial index và cập nhật có điều kiện theo state.
3. **Aggregation tăng dần**: lưu block 15 giây đã tính vào collection riêng (`$merge`) thay vì tính lại từ mẫu thô.
4. **Sharding** time series theo metaField `org_id/site_id`, hoặc MongoDB Atlas khi có ngân sách.

## 5. Phần riêng cho cuộc thi

Gom vào `apps/demo/` hoặc module `chargeops.demo`, cấu hình bật/tắt được:

- Trần 20 unique `run_id`/trial Datadog và bộ đếm bền vững.
- Quick Tunnel và listener webhook Caddy.
- `POST /demo-runs`, `inject-fault`, kịch bản healthy/load_increase/demand_drop/thermal_fault.
- Runner evaluation A/B/C và đồng hồ mô phỏng.

Sau cuộc thi, repo còn chạy được ở local mode với simulator; tắt profile `observability` khi trial hết hạn. Không giữ API key trial.

## 6. Lộ trình sau thi

| Giai đoạn | Mục tiêu | Điều kiện bắt đầu |
|---|---|---|
| P1 — dọn | Ghi ADR, chọn giấy phép mã nguồn, README tiếng Anh song song, gỡ phần riêng cuộc thi khỏi đường mặc định | Sau 22/11/2026 (hết thời hạn giám khảo truy cập repo đóng băng); làm trên nhánh mới |
| P2 — thiết bị thật | Gateway OCPP 1.6J/2.0.1 với charger mô phỏng mã nguồn mở, rồi một trụ thật | P1 xong; có đối tác hoặc thiết bị thử |
| P3 — nhiều site | `org_id` thật, OIDC, phân quyền theo site | Có người dùng thứ hai |
| P4 — mô hình | Hiệu chỉnh baseline từ dữ liệu thật, shadow detector, so sánh với residual tuyến tính bằng cùng protocol A/B/C | Có ít nhất vài tuần telemetry thật |

Mỗi giai đoạn giữ nguyên nguyên tắc của bản thi: báo số đo thực, tách dữ liệu mô phỏng khỏi dữ liệu thật, không tuyên bố an toàn thiết bị từ simulator.
