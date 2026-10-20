# Dependencies — chuẩn bị trước ngày 17/10/2026

**Cập nhật 14/10:** đã cài dependencies Python của luồng tối thiểu trong `.venv` và khóa tại `uv.lock` theo yêu cầu bắt đầu ngay. `pyproject.toml` là manifest thực tế; frontend, pandas/evaluation và APM chưa cài. Có Dockerfile/Compose với image tag cụ thể nhưng chưa build/pull kiểm chứng. Nội dung dưới đây giữ danh mục mục tiêu ban đầu, không phải trạng thái mới nhất.

Đây là danh mục đã chọn, không phải bằng chứng đã cài đặt hoặc đã chạy thành công. Không có package manifest, lockfile hay cấu hình thực thi trong giai đoạn chuẩn bị. Phần cốt lõi bắt đầu từ 17/10 theo quyết định của nhóm.

## Công cụ và runtime

| Thành phần | Mục tiêu | Cách sử dụng |
|---|---|---|
| Git | Phiên bản hiện có, xác nhận bằng `git --version` | Repository riêng, private trên GitHub |
| Docker Desktop + Compose | Linux containers, Engine truy cập được | Chạy ứng dụng và MongoDB local |
| Node.js | Nhánh 22, tối thiểu 22.12 cho Vite hiện hành | Host hoặc build container; kiểm tra `node --version` |
| npm | Bản tương thích Node đã chọn | Khóa dependency bằng `package-lock.json` |
| Python | 3.12 trong container | API, worker, simulator; không thay Python hệ thống |
| uv | Chốt phiên bản khi triển khai | Tạo `uv.lock`, đồng bộ môi trường Python |
| GitHub CLI hoặc connector | Xác thực đúng tài khoản | Tạo và xác minh repo private, push |
| Trình duyệt | Chromium do Playwright quản lý khi triển khai | Kiểm thử giao diện |

Vite hiện yêu cầu Node 20.19+ hoặc 22.12+; cần kiểm tra thêm yêu cầu template khi khóa phiên bản. [Nguồn Vite](https://vite.dev/guide/)

## Packages dự kiến

| Nhóm | Packages |
|---|---|
| Frontend runtime | `react`, `react-dom`, `react-router-dom`, `recharts`, `lucide-react` |
| Frontend build | `typescript`, `vite`, `@vitejs/plugin-react`, `@types/react`, `@types/react-dom` |
| Frontend test | `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`, `@playwright/test` |
| API/domain | `fastapi`, `uvicorn`, `pydantic-settings`, `pymongo`, `httpx` |
| Dữ liệu | `numpy`, `pandas`, `openpyxl` |
| Observability | `datadog`, `ddtrace` |
| Python test/lint | `pytest`, `pytest-cov`, `ruff` |
| Contract client | `openapi-typescript` — sinh kiểu frontend từ OpenAPI |

Không tự đặt phiên bản package chưa kiểm thử. Từ 17/10: giải dependency trên Python 3.12/Node 22, chạy build/tests, commit hai lockfile. Khi tái lập dùng `npm ci` và `uv sync --frozen`. Chọn image MongoDB, Python, Node, Caddy, Datadog Agent và cloudflared có phiên bản cụ thể; ghi digest sau khi xác minh. Không dùng tag `latest` cho bản nghiệm thu.

## Hai chế độ triển khai dự kiến

| Chế độ | Dịch vụ | Điều kiện |
|---|---|---|
| Local mặc định | MongoDB, API, worker, simulator, frontend/Caddy | Không cần Internet sau khi tải đủ images/packages |
| Profile `observability` | Local + Datadog Agent + cloudflared | Internet, trial Datadog hợp lệ, API key và webhook secret |

Compose và các lệnh chạy chỉ được tạo/kiểm chứng trong giai đoạn build. MongoDB không mở cổng ra Internet. Caddy dành một listener chỉ cho webhook; tunnel trỏ listener đó, không trỏ toàn bộ giao diện điều khiển.

Cloudflare Quick Tunnel cấp URL tạm không cần domain/tài khoản; hostname đổi khi khởi động lại, không bảo đảm uptime và không hỗ trợ SSE. Webhook dùng secret, không dùng bước xác thực email tương tác. [Tài liệu Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## Điều kiện giữ chi phí dịch vụ bằng 0

- Dùng máy sẵn có, MongoDB local; không tạo EC2, Atlas, domain hoặc tài nguyên cloud trả phí.
- Xác nhận Docker Desktop phù hợp điều kiện sử dụng miễn phí của nhóm; không bật Build Cloud/Offload hoặc mua gói. [Docker plans FAQ](https://www.docker.com/pricing/faq/)
- Datadog trial cuộc thi: ghi ngày hết hạn, tính năng được cấp và giới hạn trong tài khoản trước khi bật ingest; không giả định số ngày hoặc quota. Không nâng cấp trả phí. Trial hết hạn thì dừng gửi dữ liệu và dùng local, nhưng chưa được coi đạt nghiệm thu Datadog thật.
- Chỉ gửi telemetry cần thiết, không ghi secrets vào logs. Metrics dùng tag ổn định, không dùng session/incident ID. Ngoại lệ có kiểm soát: unique run_id cho tối đa 20 lượt demo Datadog trong trial, lưu ngân sách bền vững và không tái sử dụng ID; đánh giá offline không xuất metric. Xem CONTRACT.md để tương quan webhook đúng run.
- Chưa bật GitHub Actions; kiểm thử local là bắt buộc. Tải package/image cần mạng và dung lượng ổ đĩa sẵn có.

## Kiểm tra trước khi bắt đầu build

1. Ghi phiên bản thực tế Git, Node, npm, Docker CLI và Compose vào báo cáo môi trường.
2. Xác nhận `docker info` thành công với Linux Engine; CLI tồn tại chưa chứng minh Engine hoạt động.
3. Kiểm tra đăng nhập GitHub và quyền repo private mà không in token.
4. Xác nhận trial Datadog còn hiệu lực đến diễn tập; API key chỉ lưu trong `.env` không commit.
5. Sau 17/10 mới cài package dự án, sinh lockfile và chạy smoke test containers.
