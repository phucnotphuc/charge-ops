# Protocol đánh giá A/B/C — ChargeOps

protocol_version: `b1-eval-v3.1`

**Lịch sử phiên bản.** `b1-eval-v1` (khóa 21/10/2026 04:53 UTC, file `chargeops/evaluation/locked-b1-eval-v1.json`) là lượt đầu. `b1-eval-v2` đổi quy tắc sản phẩm theo quyết định của người dùng (CONTRACT §3): (1) operator được bảo trì từ `mitigating` khi chu kỳ đã ghi `load_reduction_ineffective` sau khi giảm dòng đã được xác nhận; (2) cửa sổ có mẫu không sạc (trip) không bao giờ xác nhận giảm tải. Kèm theo: ngưỡng là hằng số `ALERT_RESIDUAL_C`/`RECOVERY_RESIDUAL_C` trong `domain.py`; fingerprint gồm SHA-256 mã nguồn đánh giá; detector-only ghép theo cạnh lên đầu tiên sau onset. Cùng seed tuning và đánh giá như v1; `tune` chạy lại trên seed tuning và khóa trước khi chạy đánh giá. `b1-eval-v3` sửa quy tắc mở khóa cho đúng quyết định: chỉ event `load_reduction_ineffective` reason `residual_not_recovered` (120 giây sau khi **xác nhận** giảm dòng, không phải sau ACK, residual vẫn chưa phục hồi; một lần mỗi chu kỳ) mới mở khóa bảo trì; `load_not_reduced`/`load_reduction_unverified` không mở khóa nhưng không chặn lần kiểm tra sau xác nhận. Bảo trì được ghi theo chu kỳ lúc phát lệnh. Fingerprint thêm `domain.py` và `evaluation/__main__.py`. `b1-eval-v3.1` (WP-W2B) là khóa lại chỉ đổi fingerprint, hành vi giữ nguyên: `domain.py`/`lifecycle.py`/`model.py` đổi cho fleet 2 trạm × 3 connector (và lần merge API trước đó đã làm khóa v3 bị từ chối); ngưỡng và chi tiết tuning chép từ v3, không tune lại; kết quả `run` giống từng byte với v3 trừ trường `protocol_version` (test `test_relocked_v3_1_reproduces_v3_results_exactly`). Mỗi phiên bản có file khóa riêng; v1/v2/v3 giữ làm lịch sử.

Protocol này chốt **trước** khi chạy tập đánh giá, theo DATA_CARD.md ("Đối chứng để chứng minh giá trị") và PLAN.md ("Nghiệm thu"). Mọi giá trị dưới đây nằm trong `chargeops/evaluation/protocol.py`; thay đổi bất kỳ giá trị nào làm đổi fingerprint, và `run` từ chối chạy với cấu hình đã khóa theo protocol cũ. Muốn đổi protocol phải tăng `protocol_version`, chạy lại `tune` và ghi lý do; không chỉnh protocol sau khi đã xem kết quả đánh giá.

Toàn bộ là **mô phỏng**: nhiệt độ trip, ngưỡng, độ trễ operator và lịch bảo trì là giả định mô phỏng, không phải giới hạn an toàn thiết bị thật. Không ngoại suy kết quả sang trạm sạc thật hay dự báo tuổi thọ. Latency ở đây đo trên đồng hồ mô phỏng, không suy ra latency Datadog.

## Cách chạy

```
uv run python -m chargeops.evaluation tune
uv run python -m chargeops.evaluation run --protocol b1-eval-v2 --out artifacts/evaluation/<timestamp>/
```

`tune` chỉ dùng seed tuning, ghi `chargeops/evaluation/locked-<protocol_version>.json` (version, fingerprint, seed, ngưỡng, thời điểm khóa) và từ chối ghi đè file khóa đã có cùng version. `run` từ chối nếu không có file khóa, khác version, khác fingerprint, hoặc seed đánh giá trùng/khác với seed đã khóa. Chạy hoàn toàn trong tiến trình: không MongoDB, không Datadog, không mạng. Kết quả ở `artifacts/` (Git ignore): `runs.jsonl` (mỗi dòng một lượt), `summary.json`, `summary.md`, bản sao cấu hình khóa.

## Ba phương án

| Phương án | Phát hiện (mở incident) | Can thiệp |
|---|---|---|
| A — ngưỡng cố định | Trung bình nhiệt tuyệt đối của cửa sổ 60 giây hợp lệ > ngưỡng A | Cùng workflow, operator và lịch bảo trì như C |
| B — không can thiệp | Residual theo tải như ChargeOps (> 8 °C) | Chỉ ghi incident; không command nào |
| C — ChargeOps | Residual theo tải (> 8 °C) | Workflow đầy đủ CONTRACT §3 |

A và C chỉ khác nhau ở detector mở incident (kể cả alert tái phát khi `verifying`). Các cổng sau đó — kiểm chứng giảm tải (dòng ≤110 % mục tiêu, giới hạn ≤ mục tiêu), `mitigated` khi residual < 3 °C, retest 120 giây — giữ nguyên quy tắc sản phẩm cho cả hai, để so sánh tách riêng giá trị của detector. Cả hai detector dùng cùng cửa sổ 60 giây `[end − 60 s, end)`, cùng quality gate (`domain.window_evidence`) và cửa sổ kết thúc trên mốc 15 giây.

Detector và quy tắc dùng lại hàm sản phẩm: `with_residuals`, `window_evidence`, `target_current` (200 A/60 A → 30 A) từ `domain.py`; hằng số `RECOVERED_C`, `CURRENT_TOLERANCE`, `LOAD_FLOOR`, `RETEST_S`, `REDUCTION_CHECK_S` từ `lifecycle.py`. Vì `lifecycle.py` gắn với MongoDB, `chargeops/evaluation/rules.py` viết lại tối thiểu các quy tắc trạng thái trong bộ nhớ; test ghim chúng vào cùng hằng số. Ngưỡng alert 8 °C và phục hồi 3 °C là hằng số `domain.ALERT_RESIDUAL_C`/`RECOVERY_RESIDUAL_C`, dùng chung bởi worker, `lifecycle.py` và `rules.py`. Các vị từ thuần `charging` và `maintenance_unlocked` được import trực tiếp từ `lifecycle.py`. Test parity chạy cùng chuỗi cửa sổ/lệnh qua `lifecycle.py` (mongomock) và `rules.py`, so sánh trạng thái, các trường `*_at` và timeline (vòng đầy đủ, bảo trì sau ineffective, cửa sổ trip, reset retest, tái phát).

## Seed và kịch bản

| Tập | Seed lỗi (`thermal_fault`) | Seed khỏe |
|---|---|---|
| Tuning | 1000–1019 | 1020–1039 |
| Đánh giá | 2000–2019 | 2020–2039 |

20 seed khỏe của mỗi tập được gán theo vị trí (không theo RNG): 8 `healthy` (replay nguyên đường tải), 6 `load_increase`, 6 `demand_drop`. Không đổi, bỏ hay thêm seed sau khi xem kết quả; mọi lượt, kể cả lượt thất bại, đều được ghi.

Mỗi lượt dài **1800 s** (30 phút mô phỏng, bằng `RUN_MAX_S` của sản phẩm), bước 5 giây (360 mẫu), cold start ở nhiệt môi trường.

## Đầu vào ngoại sinh (`chargeops/model.py`)

Mỗi seed rút toàn bộ đầu vào ngoại sinh **một lần, trước khi chạy**, bằng `random.Random(seed)` theo thứ tự cố định, giống nhau cho mọi kịch bản. Hành động của A/C không thể làm lệch chuỗi random. Cùng seed → cùng đường tải, ambient, noise, tham số connector và lỗi cho cả ba phương án.

| Đại lượng | Phân bố |
|---|---|
| Đường tải | 1 trong 50 phiên Autosun–PolyU v3 đã kiểm định (chọn đều), replay nội suy 1 phút → 5 giây |
| Ambient | Nền đều 18–30 °C, dao động sin biên độ đều 0–1,5 °C, chu kỳ 1 giờ, pha ngẫu nhiên; cảm biến ambient đo đúng |
| Hệ số nhiệt thật k | 0,0013 × đều(0,95–1,05) — connector thật lệch baseline của detector ±5 % |
| τ thật | 60 s × đều(0,9–1,1) |
| Noise cảm biến nhiệt | Gauss σ 0,3 °C mỗi mẫu, cộng vào nhiệt đo; trip dùng nhiệt thật |
| `load_increase` | Từ thời điểm đều 300–1200 s, trong 60 s nhu cầu tăng lên max(nền, min(nền × đều(1,2–1,6), 200 A)) |
| `demand_drop` | Từ thời điểm đều 300–1200 s, trong 60 s nhu cầu giảm còn nền × đều(0,2–0,5) |

Trần 200 A của `load_increase` và biên trên của ambient/k bảo đảm connector khỏe không thể trip: trạng thái ổn định nóng nhất là 31,5 + 0,001365 × 213,4² ≈ 93,7 °C < 100 °C (có test).

## Lỗi (ground truth, chỉ simulator đọc)

Lỗi nhiệt kiểu điện trở tiếp xúc tăng: nhiệt thêm tỷ lệ I², định cỡ bằng °C ở tải lúc lỗi bắt đầu (cùng dạng với simulator demo), tăng theo hàm mũ bão hòa:

- Thời điểm bắt đầu: đều 300–900 s (bội số 5 s).
- Mức nghiêm trọng (°C thêm ở tải lúc bắt đầu, sau khi bão hòa): đều 10–80 °C. Lỗi < 10 °C nằm ngoài phạm vi protocol này (gần ngưỡng alert 8 °C); recall báo cáo chỉ có nghĩa với phân bố này.
- Hằng số tăng: đều 60–600 s.
- Bảo trì (`maintenance` đã áp dụng) xóa lỗi hoàn toàn. Lỗi không tự hết.

Giảm dòng còn một nửa làm nhiệt lỗi còn một phần tư. Với lỗi lớn, phần tư này vẫn ≥ 3 °C nên không đạt `mitigated`. Ở v1 điều này chặn bảo trì; từ v3, 120 giây sau thời điểm dòng được xác nhận giảm mà residual chưa phục hồi, worker ghi `load_reduction_ineffective` (reason `residual_not_recovered`) và operator được bảo trì từ `mitigating` (CONTRACT §3).

## Trip và khởi động lại (chỉ mô phỏng)

- **Trip** khi nhiệt connector *thật* > **100 °C**. Giá trị này chọn trước, chỉ dùng trong mô phỏng: cao hơn trạng thái ổn định khỏe nóng nhất (≈ 93,7 °C) khoảng 6 °C để lượt khỏe không trip theo cấu trúc, và gần mức cắt bảo vệ nhiệt thường gặp ở đầu nối DC (khoảng 90–100 °C). Không phải ngưỡng an toàn thiết bị thật.
- Khi trip: dòng = 0 (phiên bị gián đoạn), nhu cầu xe vẫn tiếp tục theo đường tải, giới hạn dòng giữ nguyên. Từ v2, cửa sổ có mẫu không sạc không xác nhận giảm tải: trip chỉ được tính là gián đoạn.
- **Khởi động lại** tự động khi đã qua ít nhất **300 s** kể từ trip *và* nhiệt thật ≤ **60 °C**; chạy lại với giới hạn đang áp dụng. Nếu lỗi còn, có thể trip lại; mọi lần trip đều được đếm.
- Phút gián đoạn = thời gian đang trip mà nhu cầu xe > 0.

## Operator và bảo trì (giống hệt cho A và C)

Operator kịch bản hóa, phản ứng sau **30 s** mỗi bước, tính từ lúc incident vào trạng thái tương ứng:

| Trạng thái | Lệnh | Thời điểm |
|---|---|---|
| `open` | `acknowledge` | vào `open` + 30 s |
| `acknowledged` | `reduce_load` | vào `acknowledged` + 30 s |
| `mitigated`, chưa bảo trì | `maintenance` | max(vào `mitigated` + 30 s, mở incident (chu kỳ hiện tại) + **600 s**) |
| `mitigated`, đã bảo trì | `verify_recovery` | bảo trì áp dụng + 30 s |
| `mitigating`, đã ghi `load_reduction_ineffective` reason `residual_not_recovered`, chưa bảo trì | `maintenance` | max(event đó + 30 s, mở incident (chu kỳ hiện tại) + 600 s) |
| `mitigating` khác, `verifying` | không lệnh | chờ telemetry |

Worker xác thực lệnh ngay khi nhận; simulator áp dụng lệnh ở lượt poll tiếp theo (**5 s**, nhỏ hơn timeout 30 s nên không lệnh nào timeout). Mỗi loại lệnh gửi một lần cho mỗi chu kỳ xử lý của incident; lệnh bị từ chối (ví dụ `no_valid_reference_window`) được gửi lại sau 30 s và đếm là lệnh bị từ chối. Lịch bảo trì là thao tác operator mô phỏng, không thêm tự động bảo trì vào sản phẩm. B không có operator.

## Ghép incident với lỗi

- Seed lỗi: incident đầu tiên mở lúc (cuối cửa sổ) ≥ thời điểm lỗi bắt đầu được ghép với lỗi. Incident mở trước thời điểm đó là cảnh báo giả, báo riêng.
- Seed khỏe: mọi incident là cảnh báo giả.
- **Phát hiện trước trip**: incident ghép lỗi mở trước lần trip đầu tiên của **B cùng seed**; nếu B không trip, phát hiện bất kỳ lúc nào trong lượt được tính. Mẫu số là toàn bộ 20 seed lỗi (gồm cả seed B không trip). Báo thêm số phát hiện trước trip trong nhóm seed B có trip.
- Trước alert đầu tiên, telemetry của A/C trùng B, nên phát hiện của C trùng với detector residual trên telemetry B (có test).

## Chỉ số

- **Latency phát hiện**: mở incident − bắt đầu lỗi (đồng hồ mô phỏng).
- **Lead time**: trip đầu tiên của B − mở incident; chỉ khi B thực sự trip và phát hiện trước trip. Khi B không trip, lead time **không xác định** (`null`, không điền 0); seed vẫn nằm trong mẫu số recall, và số seed đủ điều kiện lead time (`n`) được báo riêng.
- **Thời gian hành động có tác dụng**: mở incident → cuối cửa sổ đầu tiên xác nhận giảm dòng (`load_reduction_verified_at`). Báo thêm → `mitigated`, → bảo trì, → `resolved`. B: không áp dụng.
- **False alerts/connector-hour**: số incident trên seed khỏe ÷ tổng giờ connector khỏe mô phỏng (20 × 0,5 h = 10 h). Báo thêm số lượt khỏe có ít nhất một incident (mục tiêu ≤ 1/20) và phân theo kịch bản.
- **Trip, phút gián đoạn, kWh phục vụ**: kWh = Σ V × I × 5 s trên cùng 1800 s, tính cả giai đoạn gián đoạn. **Chênh lệch kWh có dấu** so với B cùng seed; phần năng lượng chênh trong các bước đang giảm tải (giới hạn < 250 A) báo riêng. Chênh lệch âm nghĩa là phục vụ ít hơn B; không gán mọi chênh lệch là lợi ích.
- **Command trùng**: lệnh cùng loại, cùng incident, cùng chu kỳ được chấp nhận lần thứ hai trở lên. Mục tiêu 0. Giá trị 0 đúng **theo cấu trúc** của operator kịch bản (mỗi loại lệnh một lần mỗi chu kỳ); đây không phải bằng chứng sản phẩm chống trùng (idempotency được test riêng trong `tests/test_lifecycle.py`).
- **Chất lượng detector riêng**: chạy cả detector ngưỡng cố định và residual trên cùng cửa sổ telemetry chưa can thiệp của B; phát hiện là **cạnh lên đầu tiên** của alert có cuối cửa sổ ≥ onset (alert đang bật từ trước onset không được tính là phát hiện lỗi): phát hiện trước trip, latency, lead time, cảnh báo trước onset, số lượt khỏe có alert, số đợt alert (cạnh lên)/connector-hour, theo kịch bản. Không dùng các đường nhiệt đã khác nhau sau can thiệp để so detector.

## Khóa ngưỡng trên tập tuning

- **C**: ngưỡng alert 8 °C và phục hồi 3 °C là hằng số sản phẩm (CONTRACT §3), không tìm kiếm lại. `tune` kiểm tra chúng trên seed tuning và ghi số lượt khỏe có alert, cùng cờ `c_within_budget`.
- **A**: ngân sách cảnh báo giả giống C: tối đa **1 lượt khỏe có incident trên 20 lượt khỏe tuning** (mục tiêu PLAN). Ngưỡng A là giá trị **thấp nhất** trong lưới 30,0–100,0 °C bước 0,5 °C sao cho số lượt khỏe tuning có cửa sổ hợp lệ với trung bình nhiệt > ngưỡng không vượt ngân sách. Chọn giá trị thấp nhất cho A độ nhạy cao nhất có thể dưới cùng ngân sách, tránh chọn ngưỡng yếu tùy ý. Vì cảnh báo giả đầu tiên luôn xảy ra trên telemetry chưa can thiệp, tune trên telemetry B tương đương với lượt A đầy đủ.
- File khóa ghi ngưỡng, quy tắc, seed tuning/đánh giá, fingerprint protocol (gồm hằng số sản phẩm, SHA-256 file đường tải và SHA-256 đã chuẩn hóa xuống dòng của `runner.py`, `rules.py`, `summary.py`, `model.py`, `lifecycle.py`, `domain.py`, `evaluation/__main__.py`) và thời điểm khóa. File khóa được commit trước khi chạy tập đánh giá.

## Mục tiêu nội bộ (PLAN) và cách báo

C phát hiện ≥ 18/20 lỗi trước trip của B; ≤ 1 incident giả/20 lượt khỏe; 0 command trùng. Báo kết quả thực kể cả không đạt; không đổi seed hay ngưỡng sau khi xem kết quả đánh giá. **Lưu ý về tiền đăng ký:** các thay đổi quy tắc sản phẩm của v2 và v3 (quyết định của nhóm ngày 21/10: bảo trì từ `mitigating`, trip không xác nhận giảm tải, chỉ `residual_not_recovered` mở khóa) được đưa ra **sau khi đã xem kết quả seed đánh giá của v1 (và của v2 đối với v3)**. Vì vậy v3/v3.1 **không phải tiền đăng ký sạch**: protocol đã đổi sau khi thấy kết quả, dù seed tuning/đánh giá và ngưỡng (A theo quy tắc `tune`, C là hằng số 8/3 °C) không đổi. Đọc kết quả v3.1 như đánh giá có thông tin từ các lượt trước, không như kiểm định độc lập. Các tham số kịch bản được chọn trước khi chạy `tune`; trong lúc phát triển chỉ chạy thử vài seed tuning để kiểm tra code, không chạy seed đánh giá trước khi khóa.

## Giới hạn

- Một connector, mô hình nhiệt bậc 1 với τ nén ~37 lần; baseline detector và simulator cùng dạng mô hình, chỉ lệch tham số ±5 % (k) / ±10 % (τ) và noise — kết quả residual có lợi thế cấu trúc so với dữ liệu thật.
- Phân bố lỗi, ambient, trip và operator là giả định, không hiệu chỉnh từ dữ liệu lỗi thật (dataset không có nhãn lỗi charger).
- 20 + 20 seed: khoảng tin cậy rộng; không tuyên bố ý nghĩa thống kê.
- Không đo latency Datadog; đo riêng bằng luồng thật (MINIMAL_LOOP.md).
