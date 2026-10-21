# ChargeOps — Plan triển khai local, dependencies và GitHub private

## Quyết định và trạng thái

**Cập nhật quyết định 14/10/2026:** người dùng yêu cầu xây luồng tối thiểu ngay, thay quyết định chờ 17/10 cho phần này. Các mốc dưới đây vẫn là kế hoạch gốc; xem MINIMAL_LOOP.md trong docs và DISCLOSURE.md về code viết sớm, phạm vi đã triển khai và bằng chứng còn thiếu. Chưa nghiệm thu Datadog thật.

Ngày chốt 14/10/2026, giờ Asia/Saigon. Nhóm yêu cầu **chuẩn bị trước, phần cốt lõi từ 17/10**. Tài liệu này ghi yêu cầu đã duyệt; không phải báo cáo hoàn thành. Không tự chạy công việc vào 17/10 nếu chưa có phiên thực thi mới hoặc automation được yêu cầu.

- Dự án: `D:\devops\charge-ops`, Git repo độc lập với thư mục cha.
- GitHub: `phucnotphuc/charge-ops`, private.
- Ngân sách dịch vụ 0 đồng; máy local sẵn có, MongoDB local, Datadog trial, HTTPS tunnel miễn phí. Không EC2/Atlas/domain/AI trả phí.
- Sub-agent triển khai từng phần và reviewer độc lập; chốt hợp đồng trước khi song song.
- Luồng chính: telemetry → phát hiện → incident có bằng chứng → operator duyệt giảm tải → bảo trì mô phỏng → kiểm tra phục hồi.
- Không thanh toán, ứng dụng tài xế, OCPP/charger thật hoặc dự báo tuổi thọ thiết bị.

## Dependencies

Git; Docker Desktop Linux containers và Compose. Node.js 22, npm; React/React DOM, TypeScript, Vite, React Router, Recharts, Lucide React. Python 3.12 trong container; FastAPI, Uvicorn, Pydantic Settings, PyMongo, HTTPX. NumPy, pandas, openpyxl phục vụ dữ liệu. Datadog Agent, datadog, ddtrace. cloudflared, Caddy. pytest, pytest-cov, Ruff, Vitest, Testing Library, Playwright. Dùng npm lockfile và uv lockfile; khóa package/image sau build/test tương thích đầu tiên, không image latest. Không đổi Python hệ thống. Không thêm Redis/Kafka/Celery/Kubernetes.

## Kiến trúc

Monorepo frontend và package Python dùng chung API/worker/simulator, infrastructure và docs. Compose local chạy MongoDB, API, worker, simulator, web. Profile observability bổ sung Agent và tunnel. API, simulator và worker là tiến trình riêng. Giao diện localhost:8080, polling 5 giây. MongoDB chỉ trong Docker network. Caddy listener riêng chỉ công khai đường webhook qua tunnel. Script diễn tập hiển thị URL tạm và cách cập nhật Datadog.

Local mode hoàn chỉnh không cần cloud sau khi tải dependency/image. Datadog mode phải có telemetry/metrics thật, alert thật, webhook thật. Không thay ngầm Datadog bằng detector local khi cloud lỗi.

Lõi miền tách khỏi adapter theo [ARCHITECTURE.md](docs/ARCHITECTURE.md): `TelemetrySource`, `ChargerCommandSink`, `Detector`, `AlertSource`, `MetricsSink`, `Identity`. Bản thi chỉ có một implementation mỗi seam (simulator, residual, DogStatsD/ddtrace, user local); phần riêng cuộc thi (trần run_id, tunnel, demo-runs, evaluation) gom vào module demo. Mục tiêu: code dùng tiếp sau cuộc thi với OCPP/vendor observability khác mà không sửa lõi.

## Dữ liệu và mô phỏng

Hai trạm, sáu connector; mỗi 5 giây phát mẫu. Khảo sát Autosun–Hong Kong Polytechnic v3, kiểm tra tải file, schema, đơn vị, thiếu dữ liệu và timestamp. Dataset dùng làm mẫu tải, không dùng nhãn lỗi pin làm lỗi charger. Điều tra vấn đề chất lượng đến khi đủ bằng chứng quyết định sử dụng; nếu không tải được hoặc không đạt kiểm định, dùng tổng hợp hoàn toàn và khai báo rõ lý do.

Simulator có quán tính nhiệt, đáp ứng với tải, fault injection có seed. Provenance phân biệt public/synthetic/derived. Ground truth tách biệt, detector không đọc. Datadog demo theo thời gian thực; đánh giá nhanh dùng đồng hồ riêng. Tình huống khỏe bao gồm tăng tải và giảm nhu cầu xe. Telemetry mất là ca tin cậy, không phải mở rộng bài toán chính.

Provenance public/synthetic/derived áp dụng theo trường trong manifest. Composition của cả mẫu/run là synthetic/public_replay/hybrid và tham chiếu manifest, không phải enum thay thế provenance từng trường.

## MongoDB và API

Telemetry time series TTL 14 ngày; timestamps UTC observed/received, ID mẫu, connector/session, V/A/kW, nhiệt connector/môi trường, tải yêu cầu/giới hạn và provenance. Connector, session, baseline, incident/event/command, run/source/evaluation dùng collection thường. Bằng chứng incident lưu riêng ngoài TTL telemetry. Aggregation 15 giây: residual, xu hướng (`$setWindowFields`), freshness; khử mẫu trùng bằng ID. Change Streams không hỗ trợ time series, nên chỉ dùng trên collection thường (inbox/commands/events) để worker nhận việc theo sự kiện; MongoDB chạy replica set một node trong Compose. Mục tiêu: polling 5 giây chỉ còn ở frontend.

API /api/v1: đọc connector/telemetry/incident/evaluation; ingest; Datadog webhook; duyệt giảm tải, bảo trì, retest; demo start/stop; health/readiness. OpenAPI là hợp đồng frontend với kiểu TypeScript sinh tự động.

## Phát hiện và state machine

Baseline nhiệt xét tải và ambient, hiệu chỉnh bằng lượt khỏe. Ngưỡng demo khởi điểm residual >8°C trong một phút, phục hồi <3°C; khóa sau tuning trước đánh giá. Không mô tả là ngưỡng an toàn thực địa.

Chốt nghĩa cửa sổ: residual trung bình 60 giây vượt 8°C, không yêu cầu mọi mẫu đều vượt ngưỡng. Xuất các aggregate 15 giây không chồng lắp; local/Datadog chỉ áp dụng một cửa sổ 60 giây, không làm mượt hai lần. Dữ liệu thiếu không đủ điều kiện cảnh báo nhiệt hoặc xác nhận phục hồi.

Worker duy nhất chuyển incident state. Local detector tạo event trực tiếp. Datadog mode worker xuất metric, monitor gửi webhook, API ghi event bền vững, worker áp dụng. Nguồn cảnh báo hiển thị rõ.

`open → acknowledged → mitigating → mitigated → verifying → resolved`.

Operator giảm tải bằng giới hạn tuyệt đối `target_current_a = min(0.5 × original_limit_a, 0.5 × reference_current_a)`, với dòng tham chiếu đo trước can thiệp. Ví dụ giới hạn gốc 200 A, dòng thực tế 60 A thì mục tiêu là 30 A, không phải 100 A. Lưu mục tiêu một lần; command ID/idempotency bảo đảm gửi lại không giảm thêm. ACK timeout 30 giây; ACK chỉ xác nhận áp dụng lệnh. Chỉ chuyển mitigated khi telemetry chứng minh dòng thực tế giảm và residual phục hồi trong cùng cửa sổ 60 giây hợp lệ; quy tắc kiểm chứng và thời hạn tại CONTRACT.md. Bảo trì rồi retest khôi phục tải ổn định 120 giây mới resolved. Dữ liệu cũ >30 giây không được xác nhận phục hồi. Datadog OK không đóng incident. Webhook lặp/đến muộn không tạo trùng hoặc tác động run đã kết thúc. Dừng run không được giả vờ resolved.

## UI và bảo mật

Năm màn hình tiếng Việt: tổng quan, connector, incident, điều khiển demo, đánh giá. Thứ tự ưu tiên: incident (timeline/evidence/lệnh) → điều khiển demo → tổng quan; connector và đánh giá chỉ làm sau khi G2 đạt, có thể gộp vào tổng quan nếu thiếu thời gian. Hiển thị unit/freshness/provenance/timeline; loading/error/empty state. Viewer chỉ đọc; operator login cấu hình local, password hash, HttpOnly session cookie. Secret riêng simulator/webhook; không log/commit secret, .env, dữ liệu thô lớn.

## Repo và sub-agent

Tạo repo độc lập; giữ nguyên repository cha. Xác minh repo remote đích và private trước push. Chỉ đẩy file thuộc dự án, không secrets/build/venv/raw data. Không bật dịch vụ trả phí. Cấp quyền giám khảo sau khi có tài khoản chính thức.

Datadog dùng ba tín hiệu liên kết để chấm phần điều tra: metrics (dashboard/monitor), Log Management (log JSON có `trace_id`) và APM traces từ `ddtrace` cho ingest → aggregation → webhook → worker → command. Alert message chứa link tới dashboard và log query lọc theo `run_id`/`connector_id`. APM là bắt buộc nội bộ, không phải "nếu hữu ích".

Agent chính quản lý nền tảng/hợp đồng/Compose/integration và ưu tiên tích hợp Datadog tối thiểu ngay khi nền tảng sẵn sàng trong khung build chính thức, không chờ hoàn thiện backend/UI. A: backend/ingest/state/commands/auth/tests. B: dataset/simulator/baseline/evaluation. C: frontend năm màn hình/tests. Sau gate Datadog đầu tiên mới mở rộng observability; reviewer độc lập ở lượt kiểm tra. Không sửa cùng file, không đổi API chung một phía; tất cả thay đổi qua điều phối.

## Cách triển khai theo ưu tiên và phụ thuộc

Không đặt ngày cố định cho từng giai đoạn. Hoàn thành công việc sớm nhất có thể; bắt đầu phần tiếp theo ngay khi đủ điều kiện, làm song song các việc độc lập có ownership rõ ràng. Theo dõi checklist và bằng chứng tại [tasks.md](tasks.md).

1. Chuẩn bị tài liệu, tài khoản và môi trường; phần cốt lõi chỉ trong khung build 17–31/10/2026.
2. Nền tảng → một connector tổng hợp → ingest/MongoDB → aggregation → Datadog dashboard/monitor thật → webhook → đúng một incident. Lưu log tương quan, đo độ trễ và kiểm chứng cửa sổ/payload trước khi mở rộng.
3. Mở rộng simulator, kiểm định dữ liệu, commands và maintenance/retest; hoàn thành vòng xử lý có chứng cứ.
4. Hoàn thiện UI, điều tra và observability; kiểm thử dữ liệu lỗi, restart và idempotency.
5. Chạy evaluation đối chứng, review và sửa lỗi; khóa tính năng khi đủ bằng chứng nghiệm thu.
6. Diễn tập ba lần, hoàn thiện slide/video/README và kiểm tra clone sạch ngay khi build đủ ổn định; chốt hồ sơ và nộp trước 31/10/2026, 23:59 ICT.

Việc phức tạp cần phân tích sâu: ghi câu hỏi/giả thuyết, thu bằng chứng, chia thành phần kiểm chứng được và xử lý rủi ro lớn trước. Khi gặp vấn đề bất ngờ, tái hiện hoặc thu log, xác định nguyên nhân, sửa rồi kiểm chứng lại luồng bị ảnh hưởng. Cập nhật phụ thuộc và ưu tiên theo phát hiện mới; trong lúc gỡ blocker vẫn tiến hành việc độc lập. Không bỏ nghiệm thu để đạt một mốc nội bộ.

## Nghiệm thu

Ca kiểm thử: healthy/tăng tải/taper; fault tới recovery; webhook trùng/muộn; dữ liệu mất/trùng/muộn; timeout/duyệt lặp/giảm tải thất bại; restart giữ state; viewer không điều khiển; offline hoàn chỉnh; Datadog alert/webhook thật. Ba lượt demo liên tiếp thành công cùng bản build.

20 seed lỗi chưa tuning +20 healthy tương ứng. Mục tiêu phát hiện trước trip ít nhất 18/20 fault, tối đa một incident giả/20 healthy, không command trùng. Báo cáo recall incident, false alerts/connector-hour, latency local/Datadog, lead time đối chứng cùng seed, thời gian giảm tác động/phục hồi, tác động công suất và phiên. Không suy lead time nếu đối chứng không trip. Báo kết quả thực, không sửa số đo để đạt mục tiêu.

Mỗi kịch bản đánh giá chạy ba phương án: A — cảnh báo ngưỡng nhiệt tuyệt đối cố định và cùng quy trình can thiệp; B — detector theo tải nhưng không can thiệp; C — detector theo tải cùng quy trình ChargeOps đầy đủ. A/C dùng cùng chính sách giảm tải, độ trễ operator và lịch bảo trì để so sánh detector; B/C dùng để đo tác động can thiệp. Khóa ngưỡng cả hai detector trên tập tuning trước đánh giá. Giữ cùng seed, nhu cầu tải, ambient, thời điểm/cường độ fault, điều kiện đầu và thời lượng; nhiệt sau can thiệp do simulator tính lại, không replay nhiệt cố định. Báo thời gian phát hiện, cảnh báo giả, thời gian hành động có tác dụng, số trip/phút gián đoạn, kWh phục vụ và kWh chênh lệch trong cùng thời lượng. Chi tiết phương pháp tại DATA_CARD.md; không mặc định ChargeOps tốt hơn đối chứng.

## Bàn giao

Repo private, plan, lockfiles, Compose, README từ máy sạch, Datadog/tunnel runbook, sơ đồ kiến trúc/data model/data card/disclosure, benchmark tái lập seed, script demo, slide khoảng 10 trang và video 6–8 phút. Local hoàn chỉnh là mục tiêu; EC2 ngoài phạm vi. Trial và tunnel có giới hạn, không gọi miễn phí vĩnh viễn.
