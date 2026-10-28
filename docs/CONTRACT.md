# ChargeOps — Hợp đồng triển khai v1

**Phạm vi hiện thực ngày 21/10 (đã viết và có unit test mongomock/vitest; chưa chạy thật trên nhánh này):** 2 trạm × 3 connector, bốn scenario theo seed, inject-fault qua simulator, worker theo từng connector; ingest, alert/inbox/incident, baseline có quán tính nhiệt, commands (`acknowledge`/`reduce_load`/`maintenance`/`verify_recovery`), kiểm chứng giảm tải và retest theo §3; đăng nhập operator bằng session cookie (giữ `X-Operator-Secret` cho CLI); `/demo-runs`, `/connectors`, telemetry API; `GET /evaluations` đọc bản tóm tắt offline đã commit; giao diện web. Lần chạy thật duy nhất là gate ngày 20/10 với **một connector** (MINIMAL_LOOP.md). **Chưa hiện thực:** vai trò viewer có đăng nhập (chỉ có `operator`), cursor phân trang cho `/incidents`/`/demo-runs` (luôn `next_cursor: null`, tối đa 100), response model cho incident/command/run thô, seam `ChargerCommandSink`/OCPP và các seam khác ở ARCHITECTURE §2. Ngày triển khai sớm ghi tại DISCLOSURE.md.

Tài liệu chuẩn bị ngày 14/10/2026; phần cốt lõi chỉ bắt đầu từ 17/10/2026 theo lựa chọn của nhóm. Đây là thiết kế, chưa phải API đã tồn tại. Agent chính sở hữu thay đổi hợp đồng; backend, frontend và simulator triển khai theo cùng phiên bản này.

## 1. Quy ước và dữ liệu

REST JSON dưới `/api/v1`; ID là chuỗi UUID, thời gian ISO 8601 UTC có hậu tố `Z`. Giao diện hiển thị `Asia/Ho_Chi_Minh`. Danh sách trả `{items: [...], next_cursor: string|null}`, mặc định 100, tối đa 1.000 phần tử; telemetry dùng khoảng `from`/`to` bắt buộc, tối đa 24 giờ mỗi request. Lỗi trả `{error: {code, message, request_id}}`; 401 chưa đăng nhập, 403 sai vai trò, 409 xung đột trạng thái/idempotency, 422 dữ liệu sai. Giá trị thiếu là `null`, không thay bằng 0.

Mỗi mẫu telemetry gồm:

| Trường | Ý nghĩa |
|---|---|
| `sample_id`, `org_id`, `run_id`, `station_id`, `charger_id`, `connector_id`, `session_id` | Định danh; `org_id` cố định trong bản thi; `run_id` chỉ bắt buộc ở chế độ demo; `session_id` có thể null |
| `schema_version` | Số nguyên, bắt đầu `1`; thay đổi schema theo kiểu thêm trường |
| `measured_at`, `received_at` | Thời điểm đo; API tự ghi thời điểm nhận |
| `voltage_v`, `current_a`, `power_kw` | V, A, kW; công suất là giá trị đo, không suy ra thay thế âm thầm |
| `connector_temperature_c`, `ambient_temperature_c` | Độ C |
| `requested_current_a`, `current_limit_a` | Nhu cầu xe và giới hạn dòng đang áp dụng |
| `connector_status`, `session_status` | `available/charging/faulted/offline`; `idle/active/completed/interrupted` |
| `provenance` | `{composition: synthetic|public_replay|hybrid, manifest_id: string}`; manifest bất biến có version và nguồn theo từng trường |

Mọi trường số phải hữu hạn. Các đại lượng điện/tải không âm; không dùng giới hạn nhiệt tùy ý để loại chính mẫu bất thường cần phát hiện. API chỉ nhận mẫu thuộc run đang hoạt động và connector hợp lệ. Metadata time series gồm org/run/station/charger/connector. Mẫu trùng được loại theo `(run_id, sample_id)` trong aggregation; latest-state cập nhật theo thời gian đo, không theo thứ tự đến. Telemetry TTL 14 ngày; bằng chứng incident lưu riêng ngoài TTL.

Hai trạm, mỗi trạm ba connector, ID tổng hợp cố định: `station-1/charger-1/connector-1..3`, `station-2/charger-2/connector-4..6` (`domain.CONNECTORS`, `domain.LOCATION`); API suy ra `station_id`/`charger_id` từ `connector_id`. Mỗi bước 5 giây simulator gửi một `/telemetry/batch` gồm 6 mẫu, `sample_id = uuid5(run_id, "<connector_id>:<bước>")`, retry gửi lại đúng các ID đó. Mỗi connector replay một phiên Autosun khác nhau, có nhiệt độ, giới hạn dòng và lệnh riêng; `seed` của run quyết định phiên tải, tham số `load_increase`/`demand_drop` (khoảng của evaluation) và với `thermal_fault` là connector lỗi (trong các connector có tải ổn định ±8% suốt 900 giây sau onset), onset 120–300 s và mức 9,5–11 °C tại tải lúc onset (`chargeops/fleet.py`); các connector còn lại khỏe. Kế hoạch này chỉ simulator tính, không lưu vào run. Kế hoạch tất định theo seed; seed hiển thị cho viewer (`GET /demo-runs`), nhưng detector không bao giờ dùng seed hay kế hoạch. Ground truth lỗi và seed lưu trong dữ liệu simulator/evaluation, tách khỏi đầu vào detector. Dataset công khai chỉ làm mẫu đường tải sau kiểm định; nhãn lỗi pin không trở thành nhãn lỗi charger. `composition` mô tả toàn mẫu. Manifest được telemetry tham chiếu ánh xạ từng trường sang `public`, `synthetic` hoặc `derived`, kèm source/version/checksum cho public, model/version cho synthetic, công thức và input fields cho derived. Ví dụ tải public và nhiệt simulator có composition hybrid, trường nhiệt synthetic; residual là derived. Nhiệt tương tác không được gắn public chỉ vì tải lấy từ dataset. Giữ manifest khi telemetry hết TTL để đọc lại bằng chứng incident.

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
| `POST /demo-runs/{id}/inject-fault` | `{connector_id}` (một trong 6 connector của run); ghi event điều khiển simulator | Operator |
| `GET /evaluations`, `GET /evaluations/{id}` | Bản tóm tắt offline đã commit (`data/evaluation/<id>/summary.json`): protocol, fingerprint, cấu hình khóa, arm A/B/C, detector, mục tiêu; `simulation_only: true`, không phải kết quả chạy thật | Viewer |
| `POST /telemetry/batch` | `{samples: [...]}`, tối đa 1.000 mẫu; kiểm tra toàn bộ trước ghi | Simulator secret |
| `GET /simulator/commands` | Command chưa xác nhận của run hoạt động, kèm `connector_id`; kết quả `applied` chỉ đổi trạng thái thiết bị `run.sim[connector_id]` | Simulator secret |
| `GET /simulator/events`, `POST /simulator/events/{id}/consume` | Event `inject_fault` chưa tiêu thụ; consume đặt `consumed_at` đúng một lần (lặp lại trả `duplicate`) và lỗi bắt đầu trên connector đó tại `consumed_at` | Simulator secret |
| `POST /simulator/commands/{id}/result` | `{status: applied|failed, applied_at, result}`; ghi event kết quả | Simulator secret |
| `POST /simulator/vehicles` | `{vehicles: [VehicleState]}` (1–6 phần tử: `run_id, connector_id, measured_at, soc_percent, energy_kwh, minutes_to_full, capacity_source: autosun|default`); upsert trạng thái xe mới nhất vào collection `vehicle_states` (`_id = "<run_id>:<connector_id>"`, index `run_id`). Lỗi gửi chỉ ghi log `vehicle_post_retry`, không chặn telemetry | Simulator secret |
| `GET /stations/{station_id}` | `station-1|station-2` (khác → 422). Màn hình trạm cho người dùng: mỗi connector `status: offline|idle|charging|limited|checking`, `soc_percent`, `power_kw`, `current_a`, `connector_temperature_c`, `energy_kwh`, `minutes_to_full`, `measured_at`. Dùng chung pipeline latest-state của `GET /connectors`; không trả residual, incident, command, ngưỡng hay `run_id` | Công khai local, chỉ đọc |
| `POST /webhooks/datadog` | Payload chuẩn hóa ở mục 4 | Webhook secret |
| `GET /integrations/datadog/incidents` | `{items: [{incident_id, connector_id, station_id, state, allowed_commands, ui_url}]}`: chỉ sự cố chưa đóng của run đang hoạt động; `allowed_commands` tính bằng đúng cổng của UI/worker. Không có trong OpenAPI công khai | `X-Action-Secret` |
| `POST /integrations/datadog/actions` | `{incident_id, command: acknowledge|reduce_load|maintenance|verify_recovery}`, header `Idempotency-Key` (1–200 ký tự); trả 202 `{command_id, status}`. Command mang `actor: "datadog-app"` (route operator ghi `actor: "operator"`), vẫn `pending/validated=false` và đi qua cổng của worker | `X-Action-Secret` |

**Quyền hiện thực (21/10):** "Viewer" nghĩa là ẩn danh: mọi `GET` không cần đăng nhập vì triển khai chỉ bind localhost (Caddy `:8080`); chỉ tồn tại vai trò `operator` (`UserOut.role` chỉ nhận `"operator"`), UI coi người chưa đăng nhập là chỉ xem. Mutation cần session cookie kèm Origin trong `ALLOWED_ORIGINS`, hoặc header `X-Operator-Secret` (CLI). `POST /auth/login` và `/auth/logout` từ chối request có Origin lạ (403); request không có Origin (curl, CLI) được chấp nhận vì trình duyệt luôn gửi Origin với POST. Không công khai cổng `:8080` ra ngoài localhost khi chưa có vai trò viewer thật. Endpoint trả document thô (incident, command, demo-run list, simulator) mã hóa thời gian dạng UTC `...Z`.

**Route cho Datadog App (B8):** chỉ chấp nhận `X-Action-Secret` (so sánh thời gian hằng số, `DATADOG_ACTION_SECRET` ≥ 24 ký tự); cookie operator và `X-Operator-Secret` không được chấp nhận. Mã lỗi: 401 sai/thiếu secret, 404 không có sự cố, 409 `command_not_allowed` (lệnh ngoài `allowed_commands`; cũng trả 409 nếu `Idempotency-Key` dùng lại cho yêu cầu khác), 422 thiếu `Idempotency-Key` hoặc body sai, 503 `actions_disabled` khi secret để trống. Gửi lại cùng `Idempotency-Key` trả đúng command cũ. Không có demo-run, inject-fault hay đăng nhập qua các route này.

Request tạo command trả 202 và command ID; không trả thông báo đã giảm tải. API ghi yêu cầu/event bền vững; **worker duy nhất ghi chuyển trạng thái incident**. Start/stop cũng là yêu cầu bất đồng bộ để tránh API/simulator tranh quyền điều khiển. Frontend poll mỗi 5 giây; OpenAPI backend là nguồn sinh TypeScript types. Worker nhận event/command/inbox mới qua Change Streams trên collection thường, có resume token lưu bền vững để restart không bỏ sót; telemetry time series vẫn đọc bằng aggregation. Simulator chỉ poll/apply command liên quan tới run của nó, sau interface `ChargerCommandSink` (xem [ARCHITECTURE.md](ARCHITECTURE.md)) để thay bằng OCPP `SetChargingProfile` sau này mà không đổi state machine.

## 3. Detector và trạng thái

Telemetry mỗi 5 giây; worker tạo các block residual 15 giây không chồng lặp theo thời gian đo, với tổng residual và số mẫu khác nhau. Local và Datadog chỉ áp dụng **một cửa sổ 60 giây**, lấy tổng residual chia tổng số mẫu của bốn block; không xuất rolling mean 60 giây rồi lại làm mượt thêm 60 giây trong Datadog. Cửa sổ cần ít nhất 10 mẫu khác nhau, không có khoảng trống trên 15 giây và mẫu cuối không cũ quá 30 giây. Nếu thiếu block hoặc không đạt chất lượng, trạng thái `insufficient_data` chặn cả alert lẫn recovery và hủy bộ đếm ổn định.

Baseline nhiệt có xét tải, nhiệt môi trường và quán tính nhiệt. Hiệu chỉnh trên các lượt khỏe riêng, ghi hệ số, seed và phiên bản cấu hình. Tránh so sánh nhiệt động với baseline tĩnh gây cảnh báo khi tải tăng bình thường. Alert khi residual trung bình cửa sổ 60 giây hợp lệ >8°C; không yêu cầu mọi mẫu liên tục >8°C. Recovery signal khi trung bình này <3°C (hằng số `ALERT_RESIDUAL_C`, `RECOVERY_RESIDUAL_C` trong `domain.py`). Hiệu chỉnh bằng tập tuning, khóa trước tập đánh giá. Đây là ngưỡng mô phỏng, không phải chuẩn an toàn trụ sạc.

| Từ → đến | Điều kiện worker áp dụng |
|---|---|
| Chưa có → `open` | Alert hợp lệ, run còn hoạt động; snapshot bằng chứng được lưu |
| `open` → `acknowledged` | Operator acknowledge |
| `acknowledged` → `mitigating` | Worker nhận yêu cầu giảm tải hợp lệ; phát command cho simulator |
| `mitigating` → `mitigated` | Command đã áp dụng; trong cùng cửa sổ 60 giây sau ACK đạt quality gate, mọi mẫu đang sạc (`current_a` > 0, không `faulted`), dòng trung bình <=110% `target_current_a`, giới hạn đo được <= mục tiêu và residual phục hồi liên tục 60 giây |
| `mitigating` (bảo trì sớm) | Chỉ khi chu kỳ hiện tại đã ghi `load_reduction_ineffective` với reason `residual_not_recovered`: dòng đã được xác nhận giảm, và đã qua 120 giây kể từ thời điểm xác nhận (không phải từ ACK) mà incident vẫn chưa chuyển `mitigated`. Đây là điều kiện theo thời gian, không phải một phép đo residual mới: worker không đo lại cửa sổ riêng, chỉ ghi nhận rằng điều kiện phục hồi (residual <3°C liên tục 60 giây) chưa đạt trong khoảng đó. Event này ghi tối đa một lần mỗi chu kỳ; khi đó operator được gửi `maintenance` dù còn `mitigating`. `load_reduction_ineffective` reason `load_not_reduced` và `load_reduction_unverified` không bao giờ mở khóa, nhưng không chặn lần kiểm tra 120 giây sau khi xác nhận. Sau bảo trì vẫn phải qua `mitigated` → `verifying` → retest 120 giây |
| `mitigated` → `verifying` | Bảo trì đã áp dụng; operator yêu cầu kiểm tra; simulator xác nhận khôi phục giới hạn tải gốc |
| `verifying` → `resolved` | Trong cùng trọn cửa sổ kiểm tra 120 giây, residual từng mẫu <3°C và cả requested/actual current từng mẫu >=90% `reference_current_a`; mọi cửa sổ con 60 giây đạt quality gate |

`reference_current_a` được đóng băng bằng trung bình dòng thực tế của cửa sổ 60 giây hợp lệ mới nhất ngay trước lần giảm tải đầu tiên, lưu cùng evidence. Cửa sổ thiếu chất lượng hoặc trung bình bằng 0 thì chưa đủ điều kiện giảm tải/kiểm tra phục hồi; yêu cầu thu lại baseline tải hợp lệ, không tự dùng giới hạn dòng thay thế. Trong retest, bất kỳ mẫu tải thấp, residual >=3°C hoặc thiếu/stale dữ liệu đều reset đồng hồ 120 giây. Nếu `verifying` có alert nóng lại hợp lệ, trở về `open` và ghi event tái phát. Stop run ghi lý do kết thúc demo, giữ trạng thái incident chưa giải quyết; không giả là đã sửa thiết bị.

Command giảm tải đặt giới hạn tuyệt đối `target_current_a = min(0.5 × original_limit_a, 0.5 × reference_current_a)`. Lưu giới hạn gốc, dòng tham chiếu và mục tiêu bất biến trước can thiệp; không tính lại hoặc nhân 50% tiếp khi retry. Ví dụ giới hạn gốc 200 A nhưng xe dùng 60 A thì đặt 30 A; hạ xuống 100 A không bảo đảm tải thực giảm. Đây là chính sách mô phỏng, không phải hướng dẫn điều khiển thiết bị thật.

Cùng idempotency key và payload trả lại command cũ; cùng key khác payload trả 409. Command chưa xác nhận sau 30 giây chuyển timeout, incident vẫn chưa giải quyết. Kết quả đến sau timeout được lưu như event đến muộn, worker đối soát với tải thực trước khi cho thao tác tiếp. Simulator lưu applied command ID để restart không áp dụng hai lần. Bảo trì chỉ loại nguyên nhân lỗi mô phỏng; verify mới khôi phục tải.

`applied` chỉ là ACK của simulator, không chứng minh tác dụng. Worker kiểm chứng bằng telemetry sau ACK: cửa sổ 60 giây đạt quality gate, dòng thực tế trung bình <=110% mục tiêu và mọi mẫu giới hạn dòng <= mục tiêu. Dung sai 10% là cấu hình demo được khóa trước evaluation. Lưu evidence trước/sau, thời điểm ACK và thời điểm đạt điều kiện; API incident trả các bằng chứng này để UI phân biệt “đã áp dụng lệnh” với “đã giảm dòng”. Cửa sổ có mẫu không sạc (trip, `current_a` = 0 hoặc `faulted`) là gián đoạn, không bao giờ là bằng chứng giảm tải có tác dụng. Nếu sau 120 giây từ ACK chưa có cửa sổ chứng minh giảm dòng, ghi một lần event `load_reduction_ineffective` (reason `load_not_reduced`) khi dữ liệu đủ hoặc `load_reduction_unverified` (reason `insufficient_data`) khi dữ liệu thiếu; giữ `mitigating`, hiển thị lý do, không tự giảm tiếp. Khi giảm dòng đã được xác nhận (kể cả muộn, sau một event trên) mà đã qua 120 giây từ thời điểm xác nhận nhưng incident chưa đạt `mitigated` (điều kiện theo thời gian, không đo lại residual), ghi một lần mỗi chu kỳ `load_reduction_ineffective` reason `residual_not_recovered`; reason được lưu trên incident (`reduction_check_reason`). Chỉ reason `residual_not_recovered` mở khóa `maintenance` từ `mitigating`; cổng `verify_recovery` và retest không đổi. Bảo trì được ghi nhận theo chu kỳ xử lý lúc phát lệnh, nên kết quả đến sát lúc chuyển sang `mitigated` vẫn được tính. Khi có bằng chứng hợp lệ đến sau, ghi thời điểm thực và vẫn yêu cầu đủ điều kiện nhiệt để chuyển `mitigated`. Dòng giảm do xe giảm nhu cầu không tự chứng minh hiệu quả nhân quả của lệnh; tác động được đánh giá bằng các lượt đối chứng cùng nhu cầu tải.

## 4. Local và Datadog

Gate tích hợp đầu tiên được ưu tiên ngay khi nền tảng sẵn sàng trong khung build chính thức, trước khi mở rộng: một connector tổng hợp đi qua ingest → MongoDB → aggregation → dashboard/monitor Datadog thật → webhook → đúng một incident có source Datadog. Lưu log tương quan và timestamps để đo độ trễ; kiểm chứng phép tính cửa sổ bằng chuỗi mẫu có kết quả mong đợi, cả khi thiếu dữ liệu. Xác minh mapping payload thực sang schema dưới đây, không giả định template đã đúng. Gate này chưa thay cho nghiệm thu toàn bộ; không chờ UI đầy đủ hoặc dataset công khai mới kiểm tra Datadog.

Mode là cấu hình bất biến của run: `local` hoặc `datadog`. Local tạo alert event từ detector. Datadog xuất tổng residual/số mẫu của block 15 giây không chồng lặp, freshness và quality; monitor tính duy nhất mean 60 giây theo mục 3. Quality gate là điều kiện đồng thời của monitor và được worker kiểm tra lại trước xử lý webhook; thiếu block hoặc dữ liệu không đạt chặn alert/recovery, không nội suy lỗ trống. Datadog recovery không đóng incident.

Webhook có `event_id`, `monitor_id`, `run_id`, `connector_id`, `occurred_at`, `status: alert|recovery`, `message`. Dùng tag `run_id` duy nhất cho metric live Datadog để template giữ đúng tương quan. Đây là ngoại lệ cardinality dành riêng demo: tối đa **20 run ID duy nhất trên mỗi trial đã cấu hình**, bộ đếm bền vững qua restart; đạt trần thì chặn start Datadog run mới. Không tái sử dụng ID và không export offline evaluation. Kết thúc run trước khi bắt đầu run kế tiếp.

API xác thực secret, ghi inbox bền vững trước khi trả 202; unique event ID chống lặp. Payload thiếu event/run/monitor/connector identity hoặc timestamp hợp lệ được lưu quarantine với lý do, không tự tạo event identity hoặc gán run đang hoạt động. Worker bỏ qua event của run đã kết thúc, connector không thuộc run hoặc event cũ hơn trạng thái monitor đã xử lý. Unique partial index bảo đảm một incident chưa resolved cho mỗi `(run_id, connector_id, thermal_anomaly)`.

Datadog không khả dụng không kích hoạt fallback local. UI hiển thị mode và lỗi quan sát; dữ liệu/commands local vẫn được lưu. Cổng tunnel chỉ phục vụ endpoint webhook, không công khai API điều khiển. Secret tách biệt cho webhook/simulator, cấu hình qua môi trường; session mutation dùng kiểm tra Origin/CSRF, cookie SameSite. Không đặt secrets vào log hoặc frontend.
