# Huong dan su dung workflow Long Video va Shorts

File nay giai thich cach dung tool sau khi da them 2 kieu video va Shorts package.

## 1. Chon kieu video

Trong panel ben trai, muc `Dau vao kich ban`, co truong `Kieu video`.

Co 2 lua chon:

- `Growth 25-30 phut`
  - Nen dung mac dinh hien tai.
  - Tao video khoang 25-30 phut.
  - 5 chuong.
  - Phu hop de test kenh, test title/thumb/hook, tang retention.

- `Long Form hien tai`
  - Tao video dai hon, 8 chuong.
  - Dung khi format/title da co dau hieu thang.

Khuyen nghi hien tai: dung `Growth 25-30 phut` truoc.

## 2. Quy trinh tao video dai

Dung nhu binh thuong:

1. Dien `Ten project`.
2. Dan transcript/noi dung goc.
3. Chon hoac dan text thumbnail goc neu co.
4. Chon `Kieu video`.
5. Chon voice trong `Text to Voice`.
6. Bam `Chay tat ca`.

Tool se tu tao:

- title/thumb variants
- title/thumb thang
- retention map
- hook lab 30 giay dau
- script tung chuong
- voice tung chuong
- upload package
- shorts package

Ket qua nam trong:

```text
Projects/<ten_project>/
  artifacts/
  scripts/
  voices/
  shorts/
```

## 3. Render video dai

Sau khi voice va script xong:

1. Vao tab `Ket qua`.
2. Chon project.
3. Neu chua login VEO3, bam `Login VEO3` tren hang nut dau app.
4. O hang `Video dai`, bam `Dai: Tao + tai`.
5. Tool tu lay prompt VEO3 cua video dai, gui vao VEO3 va tai video ve `veo_videos`.
6. Bam `Ghep video` neu muon ghep cac clip VEO thanh 1 file nen.
7. Bam `Sub + final`.

Video dai se xuat o:

```text
Projects/<ten_project>/final/final_video.mp4
```

## 4. Shorts package la gi?

Moi video dai se tao san 2 Shorts:

- `short_01_hook_test`
  - 24 giay.
  - 3 block VEO, moi block 8 giay.
  - Dung de test hook nhanh.

- `short_02_main_trailer`
  - 40 giay.
  - 5 block VEO, moi block 8 giay.
  - Dung lam trailer chinh keo nguoi xem ve video dai.

Thu muc Shorts:

```text
Projects/<ten_project>/shorts/
  shorts_package_raw.txt
  shorts_package.json
  shorts_validation.txt
  short_01_hook_test/
  short_02_main_trailer/
```

Co the bam nut `Mo Shorts` trong tab `Ket qua` de mo nhanh thu muc nay.

## 5. Ben trong moi Short co gi?

Vi du:

```text
short_01_hook_test/
  metadata.json
  voice.txt
  cta.txt
  caption_overlay.txt
  related_video_cta.txt
  cover_image_prompt.txt
  cover_negative_prompt.txt
  block_01.voice.txt
  block_01.veo_prompt.txt
  block_01.image_prompt.txt
  block_01.negative_prompt.txt
  block_02.voice.txt
  block_02.veo_prompt.txt
  ...
```

Y nghia:

- `voice.txt`: full voice script cua Short.
- `cta.txt`: cau cuoi keu goi subscribe va bat chuong.
- `cover_image_prompt.txt`: prompt anh cover/anh dai dien cho Short.
- `block_XX.voice.txt`: voice cua block 8 giay do.
- `block_XX.veo_prompt.txt`: prompt VEO 8 giay dung voi voice block do.
- `block_XX.image_prompt.txt`: prompt anh dung voi ngu canh voice block do.
- `shorts_validation.txt`: canh bao neu ChatGPT tra sai format.

## 6. Cach tao Short bang VEO

VEO moi clip mac dinh 8 giay, nen tool chia Short thanh block:

- Short 1: 3 block x 8 giay = 24 giay.
- Short 2: 5 block x 8 giay = 40 giay.

Cach dung moi:

1. Vao tab `Ket qua`.
2. Chon project.
3. O hang `Shorts`, bam `Shorts: Tao + tai`.

Tool se tu:

- Lay 8 prompt Short rieng tu cac file `block_XX.veo_prompt.txt`.
- Tu ghep them `block_XX.voice.txt` vao prompt gui sang VEO3 de hinh bam dung cau voice.
- Tao/tai VEO 9:16 ve `shorts/veo_videos`.
- Copy media ve dung thu muc:

```text
short_01_hook_test/media/block_01.mp4
short_01_hook_test/media/block_02.mp4
short_01_hook_test/media/block_03.mp4
short_02_main_trailer/media/block_01.mp4
...
```

Neu VEO3 tra them image preview, tool cung copy ve `media/block_XX.jpg` lam anh backup/cover.

## 7. Voice cho Shorts

Short co voice rieng, khong cat nguyen tu video dai. Tool tao voice tu:

```text
short_01_hook_test/voice.txt
short_02_main_trailer/voice.txt
```

File `voice.txt` duoc ghep tu cac `block_XX.voice.txt`, nen voice va prompt VEO cua tung block khop nhau.

Luu y:

- Cuoi voice co cau subscribe va bat chuong.
- Khong spoil het ket thuc video dai.

## 8. Edit/xuat Short

Sau khi da co media VEO Short:

1. O tab `Ket qua`, hang `Shorts`, bam `Shorts: Voice + edit`.

Tool se tu:

- Tao `voice.wav` rieng cho tung Short.
- Ghep cac block 8 giay thanh video doc 1080x1920.
- Them subtitle lon kieu Shorts.
- Xuat file:

```text
short_01_hook_test/short_01_hook_test.mp4
short_02_main_trailer/short_02_main_trailer.mp4
```

## 9. Dang Short

Khi dang Short:

- Title ngan, dung hook cua Short.
- Mo ta ngan, co the dan 1 cau tu `related_video_cta.txt`.
- Gan Related Video ve video dai tuong ung.
- Pinned comment nen hoi 1 cau gay tranh luan nhe.

Vi du CTA:

```text
Full story is on the channel. Subscribe and turn on the bell for the next part.
```

## 10. Lich dang goi y

Neu moi video dai co 2 Shorts:

- Ngay dang video dai: dang `short_01_hook_test`.
- Sau 24-48 gio: dang `short_02_main_trailer`.

Muc tieu:

- Short 1 test hook.
- Short 2 keo nguoi xem ve video dai.

Khong can dang qua nhieu Shorts neu kenh con moi. Quan trong la xem:

- viewed vs swiped away
- average percentage viewed
- comment
- co keo nguoi xem sang video dai khong

## 11. Khi nao can sua Short?

Sua neu:

- 2 giay dau khong co cu soc.
- Voice qua giong tom tat.
- Anh/VEO khong khop voi voice.
- CTA qua dai hoac qua ban hang.
- Short spoil het cau chuyen.

Cong thuc dung:

```text
Short = mot canh soc + mot bang chung + mot open loop + CTA nhe
```

Khong bien Short thanh ban tom tat day du cua video dai.
