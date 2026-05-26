# Melon Music Downloader

Electron desktop app de import Excel Melon, crawl metadata/lyrics, tai audio bang `yt-dlp`, va dong bo Google Sheet.

## Chay dev tren may local

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm install
npm start
```

May dev can co `yt-dlp` va `ffmpeg` trong PATH neu chua dung ban packaged.

## Build Windows bang GitHub Actions

Workflow `.github/workflows/build-windows.yml` tao installer Windows tu dong. Ban build Windows da dong goi san:

- Python backend dang `backend.exe`
- `yt-dlp.exe`
- `ffmpeg.exe`
- `ffprobe.exe`

Nguoi dung Windows chi can tai artifact installer, cai app, mo app, chon Excel, chon thu muc luu, them YouTube cookie va nhap range row can chay.

### Cach build

1. Push repo len GitHub.
2. Vao tab `Actions`.
3. Chon `Build Windows Installer`.
4. Bam `Run workflow`.
5. Tai artifact `MelonMusicDownloader-Windows`.

## Chia range cho nhieu may

Moi may phai chay range khac nhau vi SQLite nam rieng tren tung may.

Vi du:

```text
May 1: 4002 - 5001
May 2: 5002 - 6001
May 3: 6002 - 7001
May 4: 7002 - 8001
May 5: 8002 - 9001
```

Google Sheet van ghi dung dong vi app ghi theo `source_row`.

## Luu y

Chi su dung voi noi dung ban co quyen tai/luu. YouTube cookie va rate-limit phu thuoc vao IP, tai khoan va toc do chay cua tung may.
