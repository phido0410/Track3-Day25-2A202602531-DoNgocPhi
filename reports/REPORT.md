# Lab 25B — Report

**Họ tên / nhóm:** Đỗ Ngọc Phi (MSV: 2A202602531)  **Commit:** a3f9ce7078c14d183ed1e95d311a2df59bc86855  **Điểm tự động (`make grade`):** 84.0 / 88 (88/88 với dữ liệu SLO ẩn)

> Mọi nhận định phải có số. Dán số từ `reports/gameday/*.json`, `reports/workflows.json`, `reports/slo.json`.

## 1. Kết quả Game Day (naive vs của bạn)

| Băng | Client | availability | p95 (s) | amplification | retry_ratio | cost/turn | recovery (s) | PASS? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | 0.7194 | 7.157 | 7.738 | 6.738 | 2.163 | 4.0 | FAIL |
| G1 | yours | 1.0000 | 4.776 | 1.062 | 0.002 | 2.101 | 0.0 | PASS |
| G2 | naive | 0.4560 | 15.034 | 9.904 | 8.904 | 4.627 | 155.0 | FAIL |
| G2 | yours | 1.0000 | 10.920 | 1.041 | 0.007 | 2.119 | 0.0 | PASS |
| G3 | naive | 1.0000 | 4.768 | 1.164 | 0.164 | 2.328 | 0.0 | FAIL |
| G3 | yours | 1.0000 | 4.660 | 1.060 | 0.034 | 2.145 | 0.0 | PASS |
| G4 | naive | 1.0000 | 13.764 | 1.004 | 0.004 | 2.008 | 0.0 | FAIL |
| G4 | yours | 1.0000 | 5.562 | 1.015 | 0.002 | 2.072 | 0.0 | PASS |
| G5 | naive | 0.3699 | 5.359 | 17.030 | 16.030 | 2.124 | - | FAIL |
| G5 | yours | 0.8179 | 5.477 | 1.003 | 0.002 | 2.504 | - | PASS |
| G6 | naive | 0.5170 | 4.836 | 12.344 | 11.344 | 2.009 | 0.0 | FAIL |
| G6 | yours | 1.0000 | 5.961 | 1.055 | 0.019 | 2.189 | 0.0 | PASS |

## 2. Cấu hình client và lý do

| Tham số | Giá trị | Vì sao (dựa trên băng nào, số liệu nào) |
|---|---:|---|
| attempt_timeout_s | 8.0 | Dựa trên G6 (alpha-eu treo vô tận) và G4 (alpha latency +5s lên ~6.2s). 8.0s đủ lớn để cuộc gọi hợp lệ kết thúc, nhưng đủ nhỏ so với user deadline 30s để kịp failover sang provider khác thay vì treo 30s rồi drop (tránh survivorship bias). |
| max_attempts_per_provider | 2 | Tối đa 1 lần retry trên cùng 1 provider (tổng 2 attempts). Tránh lãng phí tài nguyên và ngăn retry storm khi provider đang nghẽn (G2/G3), nếu lần 2 vẫn lỗi thì failover ngay. |
| backoff_base_s / cap_s | 0.25 / 4.0 | Base 0.25s giúp phản hồi nhanh với lỗi transient nhỏ; cap 4.0s cùng full jitter phân tán lưu lượng, ngăn chặn triệt để hiện tượng thundering herd khi provider hồi phục (G1, G2). |
| retry_budget_ratio | 0.1 | G3 yêu cầu `retry_ratio <= 0.10`. Token bucket nạp 10% token trên tổng request, đảm bảo dù alpha lỗi 500 tới 30% thì số retry cùng provider chỉ chiếm 0.034 (3.4%), không thể gây quá tải. |
| breaker.window_size / min_calls | 20 / 10 | Cửa sổ trượt 20 kết quả gần nhất, cần tối thiểu 10 kết quả mới xem xét ngắt mạch. Tránh ngắt mạch nhầm (flapping) khi lưu lượng thấp hoặc chỉ mới có 1-2 lỗi ngẫu nhiên. |
| breaker.failure_rate_threshold | 0.5 | Khi tỷ lệ lỗi >= 50% trong cửa sổ trượt (G2, G3, G6), provider đang suy sụp rõ rệt -> trip OPEN ngay để chuyển traffic sang alpha-eu/beta, bảo vệ provider chính. |
| breaker.slow_call_s | 5.0 | Dựa trên G4: alpha bình thường chạy median 1.2s, khi mạng chậm thêm 5s (thành ~6.2s), vượt ngưỡng 5.0s. Breaker nhận diện là slow call và trip OPEN để chuyển sang alpha-eu (1.5s), giữ latency p95 = 5.562s (<= 10s). |
| breaker.open_duration_s | 10.0 | Giữ OPEN trong 10s để provider có thời gian giải tỏa hàng đợi trước khi thử thăm dò lại ở HALF-OPEN. |
| breaker.half_open_max_probes | 2 | Chỉ cho phép tối đa 2 request thăm dò đồng thời tại HALF-OPEN. Tránh việc hàng chục request ùa vào cùng lúc làm sập lại provider vừa mới ngóc đầu lên (G2). |

Sơ đồ quyết định của `on_error` (ASCII hoặc bảng: loại lỗi → ghi breaker? → retry? → failover? → cooldown?):

```
                        +----------------------------+
                        |  Attempt nhận ProviderError|
                        +--------------+-------------+
                                       |
                               [ classify(err) ]
                                       |
      +-------------------+------------+------------+--------------------+
      |                   |                         |                    |
[CLIENT_ERROR]    [QUOTA_EXHAUSTED]          [RATE_LIMITED]      [OVERLOADED / SERVER_ERROR / TIMEOUT]
      |                   |                         |                    |
Breaker: release    Breaker: release          Breaker: release     Breaker: on_failure
Cooldown: không     Cooldown: 300s            Cooldown: retry_after Cooldown: không
Retry: KHÔNG        Retry: KHÔNG              Retry: KHÔNG               |
Failover: KHÔNG     Failover: NGAY LẬP TỨC    Failover: NGAY LẬP TỨC     +--> tries < max_attempts AND budget cho phép
      |                   |                         |                         AND còn đủ thời gian trước deadline?
      v                   v                         v                         |
GiveUp("client_error")  _route(next provider)     _route(next provider)      +-- YES --> Send(SAME provider, delay=jitter_backoff)
                                                                             |
                                                                             +-- NO  --> tries = 0, idx += 1, _route(next provider)
```

## 3. Ba thí nghiệm (mỗi cái 3–5 câu + số)

1. **Retry budget là lớp phòng thủ cuối.**
   - Thí nghiệm vô hiệu hóa breaker (`breaker.min_calls: 1000000000`, `max_attempts_per_provider: 5`):
     + Trên G2 (overload): Khi có retry budget, amplification = 1.380, retry_ratio = 0.058, recovery_s = 0.0s, availability = 99.87%. Khi tắt budget, amplification tăng vọt lên 1.675, retry_ratio nhảy lên 0.495 (gần 50% traffic là retry!), recovery_s kéo dài 25.0s, availability giảm còn 96.66%.
     + Trên G3 (brownout 30% lỗi 500): Khi có budget, amplification = 1.136, retry_ratio = 0.068 (đạt chuẩn <= 0.10). Khi tắt budget, retry_ratio tăng lên 0.159 (vi phạm chuẩn).
   - Nhận định: Khi breaker bị cấu hình sai hoặc gặp sự cố suy thoái chưa đủ kích hoạt ngắt mạch, Retry Budget là lớp phòng thủ chặn đứng bão retry, khống chế lượng retry dưới 10%. Cần cả hai vì Breaker ngắt mạch dựa trên sức khỏe riêng của từng provider, còn Budget là van an toàn vĩ mô kiểm soát tổng tài nguyên tiêu hao của toàn hệ thống.

2. **`attempt_timeout_s: 30`.**
   - Chạy G6 với timeout = 8s đạt availability = 1.0 (100%), p95 = 5.961s, give_up_reasons = {}. Khi tăng timeout lên 30s, availability tụt xuống 0.9488 (94.88%, FAIL check >= 95%), p95 = 5.315s, xuất hiện give_up_reasons = `{'deadline': 109, 'no_provider_available': 13}`.
   - Hiện tượng survivorship bias: p95 nhìn bề ngoài giảm từ 5.961s xuống 5.315s vì p95 chỉ tính trên *các cuộc gọi thành công*. 109 cuộc gọi bị treo ở `alpha-eu` suốt 30s đã cạn kiệt deadline của người dùng và thất bại, bị loại khỏi tập tính p95, tạo ra ảo tưởng độ trễ cải thiện.
   - Ngược lại, ở G4 (chỉ chậm thêm 5s, median ~6.2s), timeout 8s hay 30s gần như không đổi (p95 5.562s vs 5.608s) vì cuộc gọi vẫn hoàn thành trước 8s và breaker phát hiện `slow_call_s >= 5.0s` để ngắt mạch chuyển sang `alpha-eu`. Timeout "đúng" phải dựa vào SLA/p99 lúc bình thường kết hợp với deadline tổng chia cho số bậc failover tối đa, để bảo đảm luôn còn đủ thời gian failover sang provider khác.

3. **Bỏ lọc tool calling khỏi `ladder()`.**
   - Khi chạy G6 mà không lọc `supports_tools`, số lượt định tuyến sai `incompatible_routes = 234` và availability tụt từ 100% xuống 90.19%.
   - Góc nhìn Product Manager: "Trong đợt sự cố nhà cung cấp chính sập, hệ thống của chúng ta đã gửi nhầm 234 yêu cầu thực hiện tác vụ nghiệp vụ (hoàn tiền, tra cứu đơn) sang model ngôn ngữ giá rẻ (gamma-mini) vốn không có năng lực gọi công cụ. Kết quả là 234 khách hàng đó bị gián đoạn giao dịch hoàn tiền hoàn toàn, phát sinh khiếu nại gay gắt và làm độ sẵn sàng của tính năng CSKH tụt mất gần 10%, gây thiệt hại trực tiếp đến uy tín dịch vụ."

## 4. Workflow hoàn tiền

| Executor | completion | crashes | duplicate refunds | duplicate emails | lost refunds | overpaid | wasted LLM / crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 1.0 | 81 | 128 | 81 | 0 | 1496700 | 1.123 |
| yours | 1.0 | 66 | 0 | 0 | 0 | 0 | 0.303 |

- **Vì sao chỉ idempotency key là chưa đủ?** Idempotency key chỉ bảo vệ phía external service (ngăn trừ tiền 2 lần khi gọi lại cùng 1 key). Nếu không có checkpoint trong DurableStore, mỗi khi worker crash, worker mới sẽ phải chạy lại từ đầu tất cả các bước trước đó (classify, lookup...), gây lãng phí chi phí gọi LLM (`wasted_llm_per_crash = 1.123` ở naive so với 0.303 ở yours) và tăng độ trễ xử lý.
- **Vì sao chỉ checkpoint cũng chưa đủ?** Nếu có checkpoint mà không có idempotency key, khi worker gọi API thanh toán thành công nhưng bị mất kết nối mạng (`ServiceTimeout`), worker không nhận được phản hồi để ghi checkpoint. Worker retry hoặc worker mới chạy lại sẽ gọi thanh toán lần 2 với request mới không có key khử trùng -> gây hoàn tiền trùng lặp và thừa tiền (`overpaid_cents = 1,496,700` ở naive).
- **Điều gì xảy ra nếu ghi checkpoint TRƯỚC khi gọi payment?** Nếu ghi checkpoint trước khi gọi payment, và worker bị crash ngay trước/trong khi payment thực thi (hoặc payment gặp lỗi), checkpoint đã ghi nhận là bước hoàn tiền đã hoàn tất. Khi worker mới hồi phục, nó đọc checkpoint và bỏ qua bước hoàn tiền -> khách hàng không nhận được tiền (`lost_refunds > 0`), gây mất tiền của khách hàng. Do đó, nguyên tắc bắt buộc là: **chỉ lưu checkpoint SAU KHI external call đã thực thi thành công**.

## 5. Alerting & error budget

| Sự cố | Mong đợi | Naive alerter | Burn-rate alerter | Độ trễ phát hiện |
|---|---|---|---|---:|
| checkout_bug | page | page | page | 27 min |
| provider_outage | page | page | page | 4 min |
| deploy_blip | none | page (FALSE ALARM) | none (không page) | - |
| slow_leak | ticket | none (BỎ SÓT) | ticket | 1337 min |
| degraded_evening | page | page | page | 291 min |
| night_spike | none | page (FALSE ALARM) | none (không page) | - |
| small_bump | none | page (FALSE ALARM) | none (không page) | - |

- **Error budget 30 ngày:**
  + Tổng số requests: 25,901,755
  + Số lỗi cho phép (`allowed_bad` ở 99.5%): 129,508.8
  + Số lỗi thực tế (`bad`): 55,618
  + Tính sẵn sàng đạt được: 99.7853% (vượt mục tiêu SLO 99.5%)
  + Đã tiêu: **42.95%**, còn lại: **57.05%**.
  + Quyết định: **Không đóng băng release** (`freeze_releases: false`).
- **Vì sao `small_bump` (20 phút ở 12% lỗi) không nên đánh thức ai lúc 11 giờ trưa?**
  + Small bump chỉ kéo dài 20 phút rồi tự chấm dứt, tổng lượng lỗi tiêu thụ chưa đầy 0.1% ngân sách lỗi của cả tháng, hệ thống vẫn an toàn tuyệt đối với > 57% error budget còn lại.
  + Nếu page đánh thức kỹ sư on-call cho những biến động ngắn tự khỏi, kỹ sư sẽ bị "alert fatigue" (chai lì cảnh báo), dẫn tới nguy cơ phớt lờ các cảnh báo sự cố nghiêm trọng thực sự.
  + Quy tắc burn-rate đa cửa sổ đòi hỏi cả cửa sổ dài 1h (xác thực sự cố đủ lớn) và cửa sổ ngắn 5m (xác thực sự cố vẫn đang diễn ra) cùng vượt ngưỡng 14.4x mới phát tín hiệu page, nhờ đó triệt tiêu hoàn toàn false page này.

## 6. Postmortem — Băng G2 với client naive (blameless)

*Dùng `make plot` (hình `reports/gameday/G2_overload_storm.png`) và CSV `reports/gameday/naive_G2_overload_storm_timeseries.csv`.*

**Tóm tắt (3 câu):**
Vào giây thứ 120 của kịch bản G2, deployment chính `alpha` bị suy giảm đột ngột 75% năng lực GPU trong 3 phút, làm tăng thời gian chờ trong hàng đợi và phát sinh lỗi 529. Phiên bản `NaiveClient` với cơ chế 3 tầng retry chồng chéo (lên tới 36 attempts), delay cố định 0.2s và không có circuit breaker đã khuếch đại tải lên tới đỉnh điểm 254 attempts/s, làm sập hoàn toàn hệ thống với tỷ lệ sẵn sàng chỉ còn 45.6%. Hệ thống rơi vào trạng thái metastable failure tự duy trì và mất thêm 155 giây sau khi hạ tầng provider đã hồi phục hoàn toàn mới có thể giải phóng hết hàng đợi tồn đọng.

**Ảnh hưởng:**
- 1,285 lượt yêu cầu của khách hàng bị thất bại (chỉ 1,077 / 2,362 turns thành công, availability = 45.6%).
- Sự cố kéo dài 360 giây (từ t=120 đến t=480), trong đó thời gian sập kéo dài sau khi provider đã hồi phục là 155 giây (t=300 đến t=455).
- Với 2,362 turns và SLO 99.5%, số lỗi cho phép của tháng là 11.8 turns. Sự cố làm hỏng 1,285 turns, tương đương tiêu tốn **10,890%** (gấp gần 109 lần) toàn bộ Error Budget của cả tháng. Chi phí vận hành bị đội lên $4,983 (cost/good turn = $4.627 so với $2.119 của client resilient).

**Timeline** (giây ảo — lấy từ CSV):

| t | Sự kiện | Bằng chứng (số) |
|---:|---|---|
| 115–120 | Trạng thái bình thường trước sự cố | attempts_alpha = 5–9/s, calls_ok = 7–11, success_ratio = 1.0 (100%). |
| 120 | alpha mất 75% năng lực GPU | Dung lượng phục vụ giảm 4 lần, hàng đợi FIFO bắt đầu tích lũy request. |
| 134 | Hàng đợi bắt đầu dồn ứ, client bắt đầu timeout | attempts_alpha tăng vọt lên 24 attempts/s, calls_ok giảm còn 2. |
| 150 | Bão retry bùng nổ toàn diện | attempts_alpha đạt 163 attempts/s, calls_ok = 0, success_ratio tụt về 0.0. |
| 180 | Đỉnh điểm quá tải và sập hoàn toàn | attempts_alpha duy trì 138–254 attempts/s, client timeout và 529 liên tục, calls_ok = 0. |
| 300 | alpha hồi phục hoàn toàn 100% GPU | attempts_alpha vẫn là 115 attempts/s, calls_ok = 0, success_ratio = 0.0. |
| 300–450 | Hệ thống vẫn sập do Metastable Failure | alpha bận xử lý hàng đợi request rác đã timeout từ trước; attempts duy trì 50–140/s, success_ratio = 0.0. |
| 455 | Hàng đợi rác dần được giải phóng | attempts_alpha giảm về 13/s, calls_ok = 4, calls_done = 5, success_ratio hồi phục lên 0.80. |
| 480 | Hệ thống hồi phục hoàn toàn | attempts_alpha ổn định ở mức 8/s, calls_ok = 11, success_ratio = 1.0 (100%). |

**Trigger vs root cause:**
- **Trigger:** Sự cố phần cứng/hạ tầng bên ngoài khiến provider `alpha` bị mất 75% số lượng GPU trong 180 giây (t=120 đến t=300).
- **Root cause:** Lỗi thiết kế kiến trúc phần mềm client — 3 tầng retry không phối hợp (App $\times$ Gateway $\times$ SDK), không có exponential backoff và random jitter (delay cố định 0.2s), không có retry budget, không có circuit breaker và không có cơ chế failover sang region khác (`alpha-eu`/`beta`).
- **Metastable failure — Vòng lặp tự duy trì:** Khi alpha bị quá tải, thời gian xử lý trong hàng đợi FIFO vượt quá timeout của client (8s). Client từ bỏ và lập tức retry thêm các request mới với delay cố định cực ngắn (0.2s). Server alpha không biết client đã bỏ cuộc nên vẫn tiếp tục tiêu tốn năng lực GPU quý giá để xử lý các request rác đó. Khi alpha hồi phục ở t=300, toàn bộ 18 slots của alpha tiếp tục bị ngập trong hàng đợi request cũ, trong khi các request mới của user tiếp tục bị chậm và timeout, tạo thành vòng lặp tự khuếch đại duy trì tải > 100 attempts/s, khiến hệ thống tê liệt thêm 155s.

**Điều gì giúp / điều gì làm tệ hơn:**
- *Làm tệ hơn:* 3 tầng retry nhân số lần thử lên 36 lần; delay cố định 0.2s dội tải liên tục; thiếu circuit breaker khiến 100% traffic tiếp tục dồn vào alpha đang chết; provider server tiếp tục xử lý request dù client đã timeout.
- *Giúp ích:* Khi thay thế bằng `ResilientClient`: Circuit breaker phát hiện lỗi/slow call và ngắt mạch sang OPEN chỉ sau vài giây, chuyển hướng 100% traffic sang `alpha-eu` và `beta`; retry budget giới hạn retry <= 10%; ở trạng thái HALF-OPEN chỉ gửi 2 probe thăm dò, giúp alpha bình phục lập tức tại t=300 với `recovery_s = 0.0s`.

**Action items** (cụ thể, có owner, đo được):

| # | Hành động | Loại (prevent / detect / mitigate) | Owner | Cách kiểm chứng |
|---|---|---|---|---|
| 1 | Triển khai ResilientClient tích hợp Sliding-Window Circuit Breaker, Full Jitter Backoff và Retry Budget (10%) thay thế NaiveClient trên toàn bộ agent. | Prevent | Core Platform Team | Chạy regression test trên băng G2 trong CI pipeline đạt amplification <= 1.2 và recovery_s <= 30s. |
| 2 | Cấu hình Fallback Ladder đa vùng (alpha -> alpha-eu -> beta) khi breaker OPEN hoặc bị rate limit. | Mitigate | SRE Team | Diễn tập Game Day định kỳ: ngắt kết nối alpha, 100% traffic tự động failover sang alpha-eu trong < 1 giây, availability >= 98%. |
| 3 | Triển khai Multi-window Burn-rate Alerting (14.4x / 1h & 5m) theo chuẩn SRE Workbook để cảnh báo on-call khi error budget bị tiêu hao nhanh. | Detect | Monitoring Team | Kiểm thử tự động trên tập dữ liệu SLI 30 ngày đạt 0 false page trên blip và page trong vòng 5 phút khi có outage. |

## 7. Một điểm yếu còn lại

- **Tình huống client vẫn xử lý kém:** Khi xảy ra sự cố suy thoái diện rộng trên toàn bộ hạ tầng đám mây (Global Infrastructure Latency Degradation) khiến cả 4 deployment (`alpha`, `alpha-eu`, `beta`, `gamma-mini`) đồng loạt phản hồi chậm (ví dụ do sự cố mạng backbone hoặc đứt cáp biển). Khi đó, fallback ladder chuyển sang provider nào cũng gặp độ trễ cao, các probe ở HALF-OPEN liên tục bị đánh dấu là `slow` (latency >= 5s) và đẩy breaker trở lại trạng thái OPEN. Hệ thống sẽ rơi vào tình trạng flapping liên tục hoặc cạn kiệt deadline 30s của người dùng.
- **Cách khắc phục:**
  1. Triển khai kỹ thuật **Hedged Requests**: Sau phân vị p95 độ trễ bình thường (ví dụ 2.5s), tự động gửi thêm một request dự phòng song song sang deployment thứ 2 mà không hủy request 1, lấy kết quả nào về trước.
  2. Bổ sung **Adaptive Concurrency Limits** (thuật toán TCP Vegas hoặc Little's Law) ở phía gateway để tự động điều chỉnh số lượng in-flight requests phù hợp với năng lực thực tế của backend, thay vì chỉ ngắt mạch nhị phân OPEN/CLOSED.

## 8. Cache (Part G)

Số từ `reports/cache/*.json` (`make cache`).

| Client | availability | faq_availability | cache_hit_ratio | stale_served | wrong_answers | outdated_answers | cost/turn | PASS? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient (không cache) | 0.8373 | 0.8424 | 0.000 | 0 | 0 | 0 | 1.386 | FAIL |
| cached (của bạn) | 0.9124 | 0.9645 | 0.652 | 157 | 0 | 3 | 0.915 | PASS |

| Tham số `cache:` | Giá trị | Vì sao (số liệu nào) |
|---|---:|---|
| similarity_threshold | 0.92 | Paraphrase của cùng 1 câu hỏi có cosine ~0.94-0.98, còn các câu khác nhau gần nghĩa có cosine ~0.80-0.86. Ngưỡng 0.92 loại bỏ 100% `wrong_answers` (giảm từ 175 lỗi ở ngưỡng 0.80 về 0), trong khi vẫn duy trì hit ratio cao (65.2%). |
| ttl_s | 60 | Tại t=480, bảng phí ship đổi không báo trước (không có webhook). TTL 60s giới hạn số câu trả lời cũ chỉ còn 3 (so với 95 câu ở TTL 300s), thỏa mãn xuất sắc tiêu chí `outdated_answers <= 15`. |
| max_stale_s | 300 | Đủ lớn để bao phủ toàn bộ thời gian 120s mọi provider sập (t=240 đến 360). Cho phép phục vụ 157 câu trả lời stale, nâng `faq_availability` từ 84.24% lên 96.45%. |

Thí nghiệm (mỗi cái 2–4 câu + số):

1. **Ngưỡng.**
   - Chạy C1 với `similarity_threshold` = 0.80: cache hit ratio = 0.659, wrong_answers = 175, cost = 0.978, wrong_answers_by_reason = `{'other_question': 175}`.
   - Chạy với ngưỡng 0.92: hit ratio = 0.652, wrong_answers = 0, cost = 0.915, reasons = `{}`.
   - Chạy với ngưỡng 0.97: hit ratio = 0.579, wrong_answers = 0, cost = 0.965, reasons = `{}`.
   - Ở ngưỡng 0.80, các câu hỏi gần nghĩa nhưng khác hẳn ý định bị trả nhầm cho nhau: `refund_policy` nhầm với `refund_time`, `shipping_fee_city` nhầm với `shipping_fee_province`, `exchange_size` nhầm với `exchange_color`, `payment_methods` nhầm với `cod`. Ngưỡng 0.92 là điểm tối ưu tuyệt đối (0 sai sót, hit ratio 65.2%).

2. **TTL.**
   - Chạy C1 với các mức TTL:
     + `ttl_s = 30`: outdated_answers = 2, hit ratio = 0.592.
     + `ttl_s = 60`: outdated_answers = 3, hit ratio = 0.652.
     + `ttl_s = 300`: outdated_answers = 95 (vi phạm tiêu chí <= 15!), hit ratio = 0.731.
   - Giải thích: Khi đối tác thay đổi bảng giá vận chuyển mà không gửi webhook thông báo, hệ thống không nhận được sự kiện để gọi `invalidate(source)`. Do đó, cache entry chỉ có thể bị hủy khi thời gian tồn tại vượt quá `ttl_s`. TTL là cơ chế duy nhất giới hạn số câu trả lời lỗi thời khi không có tín hiệu invalidation chủ động.

3. **Stale-if-error.**
   - Chạy với `max_stale_s: 0`: `faq_availability` tụt từ **96.45%** xuống **87.33%**, số lượt `stale_served = 0`.
   - Trong 2 phút mọi provider ngừng hoạt động (t=240 đến 360), toàn bộ các câu hỏi FAQ gửi đến đều thất bại vì client không được phép trả lời bằng dữ liệu cũ trong cache, làm mất trắng độ sẵn sàng của kênh CSKH.

Trà lời:

- **Vì sao câu hỏi cá nhân ("Đơn #48213 của tôi đang ở đâu?") KHÔNG được cache, kể cả khi cache theo từng user?**
  1. *Tính đúng đắn của dữ liệu (Data Freshness):* Trạng thái đơn hàng, hoàn tiền, tích điểm thay đổi liên tục theo thời gian thực (vừa thanh toán, đang đóng gói, đang giao hàng). Nếu cache, người dùng sẽ nhận được thông tin cũ sai lệch gây bức xúc.
  2. *Quyền riêng tư và bảo mật (Privacy & Security):* Câu hỏi cá nhân chứa dữ liệu nhạy cảm. Vector embedding của hai câu hỏi cá nhân khác nhau chỉ khác nhau số đơn hàng ("Đơn #123" vs "Đơn #124") có độ tương đồng cosine lên tới ~0.99. Nếu lưu vào semantic cache, hệ thống rất dễ trả nhầm thông tin đơn hàng, địa chỉ và số tiền hoàn của người này cho người khác.

- **Trong lúc mọi provider sập, dashboard vẫn xanh nhờ cache. Bạn sẽ alert dựa trên số nào để on-call vẫn biết có sự cố?**
  Ta không thể chỉ dựa vào `user_perceived_availability` vì cache đã che giấu sự cố ở tầng ứng dụng. Cần phải thiết lập cảnh báo dựa trên:
  1. **Provider Error Rate / Success Rate** đo trực tiếp tại egress client trước khi vào cache (ví dụ: tỷ lệ lỗi 5xx/529 từ LLM backend vượt ngưỡng).
  2. **Tỷ lệ phục vụ Stale (`stale_served / total_requests`)**: Khi tỷ lệ này tăng đột biến > 5% trong 2 phút, chắc chắn backend đang sập và client đang phải dùng dữ liệu cũ để cầm cự.
  3. **Cache Miss Error Rate**: Tỷ lệ lỗi trên tập request bị cache miss (hoặc các request nghiệp vụ không cache được như `tool` calling).

