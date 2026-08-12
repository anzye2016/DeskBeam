# DeskBeam

基于浏览器的 Windows 桌面串流与远程遥控。手机/电脑浏览器即可控制 Windows 主机：触控板、键盘、文字输入、语音转文字，以及 GPU 硬件编码的实时桌面串流。

支持 Python 源码运行，或编译为单文件 exe 免安装部署。

## 功能特性

| 功能 | 说明 |
|------|------|
| 🖥️ **桌面串流** | GPU 硬件 H.264 编码（NVENC → QSV → libx264 自动降级），浏览器 VideoDecoder 解码，低延迟 |
| 🖱️ **触控板遥控** | 手机当触控板：滑动移鼠标、双指滚动、左/右键、滚轮 |
| ⌨️ **快捷键** | Ctrl+C/V、Alt+Tab、Win、F5、方向键等，支持多指同时按住 |
| 📝 **文字输入** | 浏览器输入框 → 电脑键盘输入 |
| 🎙️ **语音转文字** | 按住说话自动识别为文字发送（在线 API 或本地） |
| 🎮 **GYRO 激光笔** | 手机陀螺仪当激光笔，晃动机身移动光标，PPT 演示神器，支持轴切换 |
| 📊 **延迟监控** | 标题栏实时显示端到端延迟，严重堆积自动重连释放 |
| 🔐 **安全** | TLS 加密 + 口令认证 + 登录限流 + 审计日志 |

## 环境要求

| 条件 | 完整版 |
|------|--------|
| Windows 10+ | ✅ |
| Python 3.10+（源码运行） | ✅ |
| GPU（NVENC/QSV/AMF） | 推荐（无 GPU 自动软编，画面流畅度下降） |
| Chromium 浏览器 94+（Chrome/Edge） | 推荐（Firefox/Safari 走 WebRTC 降级） |

## 快速开始（源码运行）

```powershell
git clone https://github.com/anzye2016/DeskBeam.git
cd DeskBeam

copy config.example.json config.json   # 首次需生成配置
start.bat                                # 自动装依赖 + 生成证书 + 启动
```

`start.bat` 自动完成：创建虚拟环境 → 安装依赖 → 生成自签名证书 → 启动服务。

手动安装等效步骤：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
python certgen.py                        # 自动选择 openssl 或 Python 生成证书
copy config.example.json config.json
.venv\Scripts\python server.py
```

**防火墙放行**（局域网访问）：

```powershell
New-NetFirewallRule -Name "DeskBeam" -DisplayName "DeskBeam" -Enabled True `
  -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8769
```

然后手机/电脑浏览器打开：

```
https://<Windows主机局域网IP>:8769
```

首次访问浏览器会提示**证书警告**（自签名证书），点击"继续访问"即可。

> **无证书模式**：若 `cert.pem`/`key.pem` 不存在，自动降级为 HTTP（浏览器无法使用麦克风录音）。

## 免 Python 部署（exe）

目标机**不需要装 Python**。跑 `build.bat` 生成 `DeskBeam.exe`（PyInstaller onefile，前端 `web/` 已打进 exe），然后复制这些文件：

```
DeskBeam.exe            ← 已内置 web/ 前端，无需再拷 web/
config.json             ← 按 config.example.json 填好 token 等
cert.pem + key.pem      ← TLS 证书，两个文件缺一不可（或用 certgen.py 生成）
ffmpeg\ffmpeg.exe       ← GPU 串流用；不带则自动降级 CPU dxcam 软编
```

- `web/`、源码 `.py`、`requirements.txt` 都不用拷（已打进 exe）
- ffmpeg 目录只需 `ffmpeg.exe`（ffplay/ffprobe 可省）；也可在 config.json 配 `ffmpeg_url` 让 exe 首次运行自动下载
- exe 为 `--uac-admin`，首次运行会弹 **UAC 提权**（UAC 快捷键、键盘注入需要管理员权限）

## 三种模式

### REMOTE — 触控板遥控

默认模式，无画面，纯控制。

- **触摸板**：手指滑动移动鼠标，双指缩放（SCREEN 模式下）
- **鼠标按钮**：L（左键）、R（右键）、滚轮、退格、Enter
- **快捷键栏**：Ctrl+C/V、Alt+Tab、Win、F5 等，支持多指同时按住
- **文字输入**：底部输入框 → Send 发送到电脑
- **语音**：按住 REC 说话 → 自动转文字发送

### SCREEN — 桌面串流

实时画面 + 叠加遥控。

- 硬件 H.264 编码串流（GPU 管线），浏览器 VideoDecoder 解码（无 GPU 时 WebRTC 降级）
- 缩放档位：Fit / 1.5x / 2x / 3x / Full
- 标题栏显示 `LIVE <fps>fps <ms>ms`：端到端累积延迟，持续超限自动重连释放
- 触摸板 + 快捷键 + 文字语音输入（同 REMOTE）

### GYRO — 陀螺仪激光笔

手机陀螺仪当激光笔。

- 校准、灵敏度滑条实时调节
- 轴切换按钮（`L`/`P`）手动切换横/竖屏轴映射，旋转设备自动复位
- 文字/语音输入同 REMOTE

## 登录 / 登出

点击左上角状态文字（`LIVE` / `RETRY` / `CONNECTING`）弹出操作面板：

- **Logout**：退出登录，清除 cookie
- **Shutdown**：关闭 DeskBeam 进程
- **Cancel**：取消

## 后台运行

| 脚本 | 用途 |
|------|------|
| `start.bat` | 调试用（有 CMD 窗口，自动提权） |
| `start.vbs` | 隐藏窗口 + 提权启动 |
| `stop.bat` | 停止服务 |

## 配置

配置文件 `config.json`（由 `config.example.json` 复制而来）。

```json
{
    "port": 8769,
    "ssl_cert": "cert.pem",
    "ssl_key": "key.pem",
    "web_dir": "web",
    "token": "",
    "streaming": true,
    "max_fps": 25,
    "max_fps_lan": 25,
    "gop": 15,
    "cq": 26,
    "maxrate": "3M",
    "maxrate_lan": "50M"
}
```

### 常用配置项

| 键 | 默认值 | 说明 |
|----|--------|------|
| `port` | `8769` | HTTPS/WSS 端口 |
| `token` | `""` | 认证口令，空则跳过认证 |
| `streaming` | `true` | 启用桌面串流 |
| `max_fps` / `max_fps_lan` | `25` | 公网 / 局域网最大帧率 |
| `gop` | `15` | 关键帧间隔（GOP） |
| `cq` | `26` | 编码质量（越小越清晰，带宽越大） |
| `maxrate` / `bufsize` | `3M`/`6M` | 公网码率限制；`*_lan` 对应局域网 |
| `soft_width` / `soft_height` | `1920x1080` | 软件编码路径的输出分辨率 |
| `ffmpeg_url` | `""` | 配置后自动下载 ffmpeg（GPU 串流） |
| `asr_*` | — | 语音识别配置 |
| `esp_*` | — | ESP32 HID 中继配置（可选硬件扩展） |

## 浏览器兼容性

| 浏览器 | 遥控 | 串流 |
|--------|:---:|:---:|
| Chrome / Edge 94+ | ✅ | ✅ VideoDecoder（GPU 管线） |
| Firefox | ✅ | ✅ WebRTC 降级（无 VideoDecoder，会提示） |
| Safari / iOS | ✅ | ✅ WebRTC 降级 |

## 架构

```
浏览器                                      Python 服务端
┌──────────────────────┐               ┌─────────────────────────┐
│ canvas + VideoDecoder │◄── WSS (H.264)│ ffmpeg ddagrab → NVENC │
│ 触控板 / 鼠标        │──► WSS JSON   │  （自动降级 QSV/libx264）│
│ 快捷键               │   (控制)      │ dxcam → PyAV（软编兜底）│
│ 文字输入 / 语音      │──► WSS (WAV)  │ SendInput（键盘/鼠标）  │
└──────────────────────┘               │ speech（语音识别）      │
                                       └─────────────────────────┘
```

### 模块

| 文件 | 职责 |
|------|------|
| `server.py` | 主服务：HTTP/WSS、认证、串流、遥控命令、静态资源 |
| `capture.py` | 屏幕采集（dxcam，软件降级路径 + WebRTC 兜底） |
| `speech.py` | 语音转文字（在线 API / WSL 本地） |
| `sendinput.py` | 输入注入（SendInput）：键盘/鼠标/文字 |
| `gpu_stream.py` | GPU 串流：封装 ffmpeg ddagrab→NVENC/QSV，编码器自动降级 |
| `encoder.py` | H.264 编码器（PyAV，软编路径，硬件不可用时验证降级） |
| `webrtc_streamer.py` | WebRTC 串流降级（无 VideoDecoder 浏览器） |
| `asr.py` | 语音转文字 CLI（WSL 本地模型转发） |
| `web/` | 前端：`index.html` + `style.css` + `app.js` + `login.html` |

## 安全

| 层级 | 机制 |
|------|------|
| 传输 | TLS 1.2+ WSS 加密 |
| 认证 | Cookie 口令（HttpOnly + SameSite=Strict），5 次失败封锁 24h |
| 会话 | 服务端 24 小时过期 |
| 登录限流 | 按真实客户端 IP 限流 |
| 路径遍历 | `relative_to()` 沙箱防越权 |
| 审计 | `audit.log` 记录登录/登出/连接 |
| 密钥 | config.json / cert.pem / key.pem 已 gitignore |

自签名证书加密流量但浏览器无法验证身份，首次访问提示证书警告属正常现象。

## 文件结构

```
DeskBeam/
├── server.py              # 主服务
├── capture.py             # 屏幕采集（dxcam）
├── speech.py              # 语音转文字
├── sendinput.py           # 输入注入（SendInput）
├── gpu_stream.py          # GPU 串流（ffmpeg ddagrab → NVENC/QSV）
├── encoder.py             # H.264 编码器（PyAV，软编路径）
├── webrtc_streamer.py     # WebRTC 串流降级
├── asr.py                 # 语音转文字 CLI（WSL）
├── certgen.py             # 自签名证书生成
├── build.bat              # PyInstaller 打包为 DeskBeam.exe
├── requirements.txt       # 依赖清单
├── config.example.json    # 配置模板
├── start.bat / start.vbs  # 启动脚本
├── stop.bat               # 停止脚本
├── icon.ico / icon.png    # 图标
├── LICENSE                # MIT 许可证
├── docs/                  # 截图
└── web/
    ├── index.html         # 前端界面（REMOTE/SCREEN/GYRO）
    ├── style.css          # 前端样式
    ├── app.js             # 前端逻辑
    └── login.html         # 登录页
```

## 许可证

MIT — 详见 [LICENSE](LICENSE)
