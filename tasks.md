# ChargeOps — Checklist thi MUGVN 2026

Cập nhật: 14/10/2026, giờ Việt Nam. Tổng hợp review độc lập của ba sub-agent: thể lệ/hồ sơ, kỹ thuật và dữ liệu/đánh giá; agent chính đối chiếu nguồn BTC do người dùng cung cấp.

**Đánh giá hiện tại:** hướng giải pháp phù hợp bài toán phát hiện → điều tra → xử lý. Chưa đủ bằng chứng để gọi là bài thi hợp lệ hoặc MVP đạt: repo mới có tài liệu, chưa có ứng dụng, tích hợp Datadog hay kết quả đo. Ưu tiên xác nhận đăng ký, sau đó chứng minh tích hợp thật trước khi mở rộng.

Quy ước: `[x]` đã kiểm chứng trong lượt review này; `[ ]` chưa hoàn thành/chưa kiểm chứng. Mỗi mục chỉ đánh dấu x khi có evidence: commit, báo cáo, run ID, ảnh/log đã che secret hoặc xác nhận BTC. A/B/C bên dưới là vai trò sub-agent triển khai, không phải thành viên đội thi. Trưởng đội chịu trách nhiệm tài khoản, quyết định và nộp bài.

## 1. Điều kiện dự thi — P0, xử lý ngay

Nguồn kiểm tra ngày 14/10: [trang chính](https://mini-hackathon-sep-2026.mugvn.com/) và [thể lệ, cập nhật 25/09](https://mini-hackathon-sep-2026.mugvn.com/terms). Kiểm tra lại thông báo BTC trước nộp.

- [x] Xác minh lịch: build 17–31/10/2026; hạn nộp 31/10/2026, 23:59 ICT. Giữ core development từ 17/10.
- [ ] **Trưởng đội:** xác minh đã đăng ký trước 12/10, 23:59 và nhận email xác nhận đội hợp lệ; lưu tham chiếu riêng, không commit thông tin cá nhân. Nếu thiếu, hỏi BTC về tư cách dự thi; không mặc định được nộp.
- [ ] **Trưởng đội:** xác nhận đội 1–3 người, đủ điều kiện theo mục 2 thể lệ, có email công việc và link nộp bài.
- [ ] **Trưởng đội:** chuẩn bị quyền đọc repo private cho tài khoản giám khảo chính thức trước hạn nộp; duy trì đến hết 22/11/2026.
- [ ] **Trưởng đội:** ghi quy tắc đóng băng repository sau hạn nộp; chốt commit cuối trước hạn và không chỉnh repo sau đó.
- [ ] **Chính:** cập nhật disclosure AI/tái sử dụng/nguồn dữ liệu thực dùng; rà dữ liệu và secrets theo mục 5–6 thể lệ.

## 2. Điều kiện kỹ thuật và điểm số

Theo [yêu cầu BTC và rubric](https://mini-hackathon-sep-2026.mugvn.com/): MongoDB phải là dữ liệu vận hành chính, có mô hình/truy cập hợp lý và Aggregation Pipeline. Datadog cần dashboard, monitor và ít nhất một năng lực bổ sung. Bảng dưới ánh xạ bằng chứng dự kiến của ChargeOps; chưa mục nào được nghiệm thu.

| Tiêu chí | Bằng chứng ChargeOps cần tạo | Gate |
|---|---|---|
| MongoDB bắt buộc | Telemetry, incident, command/inbox lưu thật; pipeline aggregation, indexes và truy vấn giải thích được | G1–G3 |
| Datadog bắt buộc | Dashboard, monitor, **Log Management** có log tương quan run/connector/incident; APM bổ sung nếu hữu ích | G1, G3 |
| Sáng tạo 30% | Giải thích vì sao residual theo tải/ambient và recovery có chứng cứ khác cảnh báo nhiệt cố định | G4 |
| Kỹ thuật 30% | Data model, pipeline, state machine, observability và kết quả reliability tests | G1–G4 |
| Tác động 30% | Demo trọn vòng; đối chứng trips, gián đoạn, kWh và chi phí giảm tải mô phỏng | G2, G4 |
| Trình bày 10% | MVP tái lập, tài liệu, video, slide và trả lời được câu hỏi kỹ thuật | G5 |

Homepage nêu trọng số cho chung kết; mục 8 thể lệ vẫn nói sẽ công bố trọng số. Không tự suy ngưỡng điểm sơ loại hoặc quy tắc hòa điểm; theo cập nhật BTC. Time series/TTL là lựa chọn phù hợp dự án; không thêm công nghệ ngoài phạm vi chỉ để có nhiều tính năng.

## 3. Chuẩn bị

Nguồn: [PLAN](PLAN.md), [Dependencies](docs/DEPENDENCIES.md), [Environment](docs/ENVIRONMENT.md), [Implementation](docs/IMPLEMENTATION.md).

- [x] Ba sub-agent đọc và review toàn bộ nhóm tài liệu về phạm vi, contract, triển khai, data/evaluation và disclosure.
- [x] Kiểm tra local Git: có `4ed3ae8`, `f642417`; `main` khớp ref local `origin/main` tại lượt đọc. Đây không phải xác minh remote trực tiếp.
- [ ] **Chính:** đối soát remote/private/push hiện tại; sửa ghi chú commit/push đang chờ đã cũ trong PROGRESS, ENVIRONMENT, IMPLEMENTATION. Không tạo lại repo.
- [ ] **Trưởng đội + chính:** xác minh trial Datadog, tính năng Log Management, ngày hết hạn/quota và khả năng truy cập lúc chấm; chỉ dùng trial, không bật trả phí.
- [ ] **Chính:** xác minh Docker Linux Engine, RAM/disk, Node/npm và phương án uv/Python 3.12 container; không đổi Python host.
- [ ] **Chính:** phân công file ownership A backend, B simulator/evaluation, C frontend; mọi đổi contract qua điều phối và reviewer độc lập.
- [ ] **Chính + B:** chốt protocol đánh giá trên giấy: phân bổ healthy/load increase/demand drop, thời lượng, fault severity/time, trip/restart, incident matching, mẫu số connector-hour và trường hợp B không trip.

Không viết core, Compose/Dockerfile, tests sản phẩm hoặc cài dependencies dự án trước 17/10. Không tự tạo automation bắt đầu build. Giữ ngân sách dịch vụ 0 đồng; không EC2/Atlas/domain/AI trả phí.

## 4. G1 — Nền tảng và Datadog thật

Owner: agent chính phối hợp A/B. Nguồn: [Contract §1–4](docs/CONTRACT.md), [Implementation lượt 0/0b](docs/IMPLEMENTATION.md).

- [ ] Tạo monorepo/shared models/OpenAPI; sinh TypeScript types, lockfiles npm/uv và pin images sau build tương thích.
- [ ] Compose chạy MongoDB, API, worker, simulator, web; health/readiness hoạt động; MongoDB chỉ ở mạng nội bộ.
- [ ] **Một connector tổng hợp → ingest → MongoDB Aggregation Pipeline → metric → dashboard/monitor Datadog thật → webhook → đúng một incident source Datadog.**
- [ ] Bật Log Management thật; nối log với run/connector/incident để điều tra, lưu timestamps và đo latency end-to-end. Không coi template/ảnh minh họa là evidence.
- [ ] Kiểm chứng phép tính weighted mean: bốn block 15 giây không chồng lắp, một cửa sổ 60 giây; >=10 mẫu khác nhau, gap <=15 giây, mẫu cuối <=30 giây; thiếu chất lượng chặn alert/recovery.
- [ ] Đối chiếu payload webhook thật với contract; inbox bền vững, xác thực, quarantine thiếu identity; chỉ listener webhook được tunnel công khai.
- [ ] Ghi ngân sách tối đa 20 unique live run/trial bền vững; dành lượt cho sửa tích hợp và ba lần diễn tập cuối; evaluation offline không export.

**Điều kiện qua gate:** lưu cấu hình monitor, log/ảnh đã che secret, run ID, incident ID và latency đo thật. Chưa đạt thì xử lý tích hợp trước khi mở rộng UI/dataset.

## 5. G2 — Vòng xử lý đầy đủ

Nguồn: [Contract §3](docs/CONTRACT.md), [Data card](docs/DATA_CARD.md).

- [ ] **B:** kiểm định dataset: file/schema/unit/timezone, missing/trùng/thứ tự thời gian, checksum, phiên được chọn, attribution/license và biến đổi. Điều tra vấn đề chất lượng đến khi đủ bằng chứng quyết định dùng nguồn công khai hay hoàn toàn synthetic; ghi rõ lý do.
- [ ] **B:** simulator hai trạm/sáu connector, bước 5 giây, quán tính nhiệt và đáp ứng tải, fault có seed; detector không đọc ground truth. Gắn provenance theo trường; manifest giữ cùng evidence ngoài TTL.
- [ ] **A:** triển khai worker duy nhất chuyển state, command/inbox bền vững, snapshot evidence; dữ liệu trùng/muộn không làm sai latest state.
- [ ] **A/B:** demo acknowledge → giảm tải → chứng minh tác dụng → bảo trì → retest → resolved.
- [ ] **A/B:** đóng băng reference current từ 60 giây hợp lệ; mục tiêu `min(0.5 × original_limit_a, 0.5 × reference_current_a)`; test 200 A/60 A phải đặt 30 A; retry không giảm thêm.
- [ ] **A/B:** ACK không đủ để mitigated: cùng cửa sổ hợp lệ sau ACK phải có dòng <=110% mục tiêu, giới hạn <=mục tiêu và phục hồi nhiệt theo contract. Sau 120 giây không đủ bằng chứng ghi ineffective/unverified, giữ incident chưa giải quyết.
- [ ] **A/B:** retest 120 giây có residual từng mẫu <3°C và requested/actual current >=90% reference; nóng/tải thấp/stale reset timer; stop run và Datadog OK không tự resolve.

## 6. G3 — UI, độ bền và điều tra

Nguồn: [Implementation gates 3–6](docs/IMPLEMENTATION.md), [Contract](docs/CONTRACT.md).

- [ ] **C:** năm màn hình tiếng Việt dùng API thật; unit, freshness, provenance, mode/source, timeline và evidence trước/sau; đủ loading/error/empty states.
- [ ] **A:** kiểm tra idempotency key, khác payload trả 409, ACK timeout 30 giây, ACK muộn; restart API/worker/simulator không mất state hoặc chạy lệnh hai lần.
- [ ] **A:** test webhook trùng/muộn/sai run/thiếu identity, một incident active mỗi run/connector/type; bộ đếm 20 live run tồn tại qua restart.
- [ ] **A/C:** viewer không điều khiển; operator session/Origin hoặc CSRF đúng contract; simulator/webhook secret riêng, không vào log/frontend/Git.
- [ ] **Chính:** local hoàn chỉnh chạy offline sau khi tải images/packages; ngắt Internet trong mode Datadog hiện lỗi rõ, không fallback âm thầm.
- [ ] **Chính/C:** diễn tập điều tra từ alert đến residual/load/ambient/freshness và logs liên quan, rồi chứng minh hành động có tác dụng; UI không dùng mock khi API lỗi.

## 7. G4 — Đánh giá và khóa tính năng

Owner B, chính tích hợp, reviewer độc lập. Nguồn: [Data card — Đối chứng](docs/DATA_CARD.md), [PLAN — Nghiệm thu](PLAN.md). Các chỉ tiêu dưới đây là mục tiêu nội bộ, không phải ngưỡng BTC.

- [ ] Khóa seed tuning/evaluation tách biệt, baseline/ngưỡng A và C, simulator/config, policy/độ trễ operator/lịch maintenance trước evaluation; lưu protocol và version.
- [ ] Chạy đủ 20 fault +20 healthy scenarios × A/B/C = **120 lượt offline**, giữ cả lượt lỗi và thất bại; không đổi seed để cải thiện điểm.
- [ ] A = nhiệt cố định + can thiệp; B = residual không can thiệp; C = ChargeOps đầy đủ. Giữ cùng đầu vào ngoại sinh/thời lượng; noise/demand không lệch vì thứ tự random draws sau hành động.
- [ ] So sánh hai detector trên cùng telemetry chưa can thiệp của B; so sánh vận hành bằng A/B/C với nhiệt/dòng được simulator tính lại sau hành động.
- [ ] Báo mục tiêu >=18/20 fault phát hiện trước trip đối chứng, <=1 incident giả/20 healthy và không command trùng; công bố kết quả thực kể cả không đạt.
- [ ] Báo recall, false alerts/connector-hour, latency, lead time, thời gian giảm tải có tác dụng, trips/phút gián đoạn, kWh/chênh lệch có dấu. B không trip thì lead time không xác định; ghi mẫu số đủ điều kiện, không tự điền 0.
- [ ] Tách latency live Datadog khỏi đồng hồ mô phỏng; không ngoại suy hiệu quả trạm thật hoặc dự báo tuổi thọ từ simulator.
- [ ] Reviewer đối chiếu code/tests/evidence với contract; sửa vấn đề còn lại rồi khóa tính năng, ưu tiên correctness hơn thêm scope.

## 8. G5 — Bằng chứng và hồ sơ

Owner chính + C + trưởng đội. Nguồn: [PLAN — Bàn giao](PLAN.md), [Implementation gate 8](docs/IMPLEMENTATION.md), [thể lệ mục 7](https://mini-hackathon-sep-2026.mugvn.com/terms).

- [ ] Chạy ba demo trọn vòng liên tiếp cùng build; lưu SHA/run IDs/kết quả, gồm Datadog thật; video dự phòng không thay điều kiện tích hợp.
- [ ] Clone sạch và làm theo README: cấu hình, lockfiles/images, Compose, kiểm thử, trial/tunnel runbook; xác minh MVP chạy được trên máy trình diễn.
- [ ] Chuẩn bị tên/mô tả ChargeOps, thông tin đội/liên hệ qua kênh nộp, kiến trúc ứng dụng, data model MongoDB, kiến trúc Datadog và mô tả sự cố.
- [ ] Hoàn thiện repo/README, benchmark tái lập, data card, disclosure và giấy phép/attribution thực tế; ảnh/video chỉ từ hệ thống đã chạy.
- [ ] Làm video 6–8 phút và khoảng 10 slide theo **mục tiêu nội bộ**; xác nhận giới hạn định dạng/thời lượng từ form/email BTC trước xuất bản.
- [ ] Kiểm tra người nhận truy cập được repo và mọi artifact; chốt commit, nộp đủ hồ sơ trước hạn đã xác minh; lưu timestamp/biên nhận ngoài repo nếu sau deadline.
- [ ] Trước hạn nộp, giao trưởng đội checklist theo dõi quyền giám khảo đến 22/11 và lịch chung kết cập nhật; không cần sửa repo đã đóng băng để ghi trạng thái hậu nộp.

## Quy tắc giữ đúng hướng

Làm việc theo ưu tiên, phụ thuộc và bằng chứng nghiệm thu, không chia ngày cố định cho từng giai đoạn. Bắt đầu việc tiếp theo ngay khi điều kiện cần đã đạt; làm song song các việc độc lập có ownership rõ ràng. Hoàn thành sớm nhất có thể trong khung build chính thức, dành đủ thời gian phân tích và kiểm chứng việc phức tạp.

- [ ] Với mỗi việc phức tạp, ghi câu hỏi chưa rõ, giả thuyết và bằng chứng cần thu; chia thành phần có thể kiểm chứng, xử lý rủi ro lớn trước và kiểm tra tích hợp sớm.
- [ ] Khi có vấn đề bất ngờ, ghi hiện tượng và ảnh hưởng, tái hiện hoặc thu log, tìm nguyên nhân, sửa và kiểm chứng lại luồng bị ảnh hưởng trước khi đóng việc.
- [ ] Cập nhật checklist, phụ thuộc và ưu tiên sau mỗi phát hiện mới; tiếp tục việc độc lập khi có blocker, đưa blocker ảnh hưởng nghiệm thu lên ưu tiên cao nhất và ghi rõ bước gỡ tiếp theo.

Thứ tự ưu tiên: tư cách dự thi → G1 MongoDB/Datadog thật → G2 xử lý có chứng cứ → G3 độ bền → G4 đối chứng → G5 trình bày. Chỉ đánh dấu gate khi evidence tồn tại. Không mở rộng thanh toán, app tài xế, OCPP/phần cứng, paid cloud hoặc AI để trang trí. Nếu thiếu thời gian, giảm phần đánh bóng trước; không bỏ điều kiện bắt buộc hoặc che kết quả không đạt.
