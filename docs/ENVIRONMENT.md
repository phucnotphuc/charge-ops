# Kiểm tra môi trường — 14/10/2026

Đây là kiểm tra công cụ có sẵn, không phải kiểm thử sản phẩm.

| Công cụ | Kết quả |
|---|---|
| Git | 2.52.0.windows.1 |
| npm | 11.6.1 |
| Node | v22.20.0 |
| Python hệ thống | 3.14.0; giữ nguyên, dự án dự kiến Python 3.12 container |
| Docker Compose | v2.40.3-desktop.1 |
| Docker Engine | 29.1.3, kết nối được trong user context |
| GitHub CLI | Đăng nhập phucnotphuc bằng keyring trong user context |
| GitHub repo | Đã tạo phucnotphuc/charge-ops và xác minh isPrivate=true; chưa push |
| uv | Chưa tìm thấy trong PATH ở lượt kiểm tra; chưa cài |
| Datadog trial | Chưa xác minh tài khoản/keys/thời hạn |
| Project dependencies | Chưa cài; chưa có lockfile hoặc build |

Sandbox từng báo GitHub HTTP401 và không thấy Docker pipe. Kiểm tra cùng công cụ trong user context cho thấy GitHub đăng nhập thành công và Engine đang chạy; không yêu cầu người dùng login lại. Không xuất hoặc sao chép token. D:\devops ngoài writable roots của sandbox. Đã tạo thư mục/repo local và remote qua quyền phù hợp; đã copy tài liệu, bước commit/push tiếp theo được kiểm tra riêng.

Khi bắt đầu build: kiểm tra dung lượng/RAM Docker, image compatibility, chạy smoke containers, cài/khóa dependencies và ghi lại kết quả. Không dùng trạng thái tool-ready như bằng chứng Compose/ứng dụng đã chạy.

## Bàn giao đang chờ

Repository local đã init main, origin trỏ https://github.com/phucnotphuc/charge-ops.git. Đã chép đủ 10 file tài liệu sang thư mục đích và kiểm tra SHA256 khớp nguồn. Commit/push được thực hiện sau khi kiểm tra nội dung. Lệnh nâng quyền kiểm tra bổ sung bị hệ thống automatic approval báo hết hạn mức; không thực thi. Đây là lỗi hệ thống duyệt, không phải xác định thao tác không an toàn. Người dùng chọn giữ tài liệu để hoàn tất khi hệ thống duyệt hoạt động lại; không yêu cầu ZIP hay automation. Không thử đường vòng quyền.
