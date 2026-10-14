# ChargeOps — Bàn giao triển khai từ 17/10/2026

## Ranh giới chuẩn bị

Theo lựa chọn của nhóm ngày 14/10: trước 17/10 chỉ tạo tài liệu, kiểm tra môi trường/tài khoản và chuẩn bị private repo. Chưa viết source ứng dụng, simulator, tests sản phẩm, Dockerfile hoặc Compose thực thi. Không tự đặt lịch chạy code. Khi nhóm tiếp tục từ 17/10, đọc PLAN.md và CONTRACT.md trước khi nhận phần việc.

Đích dự án: `D:\devops\charge-ops`; remote `phucnotphuc/charge-ops` đã được tạo private trong lượt chuẩn bị, tài liệu chưa được push tại thời điểm cập nhật này. Xác minh trạng thái local/remote mới nhất và visibility trước mutation/push, không tạo lại hoặc ghi đè repo; giữ nguyên repo cha. Chỉ dùng máy local, MongoDB container, Datadog trial và tunnel miễn phí; không tạo EC2/Atlas/domain trả phí.

## Trình tự và quyền sở hữu

Agent chính chốt hợp đồng, tạo nền tảng sau mốc build, tích hợp và nghiệm thu. Ba sub-agent tối đa hoạt động cùng lúc; mỗi lượt có file ownership rõ ràng. Thay đổi API/domain phải quay về agent chính, không tự sửa hợp đồng chia sẻ.

| Lượt | Agent A | Agent B | Agent C | Gate trước lượt sau |
|---|---|---|---|---|
| 0 — chính | Agent chính tạo monorepo, shared models/OpenAPI, cấu hình Compose local, dependencies và lockfiles | — | — | Health/readiness chạy; contract kiểm tra được |
| 1 — song song | Backend: ingest, MongoDB indexes, inbox, auth, incident worker, command tests | Dữ liệu: kiểm định nguồn, simulator động, baseline và evaluation độc lập | Frontend: năm màn hình theo OpenAPI, trạng thái thiếu dữ liệu/lỗi, operator workflow | Các phần build/test riêng; không tranh file |
| 2 — song song | Datadog: metrics/logs/APM, monitor/dashboard template, webhook/tunnel | E2E: run thật, lỗi mạng/restart/idempotency và evaluation | Tài liệu/demo: data card, runbook, slide/storyboard dựa trên kết quả đã có | Local end-to-end đạt; observability kiểm chứng thật |
| 3 — kiểm tra | Reviewer độc lập đọc diff và điều kiện nghiệm thu | Agent chính sửa và tích hợp | — | Review issues đã xử lý, ba demo liên tiếp |

Agent B không chỉnh API/models shared; đề xuất schema cần thêm cho agent chính. Agent C sinh types từ OpenAPI, không tạo định nghĩa domain song song. Test fixture chỉ dùng trong test, UI runtime không thay API lỗi bằng dữ liệu giả.

## Dependencies cần xác minh khi bắt đầu

- Git và GitHub authentication có quyền tạo private repo; xác minh tài khoản thay vì dựa vào ghi chú cũ.
- Docker Desktop Linux containers/Compose hoạt động; MongoDB chỉ ở mạng nội bộ Compose.
- Node.js 22, npm; React, TypeScript, Vite, React Router, Recharts, Lucide React.
- Python 3.12 trong container; uv, FastAPI, Uvicorn, Pydantic Settings, PyMongo, HTTPX; NumPy/pandas/openpyxl cho dữ liệu.
- Datadog Agent, datadog, ddtrace; Caddy và cloudflared cho observability.
- pytest/pytest-cov/Ruff, Vitest/Testing Library, Playwright; khóa package versions sau kiểm tra tương thích, image tag cụ thể, không `latest`.
- Không đổi Python hệ thống; không cần Redis/Kafka/Celery/Kubernetes. Docker images và packages cần tải lần đầu; offline chỉ sau khi đã có chúng.

## Gates và nghiệm thu

1. **Môi trường sạch:** clone private repo, tạo cấu hình local theo README, chạy Compose. Frontend truy cập localhost:8080; MongoDB không mở cổng host. `.env` và credentials không nằm trong Git.
2. **Dữ liệu:** nguồn tải được và schema/đơn vị/timestamp được kiểm định; nếu không đạt sau một ngày làm việc thì dùng tổng hợp, ghi rõ provenance. Lưu manifest/checksum nếu dùng file công khai; không cam kết dataset chưa tải.
3. **Detector:** seed khỏe/tăng tải/giảm nhu cầu không cảnh báo sai; thermal fault cảnh báo trước trip đối chứng. Local/Datadog dùng block 15 giây và một cửa sổ 60 giây, có test không làm mượt hai lần, mean >8°C và quality gate. Detector không truy cập ground truth. Telemetry trùng/đến muộn/mất không tạo alert hoặc phục hồi giả.
4. **Vòng xử lý:** acknowledge → giảm tải tuyệt đối → xác nhận tác dụng → bảo trì → kiểm tra tải tương đương → resolve. Đóng băng reference current từ 60 giây pre-derating hợp lệ; zero/invalid không đủ điều kiện. Kiểm thử cùng 120 giây retest đạt nhiệt/tải, và reset timer khi tải thấp/nóng/stale. Command lặp không đổi tải lần hai; timeout và giảm tải không hiệu quả giữ incident chưa giải quyết.
5. **Độ bền và quyền:** restart API/worker/simulator không mất state hoặc thực thi command hai lần; viewer không thao tác; webhook gửi lại/đến muộn không tạo incident sai run; trace/log không lộ secrets.
6. **Datadog thật:** metric tới dashboard, monitor alert, webhook tới local, đúng một incident có source Datadog. Thiếu identity vào quarantine, không gán run hiện tại. Test trần 20 unique run ID/trial bền vững, không tái sử dụng; offline evaluation không export. Recovery không đóng incident. Ngắt Internet hiển thị lỗi rõ; không fallback âm thầm. Lưu bằng chứng đã che secrets, không mô tả template chưa chạy là đã tích hợp.
7. **Đánh giá:** 20 seed lỗi mới và 20 seed khỏe, tách tuning. Đạt mục tiêu ít nhất 18/20 phát hiện trước trip và tối đa một incident giả trên 20 lượt khỏe; báo thêm tổng connector-hour. So sánh can thiệp với không can thiệp cùng seed, ghi năng lượng/công suất bị giảm. Nếu đối chứng không trip, lead time là không xác định. Báo số đo thực, không sửa ground truth để đạt mục tiêu.
8. **Demo:** ba lượt trọn luồng trên cùng build; video 6–8 phút và slide khoảng 10 trang có nguồn, giới hạn mô phỏng, kết quả thực. Video là bằng chứng dự phòng, không thay điều kiện Datadog thật.

## Lịch dự kiến và bàn giao

- 14–16/10: tài liệu, khảo sát môi trường/tài khoản, private repo chuẩn bị.
- 17–19/10: nền tảng, ingest, simulator và kiểm định dữ liệu.
- 20–22/10: worker/incident và Datadog xuyên suốt.
- 23–25/10: giao diện, hành động vận hành và demo đầu tiên.
- 26–28/10: kiểm thử, đánh giá, review, khóa tính năng.
- 29–30/10: diễn tập, video, slide, README hoàn thiện.
- 31/10: kiểm tra clone sạch và hồ sơ, nộp theo hạn đã xác minh với ban tổ chức.

Bàn giao cuối: private repo, lockfiles, Compose, README, hợp đồng, sơ đồ, data card, cấu hình Datadog, runbook, báo cáo evaluation, slide và video. Cấp quyền giám khảo chỉ khi có tài khoản/địa chỉ chính thức. Ghi riêng các phần chưa kiểm chứng hoặc chờ người dùng như đăng nhập GitHub, Docker Engine và Datadog trial; không gọi chúng là hoàn thành.
