# Kịch bản video demo và dàn ý slide

Cập nhật 28/10/2026. Đề yêu cầu demo một sự cố vận hành từ lúc xảy ra tới khi giải quyết, qua ba giai đoạn **phát hiện, điều tra, phản ứng**, rồi chứng minh phục hồi bằng dữ liệu. Video chỉ quay hệ thống thật; không dùng ảnh mô phỏng hay số liệu tự điền.

**Trial Datadog hết hạn khoảng 29/10/2026.** Phải quay xong phần Datadog trước thời điểm đó; sau đó giám khảo chỉ còn thấy Datadog qua video và ảnh chụp.

## 1. Chuẩn bị trước khi quay

| Việc | Lệnh / cách kiểm tra |
|---|---|
| Stack đầy đủ đang chạy | `docker compose --profile observability ps`: `mongo`, `api` healthy; `worker`, `simulator`, `web`, `datadog`, `webhook-gateway`, `tunnel`, `datadog-sync` Up |
| Agent gửi đúng site | `docker compose --profile observability exec datadog agent status` → `API Key valid`, `datadoghq.com` |
| Tài nguyên Datadog khớp URL tunnel hiện tại | `docker compose logs tunnel` lấy URL; nếu đổi, chạy lại `python scripts/setup_datadog.py --webhook-url <URL>/api/v1/webhooks/datadog --with-app` |
| Không có run đang chạy | Trang **Điều khiển demo**; mỗi run Datadog tốn 1 lượt (đã dùng 10/20 ngày 28/10) |
| Trình duyệt | Đăng nhập operator ở `http://localhost:8080`; mở sẵn tab Datadog: dashboard kỹ sư, Monitors, Incidents, Apps, Logs, APM, RUM |
| Ẩn thông tin nhạy cảm | Không quay `.env`, API/App key, `DATADOG_ACTION_SECRET`, email cá nhân |

Run gợi ý: mode `datadog`, scenario `thermal_fault`, **seed 42** (ngày 28/10 lỗi rơi vào `connector-1`, sự cố mở ở t+480 s). Quay liên tục rồi cắt đoạn chờ; cả vòng mất khoảng 13–15 phút thực.

## 2. Kịch bản video (mục tiêu 6–8 phút)

| # | Thời lượng | Hình | Lời dẫn (ý chính) |
|---|---|---|---|
| 1 | 0:00–0:40 | Slide bài toán, ảnh màn hình trạm | Connector nóng theo I². Ngưỡng cố định vừa báo muộn ở tải thấp vừa báo nhầm ở tải cao. ChargeOps so nhiệt đo với nhiệt kỳ vọng theo tải và môi trường. |
| 2 | 0:40–1:20 | Sơ đồ kiến trúc (SUBMISSION.md §3) | Simulator replay 50 phiên sạc thật (Autosun–PolyU), MongoDB là nơi chứa mọi trạng thái, worker là writer duy nhất, Datadog quan sát và phát hiện, `datadog-sync` mang sự cố lên Datadog. |
| 3 | 1:20–1:50 | **Điều khiển demo**: tạo run `datadog` / `thermal_fault` / seed 42, bấm Bắt đầu; màn hình `/stations/station-1` | 6 đầu sạc, pin % lấy từ dữ liệu thật. Lỗi sẽ được gieo vào một connector, detector không biết connector nào. |
| 4 | 1:50–2:40 | **PHÁT HIỆN.** Dashboard kỹ sư: residual một connector vượt vạch 8 °C trong khi nhiệt tuyệt đối chưa cao; Monitors: composite "ChargeOps thermal incident" chuyển Alert; ChargeOps **Sự cố**: đúng 1 sự cố `source=datadog` | Residual chứ không phải nhiệt tuyệt đối. Monitor chỉ báo khi cửa sổ dữ liệu đạt chất lượng. Webhook vào ChargeOps, ghi inbox MongoDB trước rồi mới xử lý. |
| 5 | 2:40–4:00 | **ĐIỀU TRA.** Datadog Incidents: Incident "ChargeOps connector-1 quá nhiệt" tự tạo; mở Notebook điều tra; dashboard: nhiệt đo so với kỳ vọng, dòng/giới hạn, nhiệt môi trường; Logs lọc `@run_id`, mở một dòng log → trace APM | Không phải trời nóng (môi trường bình thường), không phải tải tăng (dòng ổn định): connector nóng hơn mức đáng có. Log, trace và metric nối với nhau bằng `run_id` và `trace_id`. |
| 6 | 4:00–5:30 | **PHẢN ỨNG.** Datadog App "ChargeOps - xu ly su co": bấm **Xác nhận**; ChargeOps cột Nguồn hiện "Datadog App". Trên web: **Giảm tải**; màn hình trạm đổi sang "Đang sạc chậm để bảo vệ đầu sạc"; timeline có `load_reduction_verified`; chờ nút **Bảo trì** sáng rồi bấm | Lệnh từ Datadog hay từ web đều qua cùng cổng của worker. Gửi lệnh chưa đủ: worker chờ telemetry chứng minh dòng thực tế ≤110% mục tiêu và residual hồi phục. |
| 7 | 5:30–6:30 | **PHỤC HỒI.** **Kiểm tra phục hồi**; retest 120 giây; sự cố "Đã giải quyết"; Datadog Incident `resolved`, Notebook có ô Postmortem; widget SLO | Kiểm tra ở tải gần như ban đầu, residual từng mẫu < 3 °C. Postmortem tính bằng `$lookup` + `$dateDiff` + `$merge` rồi đẩy lên Datadog. |
| 8 | 6:30–7:30 | Trang **Đánh giá** (A/B/C) | Trên 120 lượt mô phỏng khóa trước: phát hiện 20/20 so với 8/20 của ngưỡng cố định, 0 báo nhầm, trip từ 14 xuống 1. Đây là số mô phỏng, không phải số trạm thật. |
| 9 | 7:30–8:00 | Slide kết | MongoDB giữ trạng thái và bằng chứng, Datadog phát hiện, điều tra và phản ứng; hướng tiếp theo là OCPP với trụ thật. |

Nếu App Builder không dùng được trong lúc quay, thực hiện bước 6 hoàn toàn trên web và nói rõ.

## 3. Ảnh cần chụp (lưu vào `artifacts/`)

Đặt tên `2026-10-DD-<run8>-<nội-dung>.png`, ví dụ `2026-10-29-5e2bd546-monitor-alert.png`.

- [ ] Dashboard kỹ sư lúc residual vượt 8 °C, có event `opened` phủ lên
- [ ] Composite monitor ở trạng thái Alert, nội dung có link điều tra
- [ ] Trang sự cố ChargeOps: evidence, timeline, cột Nguồn của lệnh
- [ ] Datadog Incident: danh sách todo và link Notebook
- [ ] Notebook điều tra và ô Postmortem
- [ ] Log Explorer lọc `@run_id:<run>` và trace APM mở từ một dòng log
- [ ] Datadog App với một sự cố đang mở
- [ ] Màn hình trạm "Đang sạc chậm để bảo vệ đầu sạc"
- [ ] Dashboard IT/SRE: API p95, worker heartbeat
- [ ] Widget SLO sau run
- [ ] RUM Explorer: session có action `command_sent`, resource `/api/` nối sang trace
- [ ] Trang Đánh giá A/B/C

Che key, secret và email trước khi lưu. Ghi run ID, incident ID và commit vào PROGRESS.md sau khi quay.

## 4. Dàn ý slide chung kết (khoảng 10 trang)

| # | Tiêu đề | Nội dung | Nguồn số liệu |
|---|---|---|---|
| 1 | ChargeOps | Cảnh báo sớm quá nhiệt connector trạm sạc, xử lý có kiểm chứng; tên đội | — |
| 2 | Bài toán | Nhiệt theo I²; ngưỡng cố định báo muộn và báo nhầm; ACK không chứng minh đã xử lý | SUBMISSION.md §1 |
| 3 | Ý tưởng | Residual = nhiệt đo − nhiệt kỳ vọng (môi trường, tải, quán tính); cửa sổ 60 giây có kiểm tra chất lượng | CONTRACT.md §3 |
| 4 | Kiến trúc | Sơ đồ service; worker là writer duy nhất; một vòng xử lý cho cả local và Datadog | SUBMISSION.md §3 |
| 5 | MongoDB | Time Series + TTL, aggregation cửa sổ, `$setWindowFields`, `$facet`, `$merge`, Change Streams, unique partial index | DATA_MODEL.md |
| 6 | Datadog | Composite monitor → webhook; dashboard; Logs ↔ APM ↔ RUM; Incidents, Notebook, SLO, App | OBSERVABILITY.md §0 |
| 7 | Vòng sự cố | Timeline phát hiện → điều tra → phản ứng → phục hồi của run đã quay | Run quay video; run `39207f81…` |
| 8 | Kết quả | Bảng A/B/C: 20/20 so với 8/20, 0 báo nhầm, trip 14 → 1, gián đoạn 59,75 → 4,9 phút, chi phí −22,5 kWh | SUBMISSION.md §5 |
| 9 | Trung thực | Dữ liệu tải thật, nhiệt và lỗi mô phỏng; Datadog trial; phần làm trước 17/10; AI hỗ trợ | DATA_CARD.md, DISCLOSURE.md |
| 10 | Tiếp theo | OCPP `SetChargingProfile` với trụ thật, hiệu chỉnh baseline từ dữ liệu thật, nhiều site | ARCHITECTURE.md §6 |
