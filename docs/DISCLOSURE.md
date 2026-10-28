# Khai báo chuẩn bị và công cụ

## Trước thời gian build

Giai đoạn đầu ngày 14/10/2026: khảo sát đề và dataset qua nguồn công khai, lập plan/contract/dependencies/data card, kiểm tra công cụ sẵn có, tạo repository private và tài liệu chuẩn bị; lúc đó chưa viết code.

Sau đó trong cùng ngày, người dùng yêu cầu bỏ qua bước kiểm tra Docker/commit tài liệu và tiến đến xây luồng tối thiểu ngay. Đã viết API, MongoDB aggregation, worker, simulator tổng hợp, Compose, scripts Datadog và tests trước 17/10. Các thành phần này phải khai báo là có sẵn trước thời gian build; không mô tả là xây trong 17–31/10. Việc bắt đầu sớm không thay đổi thể lệ BTC hoặc bảo đảm phần này được chấm là giải pháp cốt lõi. Giữ nguyên ngày thực tế và lịch sử commit.

## AI

Codex và sub-agents hỗ trợ khảo sát, soạn tài liệu và review chuẩn bị. Ngày 17/10, Claude Code hỗ trợ review tài liệu theo thể lệ và soạn ARCHITECTURE.md (seams mở rộng, lộ trình sau thi). Khi build, cập nhật khai báo các công cụ thực sự được dùng và phần mã/tài liệu được hỗ trợ. Nhóm chịu trách nhiệm hiểu và giải thích giải pháp.

Luồng tối thiểu ngày 14/10 do Codex hỗ trợ viết code, tests, cấu hình và runbook; không dùng sub-agent trong lượt triển khai này. Tests dùng mongomock, không phải bằng chứng MongoDB/Datadog thật. *(Câu cũ "dữ liệu runtime hoàn toàn tổng hợp, chưa dùng dataset" chỉ đúng tới 20/10.)* Từ 20/10, simulator replay đường tải (dòng, điện áp) của 50 phiên Autosun–PolyU v3 đã kiểm định; nhiệt độ, lỗi và phản ứng can thiệp vẫn là mô phỏng (provenance `hybrid`, xem DATA_CARD.md).

### Thời gian build 17–21/10

Phần lớn mã trong thời gian build do **Claude Code** viết: một phiên điều phối (controller, Claude Opus 5.5) chia việc theo gói, giao cho sub-agent (Claude Sonnet 5.5 và Claude Opus 5.5), mỗi gói có review độc lập bởi sub-agent khác trước khi merge, và một lượt review toàn nhánh cuối cùng. Phạm vi do AI viết: backend API và đăng nhập/session, observability (metrics/logs/APM, dashboard/monitor/webhook), giao diện web React, Compose/Caddy/Dockerfile, đánh giá offline A/B/C, fleet 2 trạm × 3 connector, tests và phần lớn tài liệu. Nhóm quyết định quy tắc sản phẩm (ví dụ các thay đổi v2/v3 ngày 21/10, xem EVALUATION_PROTOCOL.md), duyệt kết quả và chịu trách nhiệm hiểu, giải thích giải pháp. Unit test do AI viết chỉ chứng minh logic; không coi là bằng chứng chạy thật.

## Dữ liệu và thành phần tái sử dụng

Nguồn ứng viên: Autosun–Hong Kong Polytechnic, Mendeley v3, https://data.mendeley.com/datasets/c7gg94tmvz/3 (CC BY 4.0 theo trang nguồn). Đã tải và kiểm định ngày 20/10 (SHA-256 khớp nguồn), dùng làm mẫu đường tải; xem DATA_CARD.md. Khi dùng thực tế phải ghi phiên bản/checksum/biến đổi và nguồn từng trường.

Dependencies Python khóa tại `uv.lock`, frontend tại `web/package-lock.json`. Thành phần mã nguồn mở tái sử dụng chính (license đọc từ metadata package đã cài ngày 21/10, trừ khi ghi khác):

| Thành phần | Phiên bản | License |
|---|---|---|
| React, React DOM | 19.3.1 | MIT |
| React Router | 7.18.4 | MIT |
| Recharts | 3.10.1 | MIT |
| lucide-react | 1.60.0 | ISC |
| Vite | 8.3.4 | MIT |
| @datadog/browser-rum | 5.35.1 | Apache-2.0 |
| FastAPI | 0.143.1 | MIT |
| pydantic / pydantic-settings | 2.14.1 / 2.16.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| pymongo | 4.18.3 | Apache-2.0 |
| ddtrace | 3.19.9 | BSD-3-Clause hoặc Apache-2.0 (metadata ghi `LICENSE.BSD3`; dự án phát hành song license) |
| datadog (DogStatsD client) | 0.55.0 | BSD-3-Clause |
| Caddy (image `caddy:2.9.1-alpine`) | 2.9.1 | Apache-2.0 (theo dự án upstream, không đọc từ metadata) |
| cloudflared (image) | 2025.2.1 | Apache-2.0 (theo dự án upstream) |
| MongoDB Community (image `mongo:8.0.5`) | 8.0.5 | SSPL-1.0 (theo dự án upstream) |
| Datadog Agent (image) | 7.66.1 | Apache-2.0 cho mã nguồn Agent (theo dự án upstream); dịch vụ Datadog dùng bản trial |

Danh sách đầy đủ phụ thuộc gián tiếp nằm trong hai lockfile. License dataset không suy ra từ license thư viện truy cập dataset; dataset Autosun–PolyU v3 là CC BY 4.0 theo trang nguồn (DATA_CARD.md).
