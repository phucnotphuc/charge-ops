# Khai báo chuẩn bị và công cụ

## Trước thời gian build

Giai đoạn đầu ngày 14/10/2026: khảo sát đề và dataset qua nguồn công khai, lập plan/contract/dependencies/data card, kiểm tra công cụ sẵn có, tạo repository private và tài liệu chuẩn bị; lúc đó chưa viết code.

Sau đó trong cùng ngày, người dùng yêu cầu bỏ qua bước kiểm tra Docker/commit tài liệu và tiến đến xây luồng tối thiểu ngay. Đã viết API, MongoDB aggregation, worker, simulator tổng hợp, Compose, scripts Datadog và tests trước 17/10. Các thành phần này phải khai báo là có sẵn trước thời gian build; không mô tả là xây trong 17–31/10. Việc bắt đầu sớm không thay đổi thể lệ BTC hoặc bảo đảm phần này được chấm là giải pháp cốt lõi. Giữ nguyên ngày thực tế và lịch sử commit.

## AI

Codex và sub-agents hỗ trợ khảo sát, soạn tài liệu và review chuẩn bị. Khi build, cập nhật khai báo các công cụ thực sự được dùng và phần mã/tài liệu được hỗ trợ. Nhóm chịu trách nhiệm hiểu và giải thích giải pháp.

Luồng tối thiểu ngày 14/10 do Codex hỗ trợ viết code, tests, cấu hình và runbook; không dùng sub-agent trong lượt triển khai này. Tests dùng mongomock, không phải bằng chứng MongoDB/Datadog thật. Dữ liệu runtime hiện hoàn toàn tổng hợp; chưa tải hoặc dùng dataset công khai.

## Dữ liệu và thành phần tái sử dụng

Nguồn ứng viên: Autosun–Hong Kong Polytechnic, Mendeley v3, https://data.mendeley.com/datasets/c7gg94tmvz/3 (CC BY 4.0 theo trang nguồn). Chưa tuyên bố đã tải/kiểm định file. Xem DATA_CARD.md. Khi dùng thực tế phải ghi phiên bản/checksum/biến đổi và nguồn từng trường.

Dependencies cho luồng tối thiểu đã cài trong `.venv`, phiên bản khóa tại `uv.lock`; phần frontend/evaluation đầy đủ vẫn là dự kiến. Cần hoàn thiện kiểm kê license cho hồ sơ nộp; không tuyên bố license dataset theo license thư viện truy cập dataset.
