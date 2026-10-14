# Tiến độ chuẩn bị — 14/10/2026

Plan: PLAN.md. Phạm vi hiện tại: chuẩn bị; phần cốt lõi từ 17/10 theo quyết định người dùng.

## Hoàn thành có bằng chứng

- Khảo sát yêu cầu cuộc thi, dataset và kiến trúc local.
- Tạo Git repository độc lập tại D:\devops\charge-ops; origin đã cấu hình.
- Tạo phucnotphuc/charge-ops private; gh trả isPrivate=true.
- GitHub authentication và Docker Engine truy cập được trong user context. Không cần login/cài lại.
- Sub-agent dependencies hoàn thành DEPENDENCIES.md và DATA_CARD.md.
- Sub-agent contract tạo CONTRACT.md và IMPLEMENTATION.md; cuối lượt agent gặp giới hạn usage, các file vẫn tồn tại và đã đọc lại.
- Agent chính chuẩn bị PLAN.md, README.md, .gitignore, ENVIRONMENT.md và DISCLOSURE.md.

## Chưa hoàn thành

- Đã chép 10 file vào D:\devops\charge-ops và kiểm tra SHA256 từng file. Hệ thống duyệt đã cho phép tiếp tục; đang hoàn tất commit/push tài liệu.
- Review độc lập đã hoàn tất: năm phát hiện được sửa và re-review xác nhận; không còn mâu thuẫn quan trọng trong phạm vi sửa. Chín file Markdown đã kiểm tra link local, không có link thiếu.
- Cài dependencies/lockfiles, Compose, code sản phẩm, Datadog thật, benchmark, slide/video: chưa làm; phần cốt lõi bắt đầu từ 17/10.

## Tiếp tục an toàn

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
- Đưa gate một connector → MongoDB → Datadog alert/webhook thật → incident vào 17–18/10, trước khi mở rộng UI và tính năng; đồng bộ lịch và thứ tự triển khai trong PLAN.md/IMPLEMENTATION.md.
- Bổ sung đối chứng ngưỡng nhiệt cố định, giữ phương án không can thiệp và ChargeOps đầy đủ; DATA_CARD.md tách so sánh detector trên cùng telemetry với so sánh tác động vận hành.

### Các quyết định giữ nguyên

- Một cửa sổ 60 giây trên các block 15 giây tránh Datadog làm mượt hai lần; kiểm chứng latency khi tích hợp thật.
- Đóng băng dòng tham chiếu trước giảm tải và yêu cầu tải/nhiệt hợp lệ suốt 120 giây retest; cần giữ simulator đủ nhu cầu tải để demo hoàn tất.
- Tách composition run/mẫu khỏi provenance từng trường để không nhập nhằng nguồn dữ liệu.
- Cho phép unique run_id metric tag với trần 20 live run mỗi trial, lưu bền vững; đổi lại có giới hạn số lần demo cloud, offline evaluation không dùng ngân sách này.
- Remote đã tạo private; tài liệu đã copy. Commit/push được xác nhận bằng Git sau bước này. Không báo nghiệm thu sản phẩm hoặc dependencies đã cài.
