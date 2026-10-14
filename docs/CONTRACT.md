# ChargeOps — Hợp đồng triển khai v1

Tài liệu chuẩn bị ngày 14/10/2026; phần cốt lõi chỉ bắt đầu từ 17/10/2026 theo lựa chọn của nhóm. Đây là thiết kế, chưa phải API đã tồn tại. Agent chính sở hữu thay đổi hợp đồng; backend, frontend và simulator triển khai theo cùng phiên bản này.

## 1. Quy ước và dữ liệu

REST JSON dưới `/api/v1`; ID là chuỗi UUID, thời gian ISO 8601 UTC có hậu tố `Z`. Giao diện hiển thị `Asia/Ho_Chi_Minh`. Danh sách trả `{items: [...], next_cursor: string|null}`, mặc định 100, tối đa 1.000 phần tử; telemetry dùng khoảng `from`/`to` bắt buộc, tối đa 24 giờ mỗi request. Lỗi trả `{error: {code, message, request_id}}`; 401 chưa đăng nhập, 403 sai vai trò, 409 xung đột trạng thái/idempotency, 422 dữ liệu sai. Giá trị thiếu là `null`, không thay bằng 0.

Mỗi mẫu telemetry gồm:

| Trường | Ý nghĩa |
|---|---|
| `sample_id`, `run_id`, `station_id`, `charger_id`, `connector_id`, `session_id` | Định danh; `session_id` có thể null |
| `measured_at`, `received_at` | Thời điểm đo; API tự ghi thời điểm nhận |
| `voltage_v`, `current_a`, `power_kw` | V, A, kW; công suất là giá trị đo, không suy ra thay thế âm thầm |
| `connector_temperature_c`, `ambient_temperature_c` | Độ C |
| `requested_current_a`, `current_limit_a` | Nhu cầu xe và giới hạn dòng đang áp dụng |
| `connector_status`, `session_status` | `available/charging/faulted/offline`; `idle/active/completed/interrupted` |
| `provenance` | `{composition: synthetic|public_replay|hybrid, manifest_id: string}`; manifest bất biến có version và nguồn theo từng trường |

Mọi trường số phải hữu hạn. Các đại lượng điện/tải không âm; không dùng giới hạn nhiệt tùy ý để loại chính mẫu bất thường cần phát hiện. API chỉ nhận mẫu thuộc run đang hoạt động và connector hợp lệ. Metadata time series gồm run/station/charger/connector. Mẫu trùng được loại theo `(run_id, sample_id)` trong aggregation; latest-state cập nhật theo thời gian đo, không theo thứ tự đến. Telemetry TTL 14 ngày; bằng chứng incident lưu riêng ngoài TTL.

Hai trạm, mỗi trạm ba connector. Ground truth lỗi và seed lưu trong dữ liệu simulator/evaluation, tách khỏi đầu vào detector. Dataset công khai chỉ làm mẫu đường tải sau kiểm định; nhãn lỗi pin không trở thành nhãn lỗi charger. `composition` mô tả toàn mẫu. Manifest được telemetry tham chiếu ánh xạ từng trường sang `public`, `synthetic` hoặc `derived`, kèm source/version/checksum cho public, model/version cho synthetic, công thức và input fields cho derived. Ví dụ tải public và nhiệt simulator có composition hybrid, trường nhiệt synthetic; residual là derived. Nhiệt tương tác không được gắn public chỉ vì tải lấy từ dataset. Giữ manifest khi telemetry hết TTL để đọc lại bằng chứng incident.

## 2. API và quyền sở hữu

| Method/path | Input hoặc output chính | Quyền |
|---|---|---|
| `GET /health`, `GET /ready` | Liveness; readiness kiểm tra MongoDB | Công khai local |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Username/password; session HttpOnly; role | Local |
| `GET /connectors`, `GET /connectors/{id}` | Latest telemetry, freshness, active incident, mode | Viewer |
| `GET /connectors/{id}/telemetry` | `run_id, from, to, cursor`; chuỗi đo và nhiệt kỳ vọng | Viewer |
| `GET /incidents`, `GET /incidents/{id}` | State, evidence snapshot, timeline, command IDs, source | Viewer |
| `POST /incidents/{id}/commands` | `{type: acknowledge|reduce_load|maintenance|verify_recovery}`, header `Idempotency-Key` | Operator |
| `GET /commands/{id}` | `pending/claimed/applied/failed/timed_out`, timestamps, result/error | Viewer |
| `POST /demo-runs` | `{scenario: healthy|load_increase|demand_drop|thermal_fault, seed: integer}` | Operator |
| `POST /demo-runs/{id}/start`, `POST /demo-runs/{id}/stop` | Yêu cầu start/stop; chỉ một run hoạt động | Operator |
| `GET /demo-runs`, `GET /demo-runs/{id}` | State, seed, mode, scenario, cấu hình đã khóa | Viewer |
| `POST /demo-runs/{id}/inject-fault` | `{connector_id}`; ghi event điều khiển simulator | Operator |
| `GET /evaluations`, `GET /evaluations/{id}` | Kết quả thật, seed, baseline/config version | Viewer |
| `POST /telemetry/batch` | `{samples: [...]}`, tối đa 1.000 mẫu; kiểm tra toàn bộ trước ghi | Simulator secret |
| `GET /simulator/commands` | Command chưa xác nhận của run hoạt động | Simulator secret |
| `POST /simulator/commands/{id}/result` | `{status: applied|failed, applied_at, result}`; ghi event kết quả | Simulator secret |
| `POST /webhooks/datadog` | Payload chuẩn hóa ở mục 4 | Webhook secret |

Request tạo command trả 202 và command ID; không trả thông báo đã giảm tải. API ghi yêu cầu/event bền vững; **worker duy nhất ghi chuyển trạng thái incident**. Start/stop cũng là yêu cầu bất đồng bộ để tránh API/simulator tranh quyền điều khiển. Frontend poll mỗi 5 giây; OpenAPI backend là nguồn sinh TypeScript types. Simulator chỉ poll/apply command liên quan tới run của nó.

## 3. Detector và trạng thái

Telemetry mỗi 5 giây; worker tạo các block residual 15 giây không chồng lặp theo thời gian đo, với tổng residual và số mẫu khác nhau. Local và Datadog chỉ áp dụng **một cửa sổ 60 giây**, lấy tổng residual chia tổng số mẫu của bốn block; không xuất rolling mean 60 giây rồi lại làm mượt thêm 60 giây trong Datadog. Cửa sổ cần ít nhất 10 mẫu khác nhau, không có khoảng trống trên 15 giây và mẫu cuối không cũ quá 30 giây. Nếu thiếu block hoặc không đạt chất lượng, trạng thái `insufficient_data` chặn cả alert lẫn recovery và hủy bộ đếm ổn định.

Baseline nhiệt có xét tải, nhiệt môi trường và quán tính nhiệt. Hiệu chỉnh trên các lượt khỏe riêng, ghi hệ số, seed và phiên bản cấu hình. Tránh so sánh nhiệt động với baseline tĩnh gây cảnh báo khi tải tăng bình thường. Alert khi residual trung bình cửa sổ 60 giây hợp lệ >8°C; không yêu cầu mọi mẫu liên tục >8°C. Recovery signal khi trung bình này <3°C. Hiệu chỉnh bằng tập tuning, khóa trước tập đánh giá. Đây là ngưỡng mô phỏng, không phải chuẩn an toàn trụ sạc.

| Từ → đến | Điều kiện worker áp dụng |
|---|---|
| Chưa có → `open` | Alert hợp lệ, run còn hoạt động; snapshot bằng chứng được lưu |
| `open` → `acknowledged` | Operator acknowledge |
| `acknowledged` → `mitigating` | Worker nhận yêu cầu giảm tải hợp lệ; phát command cho simulator |
| `mitigating` → `mitigated` | Command đã áp dụng; residual phục hồi liên tục 60 giây với dữ liệu đủ |
| `mitigated` → `verifying` | Bảo trì đã áp dụng; operator yêu cầu kiểm tra; simulator xác nhận khôi phục giới hạn tải gốc |
| `verifying` → `resolved` | Trong cùng trọn cửa sổ kiểm tra 120 giây, residual từng mẫu <3°C và cả requested/actual current từng mẫu >=90% `reference_current_a`; mọi cửa sổ con 60 giây đạt quality gate |

`reference_current_a` được đóng băng bằng trung bình dòng thực tế của cửa sổ 60 giây hợp lệ mới nhất ngay trước lần giảm tải đầu tiên, lưu cùng evidence. Cửa sổ thiếu chất lượng hoặc trung bình bằng 0 thì chưa đủ điều kiện giảm tải/kiểm tra phục hồi; yêu cầu thu lại baseline tải hợp lệ, không tự dùng giới hạn dòng thay thế. Trong retest, bất kỳ mẫu tải thấp, residual >=3°C hoặc thiếu/stale dữ liệu đều reset đồng hồ 120 giây. Nếu `verifying` có alert nóng lại hợp lệ, trở về `open` và ghi event tái phát. Stop run ghi lý do kết thúc demo, giữ trạng thái incident chưa giải quyết; không giả là đã sửa thiết bị.

Command giảm tải đặt giới hạn tuyệt đối = 50% giới hạn gốc, lưu gốc trước can thiệp; không nhân 50% vào giá trị hiện tại. Cùng idempotency key và payload trả lại command cũ; cùng key khác payload trả 409. Command chưa xác nhận sau 30 giây chuyển timeout, incident vẫn chưa giải quyết. Kết quả đến sau timeout được lưu như event đến muộn, worker đối soát với tải thực trước khi cho thao tác tiếp. Simulator lưu applied command ID để restart không áp dụng hai lần. Bảo trì chỉ loại nguyên nhân lỗi mô phỏng; verify mới khôi phục tải.

## 4. Local và Datadog

Mode là cấu hình bất biến của run: `local` hoặc `datadog`. Local tạo alert event từ detector. Datadog xuất tổng residual/số mẫu của block 15 giây không chồng lặp, freshness và quality; monitor tính duy nhất mean 60 giây theo mục 3. Quality gate là điều kiện đồng thời của monitor và được worker kiểm tra lại trước xử lý webhook; thiếu block hoặc dữ liệu không đạt chặn alert/recovery, không nội suy lỗ trống. Datadog recovery không đóng incident.

Webhook có `event_id`, `monitor_id`, `run_id`, `connector_id`, `occurred_at`, `status: alert|recovery`, `message`. Dùng tag `run_id` duy nhất cho metric live Datadog để template giữ đúng tương quan. Đây là ngoại lệ cardinality dành riêng demo: tối đa **20 run ID duy nhất trên mỗi trial đã cấu hình**, bộ đếm bền vững qua restart; đạt trần thì chặn start Datadog run mới. Không tái sử dụng ID và không export offline evaluation. Kết thúc run trước khi bắt đầu run kế tiếp.

API xác thực secret, ghi inbox bền vững trước khi trả 202; unique event ID chống lặp. Payload thiếu event/run/monitor/connector identity hoặc timestamp hợp lệ được lưu quarantine với lý do, không tự tạo event identity hoặc gán run đang hoạt động. Worker bỏ qua event của run đã kết thúc, connector không thuộc run hoặc event cũ hơn trạng thái monitor đã xử lý. Unique partial index bảo đảm một incident chưa resolved cho mỗi `(run_id, connector_id, thermal_anomaly)`.

Datadog không khả dụng không kích hoạt fallback local. UI hiển thị mode và lỗi quan sát; dữ liệu/commands local vẫn được lưu. Cổng tunnel chỉ phục vụ endpoint webhook, không công khai API điều khiển. Secret tách biệt cho webhook/simulator, cấu hình qua môi trường; session mutation dùng kiểm tra Origin/CSRF, cookie SameSite. Không đặt secrets vào log hoặc frontend.
