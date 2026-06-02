# Tool Kịch Bản & Text to Voice

Tool này tạo kịch bản bằng ChatGPT web và tự tạo audio từng chương bằng bản Text to Voice nằm trong chính thư mục tool:

`.\kokoro-tts-local`

Audio được tạo trực tiếp bằng Text to Voice local, không cần điều khiển thêm trình duyệt web cho bước voice.
Có thể nén riêng thư mục `tool master` để chuyển sang máy khác vì Kokoro, cache model và cấu hình Text to Voice đã nằm bên trong thư mục này.

## Cách chạy

1. Mở `run_tool.bat` hoặc shortcut ngoài Desktop.
2. Bấm `Khởi động / đăng nhập ChatGPT`, đăng nhập ChatGPT trong profile Chrome vừa mở.
3. Chọn cấu hình `Text to Voice`: ngôn ngữ, giọng, kiểu đọc và tốc độ.
4. Có thể bấm `Mở Text to Voice UI` để mở giao diện Kokoro Voice Studio mới nhất.
5. Dán transcript hoặc nội dung gốc.
6. Chọn ảnh thumbnail, hoặc dán sẵn text thumbnail để bỏ qua bước đọc ảnh.
7. Bấm `Chạy tất cả`.

## Kết quả

Mỗi project nằm trong `Projects/<timestamp_ten_project>`:

- `artifacts/*.txt`
- `scripts/chapter_XX.txt`
- `voices/chapter_XX.wav`
- `logs/run.log`

Tab `Kết quả` có bảng để xem text, copy text, mở audio hoặc mở thư mục project.

## VEO3 và Auto Edit

Trong tab `Kết quả`:

- `Đăng nhập VEO3`: mở Google Labs/Flow bằng engine VEO3 nội bộ để lấy token.
- `Tạo VEO3 + edit`: đẩy prompt từ `artifacts/whisk_prompts.txt` sang VEO3, tải video về `veo_videos`, rồi tự dựng final.
- `Chuẩn bị prompt VEO3`: lấy `artifacts/whisk_prompts.txt`, copy tối đa `Số prompt VEO3` prompt vào clipboard và lưu file `veo_videos/veo_prompts_for_veo3.txt` nếu muốn chạy thủ công.
- `Mở thư mục VEO video`: mở thư mục `veo_videos` của project để bỏ các video VEO đã tải về.
- `Tự edit video + sub`: ghép toàn bộ `voices/chapter_XX.wav`, lặp/cắt các video trong `veo_videos`, đặt ảnh nhân vật 9:16 bên phải, thêm waveform giữa khung, tạo subtitle từ `scripts/chapter_XX.txt`, rồi xuất `final/final_video.mp4`.

Mặc định video final là `1920x1080`, layout `character_drama`. Chọn ảnh nhân vật 9:16 JPG/PNG/WebP ở panel trái trước khi chạy project, hoặc đặt fallback trong `Ảnh nhân vật mặc định` ở tab `Cấu hình`.

## Text to Voice

- Tool dùng Python venv của `kokoro-tts-local\.venv`.
- Nếu chưa setup, chạy:

```powershell
cd ".\kokoro-tts-local"
powershell.exe -ExecutionPolicy Bypass -File .\setup.ps1
```

- Các chức năng đã nối vào master tool:
  - chọn language code của Kokoro;
  - chọn voice theo language;
  - chọn delivery style: mặc định, tự nhiên, nhấn nhẹ, diễn cảm, Heavy Drama, kể chuyện, điềm tĩnh;
  - mặc định dùng `Diễn cảm` với speed `1.0`;
  - chỉnh speed từ `0.5` đến `2.0`;
  - tự chia chapter dài và ghép lại thành một file WAV.

Lưu ý: Kokoro hiện chưa có voice tiếng Việt native; tiếng Anh cho kết quả tốt nhất.
Nếu chuyển sang laptop khác mà `.venv` không chạy do khác môi trường Python, chạy lại `.\kokoro-tts-local\setup.ps1` trong chính thư mục `tool master` để tạo lại venv nội bộ.
