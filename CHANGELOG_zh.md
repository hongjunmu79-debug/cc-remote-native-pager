# 更新记录

[English](CHANGELOG.md)

## v3.0.0 — 2026-07-24

cc-remote v3 在原有 Claude Code + Codex 远程控制面之上新增隔离的 Cowork 风格
Work 工作台，并重新设计历史恢复、原生客户端协同、多机器路由、移动端可靠性和
发布运维。

### Code 与 Work

- 为 Claude 和 Codex 新增彼此独立的 Code / Work 双空间；会话列表、焦点、目录、
  基础提示词、权限和恢复状态均分开管理。
- 新增按引擎隔离的 Work 项目、文件/链接/笔记资料库、可复用工作模板，以及创建
  工作时物化到私有目录的上下文。
- 新增一次性、每日和每周定时任务；运行记录、租约、心跳、失败重试与防重叠状态
  持久化保存。
- 每项 Work 只能访问注册表确认属于它的私有目录；外部资料必须通过附件或项目
  资料库显式加入。
- 自动列出 Work 产生的 Artifacts，并在本机预览源码、Markdown、安全清理后的
  HTML、图片、PDF 及沙箱临时转换的 Office 文档。

### 会话、控制与扩展

- 补齐可靠的删除、重命名、归档、消息级派生、临时侧聊、排队、打断和后台会话
  控制，避免后台响应抢走当前焦点。
- 接入 Codex 原生 compact、Review 和独立 Git worktree 派生。尚未完成的 Codex
  Rollback 与 Claude Rewind 不对用户开放。
- 模型、思考强度、服务档位、协作/Plan 模式、权限、上下文、目标、状态、用量和
  限额均绑定当前会话。
- 新增真实 Skills、Plugins、Apps、MCP 和 Hooks 目录。Code 可在引擎支持时管理
  Skills、插件和 Claude Hooks；Codex Hooks 与 Work 中的全部扩展保持只读。
- 将 Claude 工具审批，以及 Codex 命令、文件修改、用户输入、通用权限和 MCP
  elicitation 请求回传给当前控制浏览器。

### 本地优先历史

- 网络校验前先绘制浏览器 IndexedDB 中最近一次验证的本地投影。
- wrapper 使用可重建的 SQLite 索引物化与源文件指纹绑定的回合摘要。
- 优先加载最新回合；工具输出、reasoning 和超长正文只在展开对应回合时按需获取。
- 向上翻页加载更早历史时保持当前阅读锚点，并在后台收敛追加中的源文件。
- 历史图片按需读取，不再嵌入每一个历史分页。

### Codex 超长会话与原生命周期

- 按回合从 rollout 尾部向前读取 Codex 历史，不把历史重新上传给模型，也不替换
  app-server 原生 resume 与 compact 状态。
- 对 Codex Desktop + OpenAI 的特定超大恢复场景，增加严格限定的官方 HTTP 传输
  兜底，处理 WebSocket 在完成前关闭的问题。
- 区分 Codex shared-daemon CLI 活动与私有 Codex App 所有权。
- 将 prompt、steer、commentary、工具、compact、abort 和 completion 绑定到权威
  turn，避免历史内容漂移到会话末尾。
- 正确镜像被打断和外部正在运行的工作，不留下错误只读锁或永久“思考中”状态。

### 设备与所有权

- 新增 Device Center、会过期的一次性配对码、哈希保存的机器凭据、重命名/撤销和
  在线状态。
- 新增可选多用户策略，把每个账号限制到明确允许的 wrapper 机器。
- 在设备发现、命令、事件和 Push 订阅上执行账号到机器的授权检查。
- 按设备、Code/Work、引擎、WebSocket generation 和会话归属隔离工作目录及延迟
  focus/rekey 事件。
- 在 Darwin/Linux 上共享精确进程身份扫描；Claude 接管只处理同一用户且精确匹配
  的进程。
- 新增按用户和机器隔离的 Web Push；后台通知只包含完成/失败状态，不携带对话正文。

### 移动端与 Artifact 体验

- 稳定向上分页、本地优先会话切换和有界实时尾巴补流。
- 历史图片按需加载；触屏灯箱支持点击关闭和双指缩放。
- 支持多图附件、稳定的待发送图片预览，以及跨会话和引擎切换保留各自输入草稿。
- Markdown 相对链接/图片、源码、安全 HTML、PDF 和 Office 沙箱预览均留在 wrapper
  的本地安全边界内。
- 更新 PWA 和通知资源，修复窄屏弹层、过程时间线及无法关闭的错误提示。
- 让运行标志保持在排队/打断控件上方，保留 Claude 回合耗时，并将重复工具活动紧凑
  展示且不隐藏最终答复。

### 发布与运维

- Python、Codex `clientInfo`、Web package metadata 和公开构建清单统一为产品版本
  `3.0.0`。
- 严格 wire gate 升级为 protocol v19。
- 为 Linux x86_64、Linux arm64、macOS Intel 和 macOS Apple Silicon 发布可复现、
  带校验和及 GitHub artifact attestation 的 Relay/Wrapper 安装包。
- 新增校验后的角色引导程序、托管 Python 3.13 环境、macOS LaunchAgent 安装器和
  Linux Wrapper systemd 安装器；设备凭据始终放在不可变 release 与服务定义之外。
- staging 或激活 release 前同时校验产品版本和协议版本。
- VPS 使用不可变 release、release 内独立虚拟环境、原子切换、就绪检查和失败回滚。

### 升级注意事项

- v3.0.0 使用 wire protocol v19。Wrapper、Relay 和 Web 必须一起升级；混用协议
  版本会被拒绝。
- 部署后对已打开的浏览器页面执行硬刷新，使其加载 v3 哈希资源，并按 protocol v19
  重建本地投影。
- 运行密钥和机器状态必须放在 release 目录之外；不要覆盖 `.env`、`~/.cc-remote`、
  Claude transcripts 或 Codex rollouts。
- Claude 集成继续固定为 `claude-agent-sdk==0.2.119`。
- 浏览历史仍然只是本地读取，不会 resume Claude/Codex，也不会创建模型回合。
