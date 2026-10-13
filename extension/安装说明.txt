Tiện ích nhập Cookie Muse2API —— Hướng dẫn cài đặt
==================================================

Tiện ích này chỉ làm một việc: đồng bộ Cookie đăng nhập muse.ai trong
trình duyệt của bạn sang dịch vụ muse2api của bạn.
Không cần cài Python, không cần mở terminal.


1. Giải nén
-----------
Hãy giải nén tệp muse2api-extension.zip vào một thư mục mà bạn sẽ không xóa, ví dụ:

    Windows : D:\muse2api-extension
    macOS   : ~/Documents/muse2api-extension

Sau khi giải nén, bạn sẽ thấy ba tệp: manifest.json, popup.html, popup.js.


2. Cài vào Chrome / Edge
------------------------
Chrome:
  1. Gõ  chrome://extensions  vào thanh địa chỉ rồi nhấn Enter
  2. Bật công tắc「Chế độ dành cho nhà phát triển」ở góc trên bên phải
  3. Nhấn「Tải tiện ích đã giải nén」ở góc trên bên trái
  4. Chọn đúng thư mục đã giải nén ở bước 1 (không phải một tệp riêng lẻ bên trong)
  5. Trên thanh công cụ sẽ xuất hiện một biểu tượng mảnh ghép — hãy ghim nó vào thanh công cụ (không bắt buộc nhưng sẽ tiện hơn)

Edge:
  1. Gõ  edge://extensions  vào thanh địa chỉ rồi nhấn Enter
  2. Bật「Chế độ nhà phát triển」ở góc dưới bên trái
  3. Nhấn「Tải tiện ích đã giải nén」
  4. Các bước còn lại giống như trên

Các trình duyệt dùng nhân Chromium khác (Brave / Vivaldi / 360 Speed 等) làm tương tự.


3. Sử dụng
----------
1. Trước tiên hãy mở https://muse.ai/ trong trình duyệt này và đăng nhập,
   đăng nhập đến khi thấy được giao diện trò chuyện.
2. Vào đầu trang「Nhóm tài khoản」của trang quản trị muse2api, sao chép BASE URL và API Key.
3. Nhấn biểu tượng tiện ích trên thanh công cụ trình duyệt, điền hai mục này vào
   (chỉ cần điền một lần, tiện ích sẽ ghi nhớ).
4. Nhấn「Đọc và nhập」. Khi thấy「✓ Nhập thành công」là xong.


4. Câu hỏi thường gặp
---------------------
H: Hiện thông báo「Không đọc được Cookie nào của muse.ai」
Đ: Nghĩa là trình duyệt này chưa đăng nhập muse.ai. Hãy mở https://muse.ai/ và đăng nhập trước.

H: Hiện thông báo「Thiếu mục cốt lõi hatch_sess 等」
Đ: Cũng như trên —— đăng nhập chưa hoàn tất. Hãy chắc chắn đã thấy được giao diện trò chuyện rồi hãy bấm.

H: Hiện thông báo「API Key không đúng (dịch vụ trả về 401)」
Đ: Vào trang quản trị sao chép lại API Key một lần nữa. Lưu ý Key bắt đầu bằng m2a_.

H: Hiện lỗi cross-origin / lỗi mạng
Đ: Kiểm tra xem địa chỉ dịch vụ có phải https không, và bản thân dịch vụ có mở được không
   (thử truy cập trực tiếp  địa-chỉ-dịch-vụ/admin  bằng trình duyệt).

H: Tiện ích có gửi Cookie của tôi đi đâu khác không?
Đ: Không. Tiện ích chỉ có hai quyền là cookies và storage, mã nguồn nằm ngay trong popup.js,
   bạn có thể tự xem: nó chỉ gửi một POST đến đúng địa chỉ dịch vụ bạn điền, không có yêu cầu nào khác.


5. Gỡ cài đặt
-------------
chrome://extensions → tìm「Nhập Cookie Muse2API」→ Gỡ bỏ.
