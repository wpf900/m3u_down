# 流影

跨平台 HLS（m3u8）下载桌面应用。粘贴链接后多线程拉取分片，再用内置 ffmpeg 合成 MP4。

支持 **Windows x64**、**macOS Apple 芯片**、**macOS Intel**。

## 下载安装包

仓库是私有的，需要登录 GitHub 账号（且有本仓库权限）才能下载。

1. 打开 [Actions → Build](https://github.com/wpf900/m3u_down/actions/workflows/build.yml)
2. 点右上角 **Run workflow**，分支选 `main`，再点绿色 **Run workflow**
3. 等三个任务都变成绿色（大约 5–10 分钟）
4. 点进这次运行，拉到页面底部 **Artifacts**，下载对应压缩包：

| 文件 | 适用系统 |
|---|---|
| `Liuying-windows-x64` | Windows 10 / 11（64 位） |
| `Liuying-macos-arm64` | Apple 芯片 Mac（M1 / M2 / M3 / M4） |
| `Liuying-macos-intel` | Intel Mac |

Artifacts 默认保留 90 天。也可以打 git tag（例如 `v1.0.0`）并 push，同样会触发打包。

### Windows 怎么用

1. 解压 zip，得到 `Liuying` 文件夹
2. 双击 `Liuying.exe`（请保留整个文件夹，不要只拷贝 exe）
3. 若提示缺少 WebView2，安装 [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win10/11 一般都已自带）

### Mac 怎么用

1. 解压 zip，得到 `Liuying.app`
2. 拖到「应用程序」或直接双击
3. 未做 Apple 开发者签名。若提示「已损坏」或无法打开：

```bash
xattr -cr /path/to/Liuying.app
```

然后对图标 **右键 → 打开**。

安装包已内置 ffmpeg，用户不必再装 Python 或 ffmpeg。

## 使用方法

1. 选择保存目录（默认：`下载/流影`）
2. 粘贴 m3u8 链接，点 **开始下载**
3. 多集可填剧名，会自动建子文件夹

链接格式示例：

```
https://example.com/ep01.m3u8

第01集$https://example.com/ep01.m3u8
第02集$https://example.com/ep02.m3u8
```

- **同时**：并行下载的剧集数（1–8）
- **线程**：单集分片并发数（1–64）
- **请求头**：需要防盗链时填写 Referer / User-Agent

## 本地开发

需要 Python 3.12+。Mac 开发时系统里有 ffmpeg 更方便（打包时会再下载一份静态 ffmpeg）。

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## 本地打包

必须在对应系统上打包，不能在 Mac 上打出 Windows exe。

```bash
pip install -r requirements.txt -r requirements-build.txt
python scripts/build.py
```

| 系统 | 产物 |
|---|---|
| macOS | `dist/Liuying.app` |
| Windows | `dist/Liuying/Liuying.exe` |

脚本会自动生成图标、下载对应平台的静态 ffmpeg，再调用 PyInstaller。

## 项目结构

```
app.py                 窗口与设置
downloader.py          HLS 解析、分片下载、ffmpeg 合并
web/                   界面
scripts/build.py       当前系统一键打包
.github/workflows/     GitHub Actions 云端打包
```

配置文件位置：

- macOS：`~/Library/Application Support/Liuying/settings.json`
- Windows：`%APPDATA%\Liuying\settings.json`
