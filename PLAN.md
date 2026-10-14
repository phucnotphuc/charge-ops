# ChargeOps — Plan triển khai local, dependencies và GitHub private

## Quyết định và trạng thái

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

## Dữ liệu và mô phỏng

Hai trạm, sáu connector; mỗi 5 giây phát mẫu. Khảo sát Autosun–Hong Kong Polytechnic v3, kiểm tra tải file, schema, đơn vị, thiếu dữ liệu và timestamp. Dataset dùng làm mẫu tải, không dùng nhãn lỗi pin làm lỗi charger. Nếu không đạt kiểm định sau một ngày làm việc, dùng tổng hợp hoàn toàn và khai báo rõ.

Simulator có quán tính nhiệt, đáp ứng với tải, fault injection có seed. Provenance phân biệt public/synthetic/derived. Ground truth tách biệt, detector không đọc. Datadog demo theo thời gian thực; đánh giá nhanh dùng đồng hồ riêng. Tình huống khỏe bao gồm tăng tải và giảm nhu cầu xe. Telemetry mất là ca tin cậy, không phải mở rộng bài toán chính.

Provenance public/synthetic/derived áp dụng theo trường trong manifest. Composition của cả mẫu/run là synthetic/public_replay/hybrid và tham chiếu manifest, không phải enum thay thế provenance từng trường.

## MongoDB và API

Telemetry time series TTL 14 ngày; timestamps UTC observed/received, ID mẫu, connector/session, V/A/kW, nhiệt connector/môi trường, tải yêu cầu/giới hạn và provenance. Connector, session, baseline, incident/event/command, run/source/evaluation dùng collection thường. Bằng chứng incident lưu riêng ngoài TTL telemetry. Aggregation 15 giây: residual, xu hướng, freshness; khử mẫu trùng bằng ID. Không Change Streams trên time series.

API /api/v1: đọc connector/telemetry/incident/evaluation; ingest; Datadog webhook; duyệt giảm tải, bảo trì, retest; demo start/stop; health/readiness. OpenAPI là hợp đồng frontend với kiểu TypeScript sinh tự động.

## Phát hiện và state machine

Baseline nhiệt xét tải và ambient, hiệu chỉnh bằng lượt khỏe. Ngưỡng demo khởi điểm residual >8°C trong một phút, phục hồi <3°C; khóa sau tuning trước đánh giá. Không mô tả là ngưỡng an toàn thực địa.

Chốt nghĩa cửa sổ: residual trung bình 60 giây vượt 8°C, không yêu cầu mọi mẫu đều vượt ngưỡng. Xuất các aggregate 15 giây không chồng lắp; local/Datadog chỉ áp dụng một cửa sổ 60 giây, không làm mượt hai lần. Dữ liệu thiếu không đủ điều kiện cảnh báo nhiệt hoặc xác nhận phục hồi.

Worker duy nhất chuyển incident state. Local detector tạo event trực tiếp. Datadog mode worker xuất metric, monitor gửi webhook, API ghi event bền vững, worker áp dụng. Nguồn cảnh báo hiển thị rõ.

`open → acknowledged → mitigating → mitigated → verifying → resolved`.

Operator giảm tải về 50% giới hạn trước can thiệp. Command ID/idempotency bảo đảm gửi lại không giảm thêm. ACK timeout 30 giây. Nhiệt ổn định 60 giây mới mitigated; bảo trì rồi retest khôi phục tải ổn định 120 giây mới resolved. Dữ liệu cũ >30 giây không được xác nhận phục hồi. Datadog OK không đóng incident. Webhook lặp/đến muộn không tạo trùng hoặc tác động run đã kết thúc. Dừng run không được giả vờ resolved.

## UI và bảo mật

Năm màn hình tiếng Việt: tổng quan, connector, incident, điều khiển demo, đánh giá. Hiển thị unit/freshness/provenance/timeline; loading/error/empty state. Viewer chỉ đọc; operator login cấu hình local, password hash, HttpOnly session cookie. Secret riêng simulator/webhook; không log/commit secret, .env, dữ liệu thô lớn.

## Repo và sub-agent

Tạo repo độc lập; giữ nguyên repository cha. Xác minh repo remote đích và private trước push. Chỉ đẩy file thuộc dự án, không secrets/build/venv/raw data. Không bật dịch vụ trả phí. Cấp quyền giám khảo sau khi có tài khoản chính thức.

Agent chính quản lý nền tảng/hợp đồng/Compose/integration. A: backend/ingest/state/commands/auth/tests. B: dataset/simulator/baseline/evaluation. C: frontend năm màn hình/tests. Khi có slot: observability và reviewer độc lập. Không sửa cùng file, không đổi API chung một phía; tất cả thay đổi qua điều phối.

## Lịch

- 14–16/10: plan, tài khoản, môi trường, repo tài liệu; chưa core.
- 17–19/10: simulator/API/MongoDB, kiểm định data và aggregation/tests.
- 20–22/10: detector/Datadog/webhook/incident xuyên suốt.
- 23–25/10: UI, commands, maintenance/retest; demo đầu tiên.
- 26–28/10: kiểm thử tin cậy, evaluation, khóa tính năng.
- 29–30/10: diễn tập ba lần, slide/video/README.
- 31/10: kiểm tra máy sạch, chốt commit/nộp trước 23:59 ICT.

## Nghiệm thu

Ca kiểm thử: healthy/tăng tải/taper; fault tới recovery; webhook trùng/muộn; dữ liệu mất/trùng/muộn; timeout/duyệt lặp/giảm tải thất bại; restart giữ state; viewer không điều khiển; offline hoàn chỉnh; Datadog alert/webhook thật. Ba lượt demo liên tiếp thành công cùng bản build.

20 seed lỗi chưa tuning +20 healthy tương ứng. Mục tiêu phát hiện trước trip ít nhất 18/20 fault, tối đa một incident giả/20 healthy, không command trùng. Báo cáo recall incident, false alerts/connector-hour, latency local/Datadog, lead time đối chứng cùng seed, thời gian giảm tác động/phục hồi, tác động công suất và phiên. Không suy lead time nếu đối chứng không trip. Báo kết quả thực, không sửa số đo để đạt mục tiêu.

## Bàn giao

Repo private, plan, lockfiles, Compose, README từ máy sạch, Datadog/tunnel runbook, sơ đồ kiến trúc/data model/data card/disclosure, benchmark tái lập seed, script demo, slide khoảng 10 trang và video 6–8 phút. Local hoàn chỉnh là mục tiêu; EC2 ngoài phạm vi. Trial và tunnel có giới hạn, không gọi miễn phí vĩnh viễn.
