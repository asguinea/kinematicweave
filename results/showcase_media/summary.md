# Final Showcase Media

Release media generated only from accepted release figures, frozen test
values, and the verified qualitative replay sequence.

## Representative image

- Path: `figures/showcase/representative_image_3x2.jpg`
- Format and color: JPEG, RGB,
  sRGB
- Dimensions and resolution: 2400 x
  1600 pixels, 3:2, 300 dpi
- Size: 315871 bytes
- SHA-256: `e6ea77cf0a179f6cdb7fedcd7c8e7e3275d8c4b407c60e4caa1730f25ace8e73`
- MD5: `690fb794ac7d9d4084a3ac12f0901e4d`

## Website image

- Path: `figures/showcase/representative_image_web.png`
- Format and color: PNG, RGB, sRGB
- Dimensions: 1800 x 1200 pixels
- Size: 201908 bytes
- SHA-256: `9705c70f7a7bbd57fc90156b779b381eb8793eccbb7dcb80b321792b9da50051`
- MD5: `33a92d8c07d5482c693454ddb0bcf37f`

## Highlights video

- Path: `figures/showcase/highlights_video_45s.mp4`
- Format: MP4
- Duration: 45.000 seconds
- Frames: 1350 at 30/1
- Dimensions: 1920 x 1080 pixels
- Codec and pixel format: h264, yuv420p
- Size: 1951660 bytes
- SHA-256: `28859800c2dca553ee3d5ee8a813060ca63c4f4af6b3ba43a863d102225c5d94`
- MD5: `43354066ee557cd250bec5f36625fcfd`
- Encoder: `ffmpeg version 7.1-essentials_build-www.gyan.dev Copyright (c) 2000-2024 the FFmpeg developers`

## Commands

- Generation: `uv run --frozen python scripts/generate_showcase_media.py`
- Verification: `uv run --frozen python scripts/generate_showcase_media.py --verify-only`
- Assembly:

```text
'~\AppData\Local\uv\cache\archive-v0\EgOcJyda6LwDN0u4\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe' -hide_banner -loglevel error -loop 1 -framerate 30 -t 4 -i '.\cache\showcase_media\storyboard\title.png' -loop 1 -framerate 30 -t 3 -i '.\cache\showcase_media\storyboard\figure1_stage_1.png' -loop 1 -framerate 30 -t 3 -i '.\cache\showcase_media\storyboard\figure1_stage_2.png' -loop 1 -framerate 30 -t 3 -i '.\cache\showcase_media\storyboard\figure1_stage_3.png' -loop 1 -framerate 30 -t 3 -i '.\cache\showcase_media\storyboard\figure1_stage_4.png' -framerate 3 -start_number 0 -i '.\cache\showcase_media\storyboard\replay_frames\frame_%04d.png' -loop 1 -framerate 30 -t 6 -i '.\cache\showcase_media\storyboard\figure2.png' -loop 1 -framerate 30 -t 5 -i '.\cache\showcase_media\storyboard\metrics.png' -loop 1 -framerate 30 -t 6 -i '.\cache\showcase_media\storyboard\conclusion.png' -filter_complex '[0:v]trim=duration=4,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v0];[1:v]trim=duration=3,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v1];[2:v]trim=duration=3,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v2];[3:v]trim=duration=3,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v3];[4:v]trim=duration=3,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v4];[5:v]trim=duration=12,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v5];[6:v]trim=duration=6,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v6];[7:v]trim=duration=5,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v7];[8:v]trim=duration=6,setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30[v8];[v0][v1][v2][v3][v4][v5][v6][v7][v8]concat=n=9:v=1:a=0,tpad=stop_mode=clone:stop_duration=1,trim=duration=45.0,format=yuv420p[vout]' -map '[vout]' -frames:v 1350 -an -c:v libx264 -preset medium -crf 18 -g 60 -keyint_min 60 -sc_threshold 0 -threads 1 -pix_fmt yuv420p -movflags +faststart -map_metadata -1 -fflags +bitexact -flags:v +bitexact -y '.\figures\showcase\highlights_video_45s.mp4'
```

The video is silent and understandable when muted. It contains no author
identity, raw provider identifier, private path, composite score, or
universal-winner claim.
