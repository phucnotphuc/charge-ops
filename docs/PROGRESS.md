# Tiến độ — cập nhật 20/10/2026

Plan: PLAN.md. Phạm vi hiện tại: chuẩn bị; phần cốt lõi từ 17/10 theo quyết định người dùng.

## Cập nhật 20/10/2026 (chiều) — G2 vòng xử lý chạy thật ở local

Chi tiết: MINIMAL_LOOP.md, mục "Vòng xử lý G2".

- Simulator có quán tính nhiệt và đáp ứng lệnh; baseline `ema-tau45-v1` trễ bậc 1 (tải đổi khi không lỗi → residual 0). Run tối đa 1800 s.
- State machine `open → acknowledged → mitigating → mitigated → verifying → resolved` theo CONTRACT §3 trong `chargeops/lifecycle.py`; command API có idempotency; `chargeops.demo` tự đóng vai operator. 20 tests qua.
- Run local `5c623c09…`: `opened` t+319 s → `resolved` t+664 s; ref 60 A, giới hạn gốc 200 A, target 30 A; `mitigated` t+498 s (residual 2,98).
- Run Datadog trọn vòng: đang chạy (lượt 3/20).
- Chưa làm trong G2: 2 trạm/6 connector, kiểm định dataset; baseline chưa có nhiễu (G4).

## Cập nhật 20/10/2026 — gate G1 đạt với MongoDB và Datadog thật

Commit `ccb67d3` trên `main` (đã push). Chi tiết và timeline: MINIMAL_LOOP.md, mục "Kết quả kiểm chứng thật — 20/10/2026".

- Docker Engine chạy; Compose stack (mongo, api, worker, simulator, datadog, webhook-gateway, tunnel) healthy. `check_mongo` PASS trên MongoDB 8.0.5 thật.
- Run local `90b92398…` (thermal_fault): đúng một incident `source=local`.
- **Run Datadog `f738bf26…` (thermal_fault): composite monitor `334614967` Triggered → webhook qua tunnel → đúng một incident `source=datadog`.** Webhook delivery ~1,7 s; fault → incident ~2 phút 40 giây (chưa phải benchmark MTTD).
- Reliability: replay cùng webhook trả `duplicate`; Datadog `Recovered` thật không đóng incident; alert sau khi run dừng bị từ chối; restart api/worker và `wsl --shutdown` giữ nguyên incident/inbox/budget.
- Run Datadog healthy `640e418e…`: đạt — ~7,5 phút không incident/webhook, residual 0,0, quality 26/26 cửa sổ; 23/26 cửa sổ đúng 12 mẫu sau sửa simulator.
- Ngân sách live run: 2/20 đã dùng (trial `hackathon-2026`). Datadog trial còn 9 ngày tính từ 20/10 (hết ~29/10, trước hạn nộp 31/10) → lưu evidence sớm.
- Sửa trong lượt: dashboard widget thiếu `response_format` (HTTP 400); simulator ngủ theo mốc 5 giây wall-clock vì timer VM Docker Desktop chạy nhanh ~7% (`.wslconfig` clocksource không khắc phục được, đã gỡ); test missing-monitor bị `.env` thật ảnh hưởng.
- Log Management: đủ 6 log webhook của run `f738bf26…` trên Datadog (CSV đã lưu `artifacts/`). Log giờ có `timestamp`, `component`, và `webhook_processed` có `connector_id`/`incident_id`; đã kiểm chứng trên Datadog bằng webhook thủ công `manual-log-check-20261020`.
- Đối chiếu MongoDB ↔ Datadog: healthy 86/86 mẫu, residual 0; thermal 39/40 block khớp từng phút, block 02:52:00 mất do `wsl --shutdown` tắt Agent trước khi flush (giới hạn UDP đã biết).
- Còn lại G1: lưu ảnh dashboard/monitor/incident đã che URL tunnel vào `artifacts/` (nếu chưa); chưa có web/frontend; chưa đối chiếu từng trường payload webhook với CONTRACT.md. Tiếp theo: G2.

## Trạng thái ngày 14/10

**Cập nhật triển khai 14/10:** theo yêu cầu mới, đã xây code luồng tối thiểu trước 17/10, cài dependencies/uv.lock và cấu hình Compose. Có tests logic/API; chưa chạy MongoDB/container/Datadog thật. Xem MINIMAL_LOOP.md và DISCLOSURE.md. Các ghi chú chưa code/chưa cài dependencies bên dưới là trạng thái trước yêu cầu này. Không commit/push trong lượt triển khai này theo yêu cầu bỏ qua bước đó.

Kiểm tra thực tế: 13 tests logic/API qua, Ruff/Python compile/Compose config qua; chưa đạt gate Datadog live. Có script kiểm tra MongoDB thật dùng database tạm riêng, script khởi tạo secrets local và provision dashboard/monitors/webhook. `.env` được gitignore, chưa có Datadog keys; không ghi secrets vào tài liệu.

- Workspace hiện tại: `C:\Users\thoai\Documents\charge-ops`; đường dẫn D:\devops trong ghi chú cũ là lịch sử môi trường trước.
- Người dùng xác nhận đã hoàn tất đăng ký hackathon và bước kiểm tra Datadog trial.
- Đã kiểm tra công cụ/RAM/ổ đĩa; xem ENVIRONMENT.md. Docker Engine chưa chạy/kết nối được kể cả ngoài sandbox; RAM trống khoảng 2 GiB. Cần mở Docker Desktop và kiểm tra lại trước build.
- Người dùng chốt kịch bản một connector tổng hợp → MongoDB → Datadog alert/webhook thật → incident cho gate 17–18/10.
- Năm tài liệu điều chỉnh đã commit/push main tại f642417; các ghi chú chờ commit/push bên dưới là lịch sử trước commit này.
- Chưa triển khai phần cốt lõi hoặc chạy thử sản phẩm; xác nhận trial không đồng nghĩa đã tích hợp Datadog thật.

## Hoàn thành có bằng chứng

- Khảo sát yêu cầu cuộc thi, dataset và kiến trúc local.
- Tạo Git repository độc lập tại D:\devops\charge-ops; origin đã cấu hình.
- Tạo phucnotphuc/charge-ops private; gh trả isPrivate=true.
- GitHub authentication và Docker Engine truy cập được trong user context. Không cần login/cài lại.
- Sub-agent dependencies hoàn thành DEPENDENCIES.md và DATA_CARD.md.
- Sub-agent contract tạo CONTRACT.md và IMPLEMENTATION.md; cuối lượt agent gặp giới hạn usage, các file vẫn tồn tại và đã đọc lại.
- Agent chính chuẩn bị PLAN.md, README.md, .gitignore, ENVIRONMENT.md và DISCLOSURE.md.

## Lịch sử bàn giao trước commit f642417

- Đã chép 10 file vào D:\devops\charge-ops và kiểm tra SHA256 từng file. Hệ thống duyệt đã cho phép tiếp tục; đang hoàn tất commit/push tài liệu.
- Review độc lập đã hoàn tất: năm phát hiện được sửa và re-review xác nhận; không còn mâu thuẫn quan trọng trong phạm vi sửa. Chín file Markdown đã kiểm tra link local, không có link thiếu.
- Cài dependencies/lockfiles, Compose, code sản phẩm, Datadog thật, benchmark, slide/video: chưa làm; phần cốt lõi bắt đầu từ 17/10.

## Quy trình bàn giao trước đây (commit/push tài liệu đã hoàn tất)

1. Đọc tài liệu hiện tại và kết quả review; không lặp lại việc tạo repo.
2. Khi approval hoạt động, kiểm tra thư mục đích và remote để tránh đè thay đổi người dùng.
3. Chép đúng bộ tài liệu đã review, gồm file ẩn .gitignore nhưng không chép .git.
4. Kiểm tra diff/secret, commit tài liệu; xác minh private rồi push main.
5. Ghi commit SHA và kết quả push thực tế. Chưa có bằng chứng push thì không báo đã có tài liệu trên GitHub.
6. Chỉ bắt đầu code từ 17/10; không có automation đã được tạo.

## Quyết định kỹ thuật sau review

### Cập nhật theo yêu cầu ba điểm — 14/10/2026

Đã cập nhật thiết kế và tiêu chí nghiệm thu; chưa triển khai hoặc đo kết quả:

- Giảm tải xét cả giới hạn gốc và dòng tham chiếu thực tế; ACK không thay bằng chứng dòng giảm. CONTRACT.md quy định mục tiêu, cửa sổ kiểm chứng và xử lý không hiệu quả/thiếu dữ liệu.
- Ưu tiên gate một connector → MongoDB → Datadog alert/webhook thật → incident ngay khi nền tảng sẵn sàng trong khung build chính thức, trước khi mở rộng UI và tính năng; đồng bộ thứ tự triển khai trong PLAN.md/IMPLEMENTATION.md.
- Bổ sung đối chứng ngưỡng nhiệt cố định, giữ phương án không can thiệp và ChargeOps đầy đủ; DATA_CARD.md tách so sánh detector trên cùng telemetry với so sánh tác động vận hành.

### Các quyết định giữ nguyên

- Một cửa sổ 60 giây trên các block 15 giây tránh Datadog làm mượt hai lần; kiểm chứng latency khi tích hợp thật.
- Đóng băng dòng tham chiếu trước giảm tải và yêu cầu tải/nhiệt hợp lệ suốt 120 giây retest; cần giữ simulator đủ nhu cầu tải để demo hoàn tất.
- Tách composition run/mẫu khỏi provenance từng trường để không nhập nhằng nguồn dữ liệu.
- Cho phép unique run_id metric tag với trần 20 live run mỗi trial, lưu bền vững; đổi lại có giới hạn số lần demo cloud, offline evaluation không dùng ngân sách này.
- Remote đã tạo private; tài liệu đã copy. Commit/push được xác nhận bằng Git sau bước này. Không báo nghiệm thu sản phẩm hoặc dependencies đã cài.
