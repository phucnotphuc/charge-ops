# Data card — ChargeOps

Trạng thái ngày 14/10/2026: đã kiểm tra mô tả công khai; **chưa tải, mở hoặc kiểm định các file dữ liệu**. Chưa tuyên bố dataset phù hợp huấn luyện hay đã được dùng trong demo.

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
