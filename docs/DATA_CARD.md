# Data card — ChargeOps

Trạng thái ngày 20/10/2026: **đã tải và kiểm định** `processed_data_longer_than_30.xlsx` (SHA-256 khớp nguồn). Kết luận: dùng được làm **mẫu đường tải** sau khi làm sạch; không dùng làm ground truth lỗi charger. Kết quả ở mục "Kết quả kiểm định 20/10/2026"; manifest `data/manifests/autosun-v3.json`; tái lập bằng `scripts/audit_autosun.py`. Simulator replay đường tải của 50 phiên từ `chargeops/profiles/autosun_v3.csv` (xuất bởi script kiểm định, SHA-256 trong manifest); mô hình nhiệt dùng k từ dữ liệu và τ nén — xem MINIMAL_LOOP.md, mục "Vòng xử lý G2".

## Nguồn ứng viên

[Autosun–Hong Kong Polytechnic, phiên bản 3](https://data.mendeley.com/datasets/c7gg94tmvz/3), DOI `10.17632/c7gg94tmvz.3`, công bố 08/10/2024; giấy phép được trang nguồn ghi là **CC BY 4.0**. Tác giả: haosen yang, Jinpeng Tian, Weijie Mai, Chi-yung Chung. Khi sử dụng phải ghi công, dẫn nguồn/giấy phép và chỉ rõ biến đổi.

Trang nguồn mô tả hai file XLSX đã tiền xử lý: `processed_data.xlsx` và `processed_data_longer_than_30.xlsx`. Dữ liệu lấy mẫu mỗi phút, có ID mẫu/phiên, thời gian, điện áp, dòng, công suất, SoC và hai nhiệt độ súng sạc. `label` là nhãn lỗi **pin** (1: lỗi, 0: bình thường), không phải nhãn hỏng charger. Mô tả không cung cấp nhiệt môi trường, lịch sử bảo trì hoặc trạng thái mạng đủ cho bài toán này.

Schema cần kiểm chứng trực tiếp trong file: `id`, `transaction_id`, `begin_time`, `end_time`, `chargingv`, `charginga`, `out_power`, `charging_gun_temperature1`, `charging_gun_temperature2`, `label`. Nguồn trên là căn cứ ban đầu; tên cột, kiểu, đơn vị và chất lượng thực tế vẫn chưa được xác nhận.

## Mục đích và giới hạn sử dụng

- Khảo sát tối đa 50 phiên phù hợp để lấy mẫu đường tải và minh họa điện–nhiệt; không lấy nhãn pin làm ground truth charger.
- `label=0` không tự chứng minh connector khỏe. Baseline phải qua kiểm tra chất lượng và được mô tả là baseline demo.
- Không tuyên bố dự báo tuổi thọ hay hiệu quả trên trạm sạc thật từ kết quả mô phỏng.
- Replay đường tải và mô hình nhiệt tương tác là hai nguồn khác nhau: nhiệt sau lệnh giảm tải do simulator tạo, không phải phép đo từ dataset.

## Kiểm định thực hiện từ 17/10

1. Tải vào vùng dữ liệu local bị Git ignore; lưu tên file, dung lượng, thời gian tải, URL, phiên bản và SHA-256.
2. Kiểm tra sheet/cột, kiểu dữ liệu, số phiên, missing, trùng ID và timestamp không tăng trong phiên.
3. Xác minh đơn vị từ tài liệu/file; không suy đơn vị chỉ từ tên cột. Kiểm tra điện áp/dòng/công suất nhất quán và phạm vi giá trị.
4. Ghi rõ timezone nguồn; nếu chưa biết thì giữ timestamp gốc, không tự gán UTC. Simulator dùng thời gian run UTC độc lập.
5. Chọn phiên đủ dài và liên tục; ghi quy tắc loại dữ liệu, số hàng trước/sau và danh sách ID được chọn.
6. Xuất báo cáo kiểm định cùng manifest và attribution. Không commit file thô lớn vào repo.

Điều tra vấn đề tải/chất lượng đến khi có đủ bằng chứng quyết định sử dụng nguồn, không đặt giới hạn một ngày cố định. Nếu không tải được hoặc dữ liệu không đạt kiểm định, dùng **100% dữ liệu tổng hợp**, ghi lý do và kết quả kiểm định. Không tiếp tục gọi dữ liệu đó là dữ liệu thực.

## Kết quả kiểm định 20/10/2026

Tái lập: `uv run --with pandas==2.2.3 --with openpyxl==3.1.5 python -I scripts/audit_autosun.py` (tải nếu chưa có, kiểm SHA-256, chuyển XLSX → CSV, phân tích, chọn phiên, ghi manifest). File thô ở `data/raw/autosun-v3/`, CSV ở `data/processed/` — cả hai bị Git ignore.

**Nguồn và file:** API công khai Mendeley (version 3) liệt kê `processed_data_longer_than_30.xlsx` (103.137.520 byte), `processed_data.xlsx` (116.643.732 byte) và `Readme.txt`. Đã tải file thứ nhất và Readme ngày 20/10/2026 04:43 UTC; SHA-256 khớp giá trị nguồn công bố (`8d7c676b…`, `0a535074…`). Chưa tải `processed_data.xlsx` (gồm cả phiên ngắn, không cần cho mục đích này).

**Cấu trúc:** 8 sheet (`Sheet1`–`Sheet8`, tương ứng 8 data owner), cùng 16 cột như Readme: `id`, `transaction_id`, `begin_time`, `end_time`, `total_charging_kwh`, `total_charging_min`, `current_soc`, `current_energy_meter_value`, `chargingv`, `charginga`, `out_power`, `charging_gun_temperature1`, `charging_gun_temperature2`, `types`, `class_judge`, `label`. Không có giá trị thiếu.

| Kiểm tra | Kết quả |
|---|---|
| Số dòng | 1.382.319 trong file; Readme công bố 1.547.432 |
| Trùng giữa sheet | 240.393 dòng là bản sao hoàn toàn (2.537 phiên ở cả Sheet1 và Sheet7, 194 ở Sheet7 và Sheet8). Sau loại trùng: **1.141.926 dòng, 18.531 phiên** (Readme: 21.175 phiên) |
| Định danh | `id` **không** duy nhất: 2.857 `id` dùng lại cho dòng khác hẳn. Khóa dùng: `(transaction_id, end_time)` — không trùng |
| Thời gian | Tăng dần trong phiên; 98,95 % bước đúng 60 s (còn lại ±1 s); 869 phiên có khoảng trống >61 s. `begin_time` cố định trong phiên; kWh và phút tích lũy không giảm. Dữ liệu 2020-09-09 → 2024-03-13 (Readme: 10/2020–10/2023) |
| Múi giờ | Nguồn không ghi. Giữ nguyên timestamp; giờ bắt đầu phiên tập trung 0h và 12h, phù hợp giờ địa phương (UTC+8) hơn UTC nhưng chưa đủ kết luận |
| Đơn vị | Readme không ghi. Kiểm chứng `out_power = chargingv × charginga / 1000` sai lệch ≤0,11 % ở 99 % dòng → V, A, kW. Nhiệt độ súng giả định °C (trung vị 33/41) |
| Phạm vi | V 267–561 (0,1–99,9 %), A ≤250, P ≤166 kW, không âm; 4.785 dòng dòng điện bằng 0. Nhiệt súng có giá trị phi vật lý −49 °C và 199 °C (lỗi cảm biến) |
| Nhãn | 747 phiên `label = 1` (lỗi pin, gồm cả trường hợp trạm tự ngắt vì quá nhiệt); nhãn nhất quán trong phiên |
| Độ dài | Trung vị 56 phút; vẫn có phiên <30 dòng dù tên file là "longer than 30" |

**Quy tắc chọn phiên** (sau loại trùng):

| Quy tắc | Phiên còn lại |
|---|---|
| `label == 0` | 17.784 |
| Không có khoảng trống >61 s | 17.271 |
| ≥30 dòng | 17.070 |
| 0 < dòng ≤250 A ở mọi dòng | 12.756 |
| 200 ≤ điện áp ≤750 V | 12.756 |
| Nhiệt súng trong 0–120 °C | **12.740 phiên, 780.927 dòng** |

50 phiên chọn ngẫu nhiên với seed `20261020`; danh sách trong manifest (`selection.selected_transaction_ids`). Dòng trung bình của phiên đủ điều kiện: trung vị 94 A (5–95 %: 37–163 A). Loại pin: LFP 7.689, NMC 4.854, khác 197.

**Ước lượng nhiệt (chỉ để hiệu chỉnh bậc độ lớn):** hồi quy tĩnh nhiệt độ theo I² cho hệ số âm vì quán tính nhiệt (dòng cao đầu phiên khi súng nguội, giảm dần khi súng đã nóng). Mô hình trễ bậc 1 theo bước 60 s, hiệu ứng cố định từng phiên, trên 12.740 phiên:

| Cảm biến | k (°C/A²) | τ (phút) | R² (ΔT) | Tăng ổn định ở 100 A / 200 A |
|---|---|---|---|---|
| Súng 1 | 0,00112 | 38,7 | 0,17 | 11,2 / 44,9 °C |
| Súng 2 | 0,00146 | 35,1 | 0,22 | 14,6 / 58,2 °C |

Độ phân giải 1 °C và không có nhiệt môi trường nên R² thấp; đây không phải hiệu chỉnh chính xác. So với simulator hiện tại (k 0,004; τ 45 s): k cao khoảng 3 lần, τ nhanh khoảng 50 lần.

**Quyết định (20/10/2026):**

- Dùng dataset làm mẫu đường tải: simulator replay dòng điện của 50 phiên đã chọn, nội suy 1 phút → 5 giây (ghi rõ là nội suy, không phải tần suất gốc). Provenance `hybrid`: tải `public`, nhiệt và lỗi `synthetic`, residual `derived`.
- Mô hình nhiệt: dùng k theo bậc độ lớn từ dữ liệu (0,0013 °C/A²), giữ τ nén (60 s) để một lượt demo khoảng 15 phút; ghi rõ là **thời gian nhiệt được nén khoảng 37 lần** so với súng sạc thật. Lỗi được định nghĩa bằng °C nóng thêm ở tải lúc lỗi bắt đầu (mặc định 10,5 °C) để mức nghiêm trọng không phụ thuộc dòng của từng phiên; ngưỡng 8/3 °C giữ nguyên. Phân bố mức nghiêm trọng và ngưỡng được khóa trên tập tuning (G4).
- Dòng trong dataset là dòng thực đo được; simulator coi đó là nhu cầu xe (`requested_current_a`). Giới hạn gốc 250 A (dòng lớn nhất quan sát được) là giả định synthetic.
- Nhãn pin, `types`, `class_judge` không dùng cho detector.

**Attribution:** haosen yang, Jinpeng Tian, Weijie Mai, Chi-yung Chung. *A dataset of EV battery charging from Shenzhen Auto Electric Power Plant Co., Ltd (Autosun) and Hong Kong Polytechnic University*, Mendeley Data, V3, 2024. DOI [10.17632/c7gg94tmvz.3](https://doi.org/10.17632/c7gg94tmvz.3). CC BY 4.0. Biến đổi của ChargeOps: loại trùng giữa sheet, lọc phiên theo quy tắc trên, chỉ dùng dòng/điện áp, nội suy 1 phút → 5 giây.

## Provenance trong sản phẩm

Mỗi run lưu nguồn, phiên bản/seed, checksum dữ liệu đầu vào (nếu có), phiên bản simulator và cấu hình. Phân biệt ba loại: `public` (quan sát công khai), `synthetic` (mô phỏng) và `derived` (biến đổi/tính toán). Với run kết hợp, mô tả nguồn theo trường, ví dụ tải từ mẫu công khai nhưng nhiệt là mô phỏng.

ID station/charger/connector và ambient temperature do mô phỏng phải được gắn nguồn synthetic. Khi nội suy mẫu một phút thành bước năm giây, ghi phương pháp và không gọi là tần suất quan sát gốc. Giao diện, slide và báo cáo phải hiển thị nguồn dữ liệu.

## Ground truth và đánh giá

Ground truth gồm thời điểm fault injection, seed và cường độ lỗi, lưu riêng khỏi input detector. Tách seed tuning và seed đánh giá; chạy 20 lượt lỗi và 20 lượt khỏe đã chốt trong plan. Lưu cả kết quả không đạt, không lựa chọn lại seed để cải thiện điểm. So sánh can thiệp và không can thiệp trên cùng seed; chỉ tính lead time khi lượt đối chứng thực sự trip. Phân biệt số đo mô phỏng và bằng chứng tích hợp Datadog thật.

### Đối chứng để chứng minh giá trị

Mỗi kịch bản trong 20 seed lỗi và 20 seed khỏe chạy đủ ba phương án (120 lượt mô phỏng, không phải 120 lượt Datadog):

| Phương án | Phát hiện | Can thiệp |
|---|---|---|
| A — cách đơn giản | Nhiệt tuyệt đối trung bình 60 giây vượt ngưỡng cố định | Cùng workflow giảm tải/bảo trì/retest như C |
| B — không can thiệp | Residual theo tải như ChargeOps | Chỉ ghi cảnh báo; không giảm tải/bảo trì |
| C — ChargeOps | Residual theo tải | Workflow đầy đủ theo CONTRACT.md |

Ngưỡng nhiệt cố định của A được chọn trên tập tuning theo cùng mục tiêu hạn chế cảnh báo giả với C; ghi giá trị/lý do và khóa trước evaluation. Không chọn một ngưỡng yếu tùy ý để làm đẹp kết quả. Hai detector dùng cùng cửa sổ 60 giây và quality gate; ngưỡng A là cấu hình mô phỏng, không phải ngưỡng an toàn thiết bị thực.

Giữ cùng seed, nhu cầu tải, ambient, noise đầu vào, fault, điều kiện đầu, thời lượng và quy tắc trip/khởi động lại. A/C dùng cùng độ trễ operator, chính sách giảm tải và lịch bảo trì tính tương đối từ alert; ghi rõ các giả định này. Lịch bảo trì ở đây mô phỏng thao tác operator, không bổ sung tự động bảo trì vào sản phẩm. Nhiệt/dòng sau hành động được simulator tính lại; không replay nhiệt cố định. KWh phục vụ là tích phân công suất đo trên cùng thời lượng, tính cả giai đoạn gián đoạn.

Tách hai loại kết quả:

- **Chất lượng detector:** chạy cả detector ngưỡng cố định và residual trên cùng telemetry chưa can thiệp của B; so sánh cảnh báo trước trip, latency và false alerts/connector-hour, gồm healthy/tăng tải/giảm nhu cầu. Không dùng hai đường nhiệt đã khác nhau sau can thiệp để kết luận detector nào tốt hơn.
- **Giá trị vận hành:** so sánh các lượt A/B/C về thời gian hành động có tác dụng (từ alert đến cửa sổ xác nhận giảm dòng), số trip, phút gián đoạn, kWh phục vụ và chênh lệch kWh. B không có hành động nên thời gian tác dụng là không áp dụng. Ghi riêng phần năng lượng bị giảm trong giai đoạn giảm tải so với B; chênh lệch có thể âm nếu B đã trip, không gán mọi chênh lệch là lợi ích. Lead time dùng thời điểm trip của B, chỉ khi B thực sự trip.

Evaluation nhanh dùng đồng hồ mô phỏng và cùng giả định độ trễ cho A/C; không suy latency Datadog từ đó. Đo latency Datadog riêng bằng luồng thật. Lưu version/cấu hình, seed, kết quả từng phương án và cả lượt không đạt; không mặc định ChargeOps tốt hơn đối chứng, không ngoại suy hiệu quả lên trạm thật.
