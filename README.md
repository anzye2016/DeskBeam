# DeskBeam

基于浏览器的 Windows 桌面串流与远程遥控。

两种部署模式：
- **完整版**: 桌面串流（H.264） + 远程控制（鼠标/键盘/文字/语音）
- **纯遥控**: 鼠标/键盘/文字/语音 — 无需 GPU

硬件 H.264 编码（NVENC/QSV/AMF），触控板/鼠标/键盘/文字输入/语音转文字。支持 Python 源码运行或编译为单文件 exe。

<p align="center">
  <img src="docs/screen.jpg" width="45%" alt="SCREEN 模式">
  <img src="docs/remote.jpg" width="45%" alt="REMOTE 模式">
</p>

---

## 1. 环境要求

| 条件 | 完整版 | 纯遥控 |
|------|:---:|:---:|
| Windows 10+ | 需要 | 需要 |
| Python 3.10+ | 需要 | 需要 |
| GPU（NVENC/AMF/QSV） | 推荐 | 不需要 |
| Chromium 浏览器 94+ | 需要 | 推荐 |
| WSL（语音识别） | 可选 | 可选 |
| openssl（TLS 证书） | 需要 | 需要 |

---

## 2. 完整版安装

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 生成自签名证书
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"

copy config.example.json config.json
.venv\Scripts\python server.py
```

> **防火墙**：Windows 可能阻止入站连接，执行以下命令放行局域网访问：
> ```powershell
> New-NetFirewallRule -Name "DeskBeam" -DisplayName "DeskBeam" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 8769
> ```

Chrome/Edge 打开 `https://<局域网IP>:8769`，接受证书警告即可。

> **无证书模式**：若 `cert.pem` 和 `key.pem` 不存在，自动降级为 HTTP（浏览器无法使用麦克风）。

---

## 3. 纯遥控安装

### 方式 A：Python 源码

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-remote.txt
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=localhost"
copy config.example.json config.json
.venv\Scripts\python server_remote.py
```

### 方式 B：单文件 exe

编译为单个 `DeskBeamRemote.exe`，目标机器无需安装 Python：

```powershell
build_remote.bat
```

部署只需 4 个文件：

```
├── DeskBeamRemote.exe     # 双击启动
├── config.json             # 部署前修改
├── cert.pem                # 用 openssl 生成
└── key.pem
```

后台运行无窗口。退出方式：
- `stop.bat`（双击）
- 任务管理器结束 `DeskBeamRemote.exe`
- 浏览器点击状态文字（`LIVE`）→ 选择 `Shutdown` 关闭程序

---

## 4. 登录 / 登出

点击左上角状态文字（`LIVE` / `RETRY` / `CONNECTING`）弹出操作面板：

- **Logout**：退出登录，清除 cookie，跳转登录页
- **Shutdown**：关闭 DeskBeam 进程
- **Cancel**：取消

---

## 5. 后台运行

```powershell
start.vbs      # 隐藏窗口 + 提权启动
start.bat      # 调试用（有 CMD 窗口）
stop.bat       # 停止服务
```

---

## 6. 配置

```json
{
    "port": 8769,
    "ssl_cert": "cert.pem",
    "ssl_key": "key.pem",
    "web_dir": "web",
    "token": "",
    "max_fps": 3,
    "streaming": true,
    "gop": 1,
    "wsl_asr_script": "~/scripts/asr.py",
    "asr_health_url": "http://127.0.0.1:8082/healthz",
    "asr_cooldown": 10
}
```

| 键 | 类型 | 默认值 | 说明 |
|---|------|-------|------|
| `port` | int | 8769 | HTTPS/WSS 端口 |
| `token` | str | `""` | 认证口令，空则跳过认证 |
| `max_fps` | int | 3 | 最大帧率 |
| `streaming` | bool | true | 启用桌面串流 |
| `gop` | int | 1 | 关键帧间隔 |
| `wsl_asr_script` | str | `~/scripts/asr.py` | WSL 内 ASR 脚本路径 |
| `asr_health_url` | str | `http://127.0.0.1:8082/healthz` | ASR 健康检查地址 |
| `asr_cooldown` | int | 10 | ASR 重试间隔（秒） |

---

## 7. 浏览器兼容性

| 浏览器 | 远程遥控 | 屏幕串流 |
|--------|:---:|:---:|
| Chrome / Edge 94+ | ✅ | ✅ VideoDecoder |
| Opera / Brave | ✅ | ✅ VideoDecoder |
| Firefox | ✅ | ✅ WebRTC 降级 |
| Safari | ✅ | ✅ WebRTC 降级 |
| iOS 浏览器 | ✅ | ✅ WebRTC 降级 |

---

## 8. GOP 调优

静态桌面带宽参考（2560×1440, CQ=26）：

| `gop` | 关键帧间隔 | 带宽 | 适用场景 |
|-------|----------|------|---------|
| 1 | 每帧 | ~23 Mbps | 局域网零延迟 |
| 15 | 每 0.5s（30fps） | ~6 Mbps | 云服务器 15M |
| 30 | 每 1s（30fps） | ~5 Mbps | 云服务器 30M |
| 60 | 每 2s（30fps） | ~3 Mbps | 小带宽 3M |

静态内容 P 帧仅 ~200 字节。

---

## 9. WebRTC 降级

当浏览器不支持 `VideoDecoder` API（Firefox/Safari）时自动切换 WebRTC。无需额外配置，需要安装 `aiortc`：

```powershell
.venv\Scripts\pip install aiortc
```

WebRTC 通过浏览器原生 H.264 解码器播放画面，与 VideoDecoder 共享同一套 mss 截图 + 编码管线。

---

## 10. 语音识别（可选）

语音识别依赖 WSL（Windows Subsystem for Linux）和一个运行的 ASR 模型服务（默认端口 8082）。项目自带 `asr.py` 脚本用于转发音频。

### 快速开始

```bash
# 在 WSL 中部署 asr.py
cp asr.py ~/scripts/
# 启动 ASR 模型服务（需自行部署）
# 例如 Qwen3-ASR-0.6B 服务在 127.0.0.1:8082
```

DeskBeam 发送录音后，ASR 服务返回文字，自动输入到当前聚焦的窗口。

---

## 11. 架构

```
浏览器                                      Python 服务端
┌──────────────────────┐               ┌─────────────────────────┐
│ canvas + VideoDecoder │◄── WSS (H.264)│ mss → 屏幕捕获          │
│ 触控板 / 鼠标        │──► WSS JSON   │ numpy → 光标叠加        │
│ 快捷键               │   (控制)      │ PyAV → H.264 编码       │
│ 文字输入 / 语音      │──► WSS (WAV)  │   NVENC/QSV/AMF/x264   │
└──────────────────────┘               │ keyboard / ctypes       │
                                       │ WSL → ASR（语音识别）   │
                                       └─────────────────────────┘
```

---

## 12. 安全

| 层级 | 机制 |
|------|------|
| 传输 | TLS 1.2+ WSS 加密 |
| 认证 | Cookie 口令（HttpOnly + SameSite=Strict），5 次失败封锁 24 小时 |
| 会话 | 服务端 24 小时过期 |
| 路径遍历 | `relative_to()` 沙箱 |
| 审计 | `audit.log` 记录登录/登出/连接事件 |
| 密钥 | `config.json`、`cert.pem`、`key.pem`、`*.ps1`、`audit.log` 已 gitignore |

### 自签名证书

加密流量但浏览器无法验证身份，首次访问会提示证书警告——属正常现象。

### ARP 欺骗风险

局域网内的攻击者可重定向流量并伪造证书，实现中间人攻击。

**防御措施：**
1. 生成证书后比对指纹：`openssl x509 -in cert.pem -noout -sha256 -fingerprint`
2. 使用真实域名 + Let's Encrypt 签发证书
3. 静态 ARP 绑定：`arp -s <网关IP> <网关MAC>`
4. 不在不受信任的网络暴露

---

## 13. 文件结构

```
├── server.py              # 主服务（完整版）
├── server_remote.py       # 纯遥控版（用于构建 exe）
├── asr.py                 # 语音转文字脚本（WSL，需 ASR 模型服务）
├── encoder.py             # H.264 编码器（PyAV）
├── requirements.txt       # 完整版依赖
├── requirements-remote.txt
├── build_remote.bat       # PyInstaller 构建脚本
├── config.example.json
├── start.vbs / start.bat  # 启动脚本
├── stop.bat               # 停止脚本
├── icon.ico / icon.png    # 图标
├── .gitignore
├── LICENSE
├── docs/                  # 截图
└── web/
    ├── index.html         # 前端界面
    └── login.html         # 登录页
```

---

## 14. 免责声明

本软件按"原样"提供，不提供任何明示或暗示的担保。配置不当（弱口令、不受信任的网络、密钥泄露）可能导致未授权访问、数据泄露或其他损失。作者不承担任何责任，使用前请自行评估风险。

远程桌面软件涉及对被控计算机的完全访问权限。请仅在本机或您拥有合法授权的设备上使用本软件。使用本软件应遵守当地法律法规。详情见 [MIT 许可证](LICENSE)。

---

## 15. 许可证

MIT — 详见 [LICENSE](LICENSE)
