# MUSE2API

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python" alt="Python Version" />
  <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker" alt="Docker" />
  <img src="https://img.shields.io/badge/API-OpenAI%20Compatible-green" alt="OpenAI API Compatible" />
  <img src="https://img.shields.io/badge/License-MIT-orange" alt="License" />
  <a href="https://linux.do/" target="_blank"><img src="https://img.shields.io/badge/Community-LINUX%20DO-111827?logo=linux&logoColor=white" alt="LINUX DO" /></a>
</p>

<p align="center">
  🤝 Dự án mã nguồn mở này đã liên kết và công nhận <b><a href="https://linux.do/" target="_blank">Cộng đồng LINUX DO (https://linux.do/)</a></b> —— cộng đồng lý tưởng mới (chân thành, thân thiện, đoàn kết, chuyên nghiệp)
</p>

Đóng gói các khả năng đa phương thức tiên tiến ở giao diện web của **[muse.ai](https://muse.ai/)** thành **RESTful API tương thích OpenAI** chuẩn thông qua kỹ thuật đảo ngược. Nhờ xuyên thấu qua giao thức CDP của trình duyệt ẩn (headless), tái sử dụng đường hầm WebSocket dự phòng nóng và quản lý Session động, hệ thống hỗ trợ nguyên bản hội thoại văn bản (phản hồi ký tự đầu tiên dạng streaming chỉ 2~3 giây), tạo ảnh từ văn bản, chỉnh sửa ảnh từ ảnh, tạo video từ văn bản, tạo video từ ảnh khung đầu tiên, đồng thời cung cấp cơ chế xoay vòng linh hoạt theo pool đa tài khoản, tự động gia hạn phiên 48 giờ và đánh thức/giữ sống VM trên đám mây, cùng tiện ích mở rộng Chrome xuất tài khoản một chạm đi kèm.

---

## 🌟 Tính năng cốt lõi

- 💬 **Giao diện hội thoại chuẩn (Chat Completions & Responses API)**
  - Tương thích hoàn toàn với `/v1/chat/completions` và giao thức chuẩn `/v1/responses` mà Codex phiên bản mới sử dụng mặc định.
  - Hỗ trợ nguyên bản đầu ra streaming kiểu máy đánh chữ qua SSE (`stream=True`) và trả về đồng bộ đầy đủ, tích hợp sẵn cơ chế tái sử dụng affinity tab nóng và đường hầm Noise WebSocket, **độ trễ ký tự đầu tiên cho hội thoại liên tục chỉ 2~3 giây**.
  - Hỗ trợ ngữ cảnh hội thoại nhiều lượt, thiết lập System Prompt.
  - Tích hợp ánh xạ bí danh mô hình thông minh, tự động định tuyến các tên mô hình phổ biến như `gpt-4o`, `gpt-5`, `claude-sonnet-4`, `deepseek-chat`.
- 🎨 **Tạo ảnh và chỉnh sửa ảnh chất lượng cao (Images Generations & Edits)**
  - Tương thích hoàn toàn với `/v1/images/generations` (tạo ảnh từ văn bản) và `/v1/images/edits` (chỉnh sửa từ ảnh/ảnh tham chiếu).
  - Hỗ trợ nhiều tỉ lệ khung như `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, hỗ trợ truyền trực tiếp ảnh tham chiếu và xuất ảnh sạch đã xóa watermark.
  - Hỗ trợ hai định dạng trả về là liên kết trực tiếp `url` hoặc `b64_json`, tích hợp phát hiện nhanh việc từ chối trả lời dạng văn bản thuần trong vài giây, loại bỏ hoàn toàn tình trạng khóa cứng hàng đợi.
  - Tạo ảnh từ ảnh chỉ trả về media mới sinh trong lần này: loại trừ ảnh xem trước đã tải lên và tệp đính kèm lịch sử, việc tải xuống gắn với kết quả được chọn; báo lỗi rõ ràng khi việc tải lên ảnh tham chiếu hoặc gửi prompt chưa được xác nhận, không còn âm thầm trả về ảnh gốc.
- 🎬 **Tạo video từ văn bản / từ ảnh (Videos)**
  - Kết nối nguyên bản với mô hình tạo video cao cấp nhất của Muse, có thể truyền yêu cầu thời lượng 5 giây / 6 giây / 8 giây / 10 giây (thời lượng thành phẩm thực tế lấy theo đầu ra từ phía Muse) cùng tạo video dọc `9:16` / ngang `16:9`.
  - Hỗ trợ tải lên ảnh tham chiếu khung đầu (Data URL / HTTP URL) để sáng tác video từ khung đầu tiên một cách nghiêm ngặt.
  - Kiến trúc tác vụ bất đồng bộ (`/v1/videos` tạo tác vụ + `/v1/videos/{task_id}` thăm dò trạng thái).
  - Tích hợp dịch vụ tài nguyên media `/v1/media/{filename}`, tự động lưu trữ bền vững tài nguyên MP4 / WebP được tạo ra.
- ⚡ **Đa tài khoản chạy song song thực sự (Multi-Account Parallel Execution)**
  - Dựa trên container cách ly tàng hình `Target.createBrowserContext` nguyên bản của Chromium, nhiều tác vụ chạy song song trong các sandbox độc lập.
  - Nhiều tài khoản đồng thời tạo ảnh, tạo video, hội thoại streaming mà không chặn lẫn nhau, Cookie và Session cách ly 100% tuyệt đối không nhiễu chéo.
  - Tích hợp bộ điều phối xung đột tài khoản thông minh, cùng một tài khoản tự động loại trừ lẫn nhau chống giẫm đạp, nhiều tài khoản tự động chạy song song kín công suất.
  - Có thể tùy theo hiệu năng máy mà điều chỉnh linh hoạt giới hạn song song qua `MUSE2API_CONCURRENCY` (mặc định 2 song song).
- 🔄 **Pool đa tài khoản và điều phối affinity kết nối nóng**
  - Hỗ trợ nhập ma trận tài khoản Muse không giới hạn.
  - Dựa trên affinity kết nối nóng (Warm-Tab Affinity) và chiến lược LRU để phân phối thông minh, cân bằng giữa phản hồi siêu tốc 2 giây và mức tiêu thụ đồng đều giữa nhiều tài khoản.
  - Khi một tài khoản cạn hạn mức hoặc phiên gặp bất thường, tự động đánh dấu và chuyển đổi dự phòng tức thời 0 giây sang tài khoản khỏe mạnh dự phòng.
- 🛡️ **Tự động gia hạn phiên 48 giờ và giữ sống VM trên đám mây**
  - Coroutine nhịp tim chạy nền độc đáo, kết nối trực tiếp `/api/session` tự động gia hạn `hatch_vml` (+48h) và `hatch_sess` (+30d), đồng thời tự động gọi `/api/hatch/vm/wake` để giữ VM không gian làm việc trên đám mây luôn ở trạng thái dự phòng nóng.
  - Giải quyết triệt để vấn đề Cookie Meta hết hạn tĩnh sau 48 giờ và VM ngủ đông mất kết nối, không cần đăng nhập lại thường xuyên.
- 🧩 **Tiện ích mở rộng Chrome xuất tài khoản một chạm đi kèm**
  - Không cần bắt gói thủ công bằng F12, chỉ cần nhấn vào biểu tượng tiện ích là có thể trích xuất trạng thái đăng nhập hiện tại của trình duyệt (bao gồm Cookie cốt lõi `HttpOnly` và thời gian hết hạn thực) và đẩy an toàn vào pool tài khoản.
- 🖥️ **Bảng vận hành giao diện tối hiện đại (Web Console) và nâng cấp nóng trực tuyến theo thời gian thực**
  - Tích hợp Web UI dùng ngay, hỗ trợ xem trạng thái sức khỏe dịch vụ theo thời gian thực, hạn mức và trạng thái pool tài khoản, giữ sống toàn pool một chạm, phát lại tiến độ tác vụ, quản lý thư viện media và gỡ lỗi API trực tuyến.
  - **Phát sóng cập nhật thời gian thực trên toàn mạng lưới nút và nâng cấp một chạm**: khi kho GitHub chính thức phát hành phiên bản mới hoặc bản sửa lỗi, phần trên cùng của trang quản trị trên tất cả các nút đã triển khai sẽ tự động hiện banner thông báo cập nhật, nhấn **「⚡ Nâng cấp trực tuyến và khởi động lại một chạm」** là có thể tự động kéo mã mới nhất và khởi động lại mượt mà (tự động giữ lại cấu hình `.env` cục bộ và dữ liệu tài khoản).

---

## 🚀 Triển khai siêu tốc

### Cách 1: Docker Compose (khuyến nghị, dùng ngay với một dòng lệnh)

1. **Clone mã nguồn và vào thư mục**:
   ```bash
   git clone https://github.com/lionels-kayden/muse2api-lio.git
   cd muse2api
   ```

2. **Cấu hình biến môi trường (tùy chọn)**:
   ```bash
   cp .env.example .env
   # Chỉnh sửa .env theo nhu cầu, khuyến nghị đổi MUSE2API_KEY thành khóa quản trị của riêng bạn
   ```

   Docker Compose sẽ đọc `MUSE2API_KEY` trong `.env`, sẽ không còn bị giá trị mẫu trong file Compose ghi đè nữa.

3. **Khởi động container**:
   ```bash
   docker compose up -d
   ```

4. **Truy cập bảng quản trị**:
   Mở trình duyệt truy cập: `http://<IP máy chủ của bạn>:18610/admin?key=<MUSE2API_KEY của bạn>`

---

### Cách 2: Triển khai trên máy trần / VPS (Ubuntu / Debian)

1. **Cài đặt phụ thuộc hệ thống và Chromium**:
   ```bash
   sudo apt-get update
   sudo apt-get install -y chromium fonts-wqy-zenhei python3 python3-pip python3-venv
   ```

2. **Cấu hình môi trường ảo Python**:
   ```bash
   cd /opt/muse2api
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Cấu hình dịch vụ systemd**:
   ```bash
   sudo cp deploy/muse2api.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now muse2api
   ```

4. **Cấu hình reverse proxy Nginx (quan trọng: chống timeout 502 và giật lag streaming)**:
   Nếu dùng Nginx / BaoTa / 1Panel làm reverse proxy, nhất định phải tăng `proxy_read_timeout` lên `600s` và tắt `proxy_buffering` (xem chi tiết tại `deploy/nginx.example.conf`):
   ```nginx
   location / {
       proxy_pass http://127.0.0.1:18610;
       proxy_read_timeout 600s;      # Tránh việc tạo ảnh/video mất nhiều thời gian bị Nginx báo 502 Bad Gateway
       proxy_send_timeout 600s;
       proxy_buffering off;          # Đảm bảo luồng hội thoại SSE streaming kiểu máy đánh chữ xuất chữ với độ trễ 0
       client_max_body_size 64M;     # Hỗ trợ tải lên tệp lớn ảnh tham chiếu
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
   }
   ```

---

## 🧩 Nhập tài khoản (Tiện ích Chrome / Userscript Tampermonkey)

### Cách A: Tiện ích Chrome / Edge
Dự án tích hợp sẵn tiện ích Chrome xuất tài khoản chuyên dụng (thư mục tại `extension/`), tạm biệt việc trích xuất Cookie F12 phức tạp:

1. Mở trình duyệt Chrome / Edge, truy cập `chrome://extensions`, bật **「Chế độ nhà phát triển」** ở góc trên bên phải.
2. Nhấn **「Tải tiện ích chưa đóng gói」**, chọn thư mục `extension` trong dự án này.
3. Đăng nhập [muse.ai](https://muse.ai/) trên trình duyệt đó đến giao diện chính của chat.
4. Nhấn biểu tượng tiện ích ở góc trên bên phải trình duyệt, điền địa chỉ dịch vụ của bạn (ví dụ `http://1.2.3.4:18610`) và `MUSE2API_KEY`.
5. Nhấn **「Đọc và nhập」**, đồng bộ vào kho chỉ trong vài giây!

### Cách B: Userscript Tampermonkey (dùng đa trình duyệt, không cần cài tiện ích)
Với những người không muốn bật chế độ nhà phát triển hoặc dùng Firefox / Safari, dự án cung cấp userscript (tại `tools/muse2api_cookie_importer.user.js`):
1. Cài tiện ích Tampermonkey trên trình duyệt.
2. Tạo script mới rồi sao chép nội dung `tools/muse2api_cookie_importer.user.js` và dán vào lưu lại.
3. Mở trang [muse.ai](https://muse.ai/), ở góc dưới bên phải sẽ xuất hiện nút nổi **「⚡ Nhập vào muse2api」**, nhấp chuột phải để cấu hình địa chỉ dịch vụ và Key, nhấp chuột trái để đẩy một chạm vào kho và tự động sao chép dự phòng vào clipboard.

---

## 📖 Ví dụ gọi API

Tất cả các API được bảo vệ đều cần mang theo trong Header:
```http
Authorization: Bearer <MUSE2API_KEY của bạn>
```

### 1. Bổ sung hội thoại (Chat Completions)

```bash
curl -X POST "http://localhost:18610/v1/chat/completions" \
  -H "Authorization: Bearer m2a_your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "muse-spark",
    "messages": [
      {"role": "user", "content": "Viết một bài thơ tứ tuyệt bảy chữ về cyberpunk và mưa đêm neon"}
    ],
    "stream": false
  }'
```

### 2. Tạo ảnh từ văn bản (Image Generation)

```bash
curl -X POST "http://localhost:18610/v1/images/generations" \
  -H "Authorization: Bearer m2a_your_secret_key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "muse-image",
    "prompt": "Một chú chó Shiba cơ khí mặc bộ đồ du hành phản trọng lực, ánh sáng điện ảnh, độ phân giải 8k",
    "size": "16:9",
    "response_format": "url"
  }'
```

**Ví dụ phản hồi**:
```json
{
  "created": 1790148495,
  "data": [
    {
      "revised_prompt": "Một chú chó Shiba cơ khí mặc bộ đồ du hành phản trọng lực, ánh sáng điện ảnh, độ phân giải 8k",
      "url": "http://localhost:18610/v1/media/img_abc123.webp",
      "kind": "image",
      "bytes": 54210
    }
  ]
}
```

### 3. Tạo video từ văn bản / từ ảnh (Videos)

- **Bước một: Tạo tác vụ sinh**
  ```bash
  curl -X POST "http://localhost:18610/v1/videos" \
    -H "Authorization: Bearer m2a_your_secret_key" \
    -H "Content-Type: application/json" \
    -d '{
      "prompt": "Những chiếc lá phong vàng nhẹ nhàng rơi trong làn gió nhẹ, ánh nắng xuyên qua ngọn cây",
      "duration": 5,
      "size": "16:9"
    }'
  ```
  Trả về ID tác vụ: `{"id": "task_xyz789", "status": "queued"}`

- **Bước hai: Thăm dò tiến độ tác vụ**
  ```bash
  curl "http://localhost:18610/v1/videos/task_xyz789" \
    -H "Authorization: Bearer m2a_your_secret_key"
  ```
  Khi hoàn thành trả về:
  ```json
  {
    "id": "task_xyz789",
    "status": "succeeded",
    "progress": 100,
    "result": {
      "url": "http://localhost:18610/v1/media/vid_xyz789.mp4"
    }
  }
  ```

---

## ⚙️ Tổng quan cấu hình biến môi trường

| Tên biến | Giá trị mặc định | Tác dụng và mô tả |
|---|---|---|
| `MUSE2API_KEY` | Tự động tạo | Khóa xác thực bảng quản trị và API (bắt đầu bằng `m2a_`) |
| `MUSE2API_HOST` | `127.0.0.1` | IP lắng nghe (trong Docker khuyến nghị `0.0.0.0`) |
| `MUSE2API_PORT` | `18610` | Cổng chạy dịch vụ |
| `MUSE2API_PUBLIC_BASE` | `Trống` | Tiền tố URL đầy đủ đối ngoại, khi để trống frontend tự động nhận diện nguồn truy cập |
| `MUSE2API_CHROMIUM` | `chromium` | Đường dẫn đầy đủ của chương trình thực thi trình duyệt |
| `MUSE2API_CDP_PORT` | `19210` | Cổng giao tiếp gỡ lỗi CDP nội bộ |
| `MUSE2API_CONCURRENCY` | `2` | Số tác vụ sinh song song tối đa (hỗ trợ tùy tài nguyên máy chủ mà tăng thoải mái, mặc định 2) |
| `MUSE2API_IMAGE_TIMEOUT` | `240` | Giới hạn timeout tạo ảnh (giây) |
| `MUSE2API_VIDEO_TIMEOUT` | `600` | Giới hạn timeout tạo video (giây) |
| `MUSE2API_CHAT_TIMEOUT` | `300` | Giới hạn timeout sinh hội thoại (giây) |

---

## 🔒 Tuyên bố bảo mật và quyền riêng tư

- **Không rò rỉ dữ liệu**: Toàn bộ dữ liệu của phần mềm này (bao gồm thông tin xác thực tài khoản, hàng đợi tác vụ, tệp media) đều được lưu trữ bền vững trong thư mục `data/` cục bộ, không phụ thuộc bất kỳ dịch vụ telemetry bên thứ ba hay dịch vụ trung chuyển bên ngoài nào.
- **Tuân thủ mã nguồn mở**: Dự án này chỉ dành cho giao lưu kỹ thuật, nghiên cứu vận hành tự động hóa hệ thống và kiểm thử tự động. Vui lòng không dùng dự án này cho mục đích vi phạm điều khoản dịch vụ của nền tảng Meta hoặc bất kỳ luật pháp, quy định nào của quốc gia.

---

## 🤝 Cộng đồng công nhận và liên kết hữu nghị

Dự án này đã liên kết và đánh giá cao **[Cộng đồng LINUX DO](https://linux.do/)**, cảm ơn sự giao lưu, phản hồi và ủng hộ của các bạn trong cộng đồng:

- 🌐 **[Cộng đồng LINUX DO (https://linux.do/)](https://linux.do/)** —— cộng đồng lý tưởng mới (chân thành, thân thiện, đoàn kết, chuyên nghiệp, cùng xây dựng cộng đồng mà bạn và tôi đều tự hào)

---

## 👥 Tri ân người đóng góp (Contributors)

Cảm ơn các nhà phát triển sau đây đã đóng góp mã nguồn và tối ưu tính năng cho MUSE2API (sắp xếp theo thứ tự PR được hợp nhất):

- 🌟 **[@cpt-kenvie](https://github.com/cpt-kenvie)** (PR [#2](https://github.com/czg86389-hub/muse2api/pull/2)) —— Sửa vấn đề Docker Compose đọc biến môi trường `.env` và cấu hình mẫu bị ghi đè
- 🌟 **[@CarloCPP](https://github.com/CarloCPP)** (PR [#6](https://github.com/czg86389-hub/muse2api/pull/6)) —— Đóng góp script xuất tài khoản một chạm Tampermonkey và cấu hình giữ sống tự động cho tài khoản đã bị vô hiệu
- 🌟 **[@djs-91](https://github.com/djs-91)** (PR [#9](https://github.com/czg86389-hub/muse2api/pull/9)) —— Sửa lỗi khi chưa cấu hình `MUSE2API_PUBLIC_BASE` thì Base URL bị thu gọn dẫn đến phân giải địa chỉ trang quản trị bất thường
- 🌟 **[@toby-bridges](https://github.com/toby-bridges)** (PR [#8](https://github.com/czg86389-hub/muse2api/pull/8)) —— Thích ứng UI tiếng Trung trên mạng hiện tại của muse.ai, sửa việc xác nhận tải lên tệp đính kèm và nhận định nhầm trạng thái Stop, tăng cường chống chọn nhầm video thành phẩm và ảnh bìa
- 🌟 **[@LF2023](https://github.com/LF2023)** (PR [#18](https://github.com/czg86389-hub/muse2api/pull/18)) —— Sửa vấn đề tạo video chỉ dựa vào tid để trúng khung tĩnh ảnh bìa, chuẩn hóa cơ chế khớp dự kiến và chống chọn nhầm

Hoan nghênh mọi người gửi PR và Issue, cùng nhau mài giũa dự án ngày càng mạnh mẽ và hữu dụng hơn!

---

## 📄 Giấy phép mã nguồn mở

Dự án này được phát hành mã nguồn mở theo [MIT License](LICENSE).


## Kiểm thử hồi quy chọn media

Sửa vấn đề ảnh tham chiếu tải lên bị nhận nhầm là kết quả sinh, và quá trình tải xuống bị media khác trên trang ghi đè.
Kiểm thử dùng profile Chromium tạm thời độc lập và DOM tổng hợp cục bộ, không truy cập Muse, không đọc tài khoản, không tiêu tốn hạn mức sinh.
Sau khi cài đặt phụ thuộc dự án và Chromium, chạy từ thư mục gốc kho:

```bash
python tests/test_media_selection.py
```

Nếu trình duyệt không nằm trong PATH, có thể đặt `MUSE2API_CHROMIUM` là đường dẫn tệp thực thi.
Bao gồm loại trừ ảnh xem trước tải lên, khử trùng lặp kết quả, loại trừ tệp đính kèm lịch sử, tải xuống theo nguồn chính xác, giữ nguồn video,
cấm dự phòng tải xuống toàn cục, và dừng lại khi đọc ảnh tham chiếu thất bại. Hiệu quả chỉnh sửa ảnh cuối cùng vẫn cần nghiệm thu qua API thực tế.


## v1.5.2: Tạo ảnh mất nhiều thời gian sau reverse proxy/CDN

API ảnh đồng bộ kiểu OpenAI vẫn được giữ tương thích, nhưng CDN/client có thể cắt ngang yêu cầu dài trước khi sinh xong.
Client production nên dùng gửi ngắn + thăm dò, thay vì kéo dài timeout HTTP rồi sinh lại:

1. `POST /v1/images/tasks`: JSON giống với `/v1/images/generations`, có thể dùng `reference_image` để truyền ảnh tham chiếu cho tạo ảnh từ ảnh; khuyến nghị thêm header yêu cầu `Idempotency-Key` ổn định.
2. Nhận HTTP 202 và `id`, mỗi 3 giây gọi `GET /v1/images/tasks/{id}`.
3. Khi `status=completed` thì đọc `url` hoặc `data[0]`; khi `status=failed` thì hiển thị `error`. Truy vấn thất bại chỉ thử lại truy vấn, không POST lại sinh ảnh.
4. Cùng khóa idempotency và cùng đầu vào trả về tác vụ gốc; cùng khóa khác đầu vào trả về 409; hàng đợi đầy trả về 429. Một tiến trình chia sẻ một trình duyệt, nhận tối đa 8 tác vụ ảnh chưa kết thúc.

`/v1/images/generations` JSON gốc và `/v1/images/edits` JSON/multipart cũng có thể truyền `async=true`, chuyển sang cùng xử lý bất đồng bộ. Hành vi đồng bộ mặc định không đổi.
`timeout` là ngân sách chờ xếp hàng và sinh (1–600 giây), khởi tạo trình duyệt và lấy về có timeout riêng; client nên tiếp tục thăm dò trạng thái cuối của tác vụ.
Metadata tác vụ được lưu bền vững; khi tiến trình khởi động lại, các tác vụ ảnh chưa hoàn thành được đánh dấu thất bại, không âm thầm sinh lại.
Kiểm thử API ảnh trên trang quản trị cũng đã đổi thành thăm dò tác vụ.

Kiểm tra hồi quy (không tiêu tốn hạn mức sinh thật):

```bash
python tests/test_async_images.py
python tests/test_vm_wait.py engine.py --assert
```

Sửa lỗi cạnh tranh khi xử lý ngoại lệ trình duyệt chia sẻ đặt lại tác vụ khác sau khi mở khóa; không còn coi văn bản
`Connecting...` / `Still sending` còn sót lại ở thanh bên trang là bằng chứng tác vụ sinh ảnh hiện tại thất bại trong 16 giây.


## v1.5.3: Trạng thái tài khoản bất thường và khắc phục sự cố instance tự dựng

Yêu cầu giữ sống đến từ **cổng ra của máy chủ triển khai**, không phải từ trình duyệt đã xuất Cookie. Máy chủ của tác giả dùng được không chứng minh được mạng, khu vực, cấu hình proxy hay cùng một phiên tài khoản trên máy chủ khác cũng bình thường.

- `/api/session HTTP 401`: phía upstream từ chối xác thực. Trước tiên xác nhận tài khoản đó có thể đăng nhập trên trang muse.ai chính thức, rồi nhập lại Cookie.
- `HTTP 403`: truy cập bị từ chối, có thể liên quan đến quyền tài khoản, cổng ra máy chủ hoặc hạn chế khu vực/truy cập; **không đồng nghĩa với Cookie hết hạn**. Kiểm tra lỗi đầy đủ và mạng triển khai, đừng nhập lại một cách mù quáng nhiều lần.
- `HTTP 429`, `5xx`, timeout, không phải JSON hoặc không trả về `assigned`: lần giữ sống này chưa được xác nhận, không còn báo thành công giả hoặc ghi đè trạng thái tài khoản đã xác nhận lần trước; phần ghi chú hiển thị chẩn đoán. Khi trạng thái gốc là "chưa kiểm tra" hoặc "bất thường" cũng không bị ép đổi thành "khả dụng".
- Trang trình duyệt tải quá lâu cũng không còn bị trực tiếp coi là xác thực mất hiệu lực; chỉ khi xác thực thất bại rõ ràng mới đánh dấu tài khoản bất thường.
- "Khả dụng" là kết quả đã xác nhận gần nhất, không đảm bảo mạng lần này có thể kết nối; "thời gian hiệu lực còn lại" là giá trị thời gian/ước lượng của Cookie, không phải bằng chứng phiên phía máy chủ có hiệu lực. Kiểm tra thành công không còn kéo dài 48 giờ một cách vô căn cứ, việc gia hạn lấy theo Cookie thực tế trả về.

Sau khi cập nhật lên v1.5.3 và khởi động lại dịch vụ, nhấn "kiểm tra" ở tài khoản để xác nhận lại. Các bất thường đã được ghi vào từ phiên bản cũ sẽ không bị rửa thành bình thường một cách vô điều kiện. Di chuột vào ghi chú để xem lỗi đầy đủ; khi phản hồi vui lòng cung cấp HTTP status, phiên bản và môi trường triển khai, **không công khai Cookie, API Key hoặc liên kết trang quản trị có kèm key**.

Triển khai Git trên máy trần: sau `git pull --ff-only` thì khởi động lại dịch vụ tương ứng; Docker Compose: `git pull --ff-only && docker compose up -d --build`. Vui lòng lưu trước các chỉnh sửa tùy chỉnh cục bộ, giữ lại `.env` và `data/`.

Kiểm thử hồi quy không dùng tài khoản thật, không tiêu tốn hạn mức sinh:

```bash
python tests/test_session_health.py
```
# MUSE2API 1.6.1

## Ghi chú cập nhật (2026-10-13)

- Sửa lỗi đọc chéo hạn mức đa tài khoản: truy vấn hạn mức gắn với trang trình duyệt của tài khoản tương ứng, tránh hiển thị sai số dư và mức sử dụng giữa các tài khoản khác nhau.
- Khi đọc hạn mức thất bại hoặc không đọc được dữ liệu hợp lệ thì giữ cache cũ, và báo cáo rõ ràng việc đọc thất bại, tránh coi kết quả không hợp lệ là hạn mức thời gian thực.
- Sửa báo nhầm giữ sống thành công: phân biệt yêu cầu đánh thức đã được chấp nhận với VM đã chạy, chỉ khi trạng thái kiểm tra lại là RUNNING mới báo đánh thức thành công.
- Trang quản trị hiển thị thời gian truy vấn hạn mức và nhắc nhở hết hạn; kiểm tra tài khoản phản hồi riêng biệt tính hiệu lực của phiên và trạng thái đọc hạn mức.
- Thêm kiểm thử hồi quy offline, bao phủ cách ly hạn mức tài khoản và xác nhận trạng thái đánh thức VM.

## Ghi chú dịch (Việt hoá)

Toàn bộ dự án đã được dịch sang tiếng Việt: README, `admin.html`, `config.py`, `cdp.py`, `store.py`, `engine.py`, `app.py`, thư mục `extension/`, `tools/`, `deploy/`, `Dockerfile`, `docker-compose.yml`, `version.json` và các chú thích trong `tests/`.

### Những chuỗi tiếng Trung được GIỮ NGUYÊN có chủ đích

Đây là các chuỗi **chức năng**, dịch đi sẽ làm hỏng hoạt động của tool:

- **Regex khớp DOM của muse.ai** trong `engine.py` (`button[aria-label*="停止"]`, `/发送|send/i`, `/下载|保存|download/i`, `移除附件|删除附件`, `额度不足|积分不足`, `正在发送|未确认送达`, `登录|use another account`...). Giao diện muse.ai hiển thị tiếng Trung, nên các selector này phải khớp đúng tiếng Trung.
- **Payload prompt gửi tới muse.ai** trong `app.py` (`build_image_prompt`, `build_video_prompt`, `_TOOL_PROTOCOL_HEAD`, `参考此图片并进行生图创作`, `【工具执行结果】`, `【请求调用工具】`...). Đây là nội dung gửi lên model, không phải văn bản hiển thị cho người dùng.
- `tools/verify_engine_selectors.py:91` — selector `button[aria-label*="移除附件"]` dùng để kiểm tra DOM upstream.

### Lỗi tồn tại từ trước (không sửa, giữ nguyên hành vi)

`app.py:899` so khớp literal `"未产出媒体附件，仅返回了文本回复"` với thông báo lỗi do engine ném ra. Thực tế `engine.py` ném chuỗi `"模型未生成媒体，仅返回文本"`, nên **điều kiện này không bao giờ đúng** và nhánh dừng sớm thử lại là code chết. Bản dịch giữ nguyên hành vi cũ thay vì âm thầm sửa; nếu muốn khắc phục, hãy đồng bộ hai chuỗi này.

## Hai giao diện quản trị

Dự án có hai giao diện quản trị dùng chung **một bộ API backend duy nhất** — không có endpoint, tên trường JSON hay khoá `localStorage` nào khác nhau giữa hai bản.

| Đường dẫn | Tệp | Mô tả |
|---|---|---|
| `/`, `/admin`, `/admin/v2` | `admin-v2.html` | **Giao diện mới** (mặc định) — bố cục sidebar + form theo biểu mẫu, có nút Sáng/Tối, thay hộp thoại `prompt()`/`confirm()` của trình duyệt bằng modal trong trang |
| `/admin/classic` | `admin.html` | **Giao diện cổ điển** — bản 1:1 với kho gốc về cấu trúc và chức năng, console 6 tab ngang, giữ nguyên để đối chiếu |

- Hai bản có link chuyển đổi qua lại ngay trên thanh tiêu đề (bản mới: mục *Chuyển sang giao diện cổ điển*; bản cổ điển: pill *Giao diện mới*).
- Nếu thiếu tệp `admin-v2.html`, `/admin` sẽ **tự động lùi về** giao diện cổ điển thay vì báo lỗi.
- Thêm giao diện mới chỉ là **thêm một tệp HTML tĩnh**: `app.py` đọc trang từ đĩa ở mỗi request, backend không đổi. Tệp này đã được thêm vào `TRACKED_REPO_PATHS` và `Dockerfile` để tính năng cập nhật/nâng cấp trực tuyến không bỏ sót.
