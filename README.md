# 本地模型翻译器

中文 | [English](README.en.md)

这是一个尝试性的作品，它无法比拟微信自带的截屏翻译功能，调用本地模型也是一个华而不实的设计，唯一能够拿得出手的只是完全离线和稍长文本的翻译，而这两个功能取决于你的本地模型，很明显，这个作品没有达到我的预期，只能算个小玩具，不过还是欢迎你的使用。

这是这个小作品的最终版本，目前没有继续改进的计划。

程序名为 **LocalLens**，在 Windows 上通过 Ollama 调用本地 AI 模型翻译，用 RapidOCR 识别截图文字。提前装好模型后，就可以离线使用。

## 界面

![主窗口](docs/images/main-window.png)

![翻译小窗口](docs/images/translation-example.png)

翻译小窗口使用圆角半透明背景。

## 开始使用

1. 准备 64 位 Windows 10 / 11，安装并启动 [Ollama](https://ollama.com/)，下载你想用的模型。
2. 打包版解压整个文件夹后，运行 `LocalLens.exe`，不用安装 Python。GitHub 源码 ZIP 请按下方步骤运行。
3. 在 Settings 里选好模型并保存。运行 `ollama list` 可查看模型名称；默认的 `deepseek-r1:8b` 可以换成自己的模型（请一定保证自己的电脑部署了本地模型！）。Ollama 地址通常保持 `http://127.0.0.1:11434` 即可。

## 翻译

- **选中文字：** 长按左键并滑动选中文本，然后按 `Alt+T`。
- **截图：** 按 `Alt+Q`，框选文字；按 `Esc` 取消。
- **小窗口：** 可以拖动，点 `Copy` 复制译文。
- **语言：** 英文译成中文，中文译成英文；中英混合直接显示原文，不调用模型。
- **排版：** 截图文字会合成一行，保留识别到的标点。长文本会分段翻译，再按顺序拼起来。

每次翻译独立进行，只显示译文。快捷键和模型可在 Settings 修改。默认翻译后释放模型，下次加载可能需要多等一会儿。

尽量不要与游戏同时运行，你也知道本地模型运行需要占用电脑资源的。

## 启动和退出

打包版首次运行后会添加开始菜单入口，默认登录 Windows 后后台启动，不创建桌面图标。直接用快捷键即可；左键点托盘图标打开主窗口。

关闭主窗口后仍在后台运行，托盘菜单可以彻底退出。需要重开时，按 Win 键搜索 `LocalLens`。Settings 中的 `Sign-in startup` 可以关闭自动启动。移动程序文件夹后，手动运行一次以更新路径。

## 小提醒

- 复杂背景、小字和模糊图片可能识别不准；漏识别的标点不会自动补上。
- 某些全屏游戏、受保护画面可能截不到。管理员程序中选字失败时，可以试试截图。
- 多显示器和不同缩放比例可能有兼容问题。
- 默认不上传正文或截图，不保存翻译历史。模型需要提前下载。
- 设置在 `config/settings.json`；日志在 `logs/locallens.log`，自动滚动，不记录原文和译文。开启 Debug 才会保存调试截图。

## 源码运行与打包

在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

测试用 `.\.venv\Scripts\python.exe -m pytest -q`。需要固定依赖版本时，用 `requirements-lock.txt` 替代 `requirements.txt`。

打包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1
```

分享打包版时保留完整的 `dist\LocalLens` 文件夹，不要只复制 EXE。

## 许可证

[MIT](LICENSE)，欢迎使用、研究或修改。
