# 大字报视频 FFmpeg 云端渲染接口

这个项目用于配合扣子工作流：扣子生成大字报字幕 JSON，本项目负责把“用户上传的视频 + 字幕 JSON”合成成 MP4，并返回视频链接。

## 一、接口地址

部署后：

```text
https://你的域名/render
```

填到扣子：

```text
render_api_url = https://你的域名/render
render_api_key = 123456
```

## 二、部署到 Render

1. 新建 GitHub 仓库，把本文件夹全部上传。
2. 打开 Render，选择 New Web Service。
3. 连接你的 GitHub 仓库。
4. Render 检测到 Dockerfile 后会用 Docker 构建。
5. 环境变量设置：

```text
RENDER_API_KEY = 123456
MAX_INPUT_MB = 300
FFMPEG_PRESET = veryfast
FFMPEG_CRF = 23
```

6. 部署完成后，打开：

```text
https://你的Render域名/health
```

看到 `ok: true` 就说明接口正常。

## 三、部署到 Railway

1. 新建 GitHub 仓库，把本文件夹全部上传。
2. 打开 Railway，New Project，选择 Deploy from GitHub Repo。
3. 选择你的仓库。
4. Railway 会检测根目录 Dockerfile 并用它构建。
5. Variables 里设置：

```text
RENDER_API_KEY = 123456
MAX_INPUT_MB = 300
FFMPEG_PRESET = veryfast
FFMPEG_CRF = 23
```

6. 给服务生成 Public Domain。
7. 打开：

```text
https://你的Railway域名/health
```

看到 `ok: true` 就说明接口正常。

## 四、请求格式

扣子会向 `/render` POST 这样的 JSON：

```json
{
  "video_url": "https://example.com/input.mp4",
  "title": "考公大字报视频",
  "duration": 7,
  "ratio": "9:16",
  "background_blur": true,
  "font": {
    "size": 86,
    "stroke_color": "black",
    "stroke_width": 7,
    "position": "center"
  },
  "lines": [
    {"text": "我以为考公", "color": "yellow"},
    {"text": "靠自己就行", "color": "white"}
  ]
}
```

请求头：

```text
Authorization: Bearer 123456
```

返回：

```json
{
  "success": true,
  "video_url": "https://你的域名/outputs/dazibao_xxx.mp4",
  "message": "成品视频已生成"
}
```

## 五、注意事项

1. 免费云服务可能休眠，第一次请求会慢。
2. 输出视频目前保存在服务本地磁盘，服务重启后可能丢失。
3. 如果要长期商用，建议把输出视频上传到对象存储，比如 Cloudflare R2、阿里云 OSS、腾讯云 COS、火山 TOS。
4. 扣子上传的视频 URL 必须能被这个云端服务访问，否则会下载失败。
5. 本项目不会内置任何字体文件，只在 Docker 里安装开源字体包。
