# AstrBot JMComic Bot

在 AstrBot 中使用 JMComic-Crawler-Python 查询、智能搜索、下载 JM 内容，并导出 PDF 发送到聊天。

## 功能

- `jm 查询 <id>` 查询本子信息。
- `jm 搜索 <关键词>` 搜索本子并展示结果。
- `jm <id>` 直接下载、压缩为 PDF 并发送。
- 支持自然语言搜索，群聊可配置为只有 @ 机器人时触发。
- 支持 LLM 生成智能检索计划，例如按观看/喜欢排序、作者检索、短篇约束、排除连载等。
- 支持 PDF 压缩、PDF 随机数字密码加密、发送后自动清理本地文件。
- 支持 AstrBot 统一会话 ID，把 PDF 额外发送到其他协议/会话。
- 针对 OneBot/NapCat 上传超时做了兼容：上传接口超时时不直接提示失败，并延迟删除本地 PDF。

## 安装

在 AstrBot 插件目录中安装：

```bash
git clone https://github.com/aboutbalnk/astrbot_plugin_jmcomic_bot.git
```

然后在 AstrBot WebUI 中安装依赖或在容器内执行：

```bash
pip install -r requirements.txt
```

如果使用 Docker 部署 AstrBot，请在 AstrBot 容器内安装依赖。

## LLM 前提

智能搜索依赖 AstrBot 已经配置并启用了可用的 LLM Provider。插件会调用 AstrBot 当前会话的模型生成搜索计划。

如果没有配置 LLM，或者模型调用失败，插件会自动降级为规则搜索；基础功能仍可使用，包括：

```text
jm 查询 <id>
jm 搜索 <关键词>
jm <id>
```

## 命令

```text
jm 查询 <id>
jm 搜索 <关键词>
jm <id>
jm 章节 <photo_id>
jm 第一个
```

`jm 第一个` 会下载最近一次搜索结果中的第一个条目。

## 关键配置

- `download_dir`: JM 图片临时下载目录。
- `pdf_dir`: PDF 输出目录。
- `compress_temp_dir`: 压缩 PDF 时的临时目录。
- `runtime_dir`: 插件生成 jmcomic 运行时配置文件的目录。
- `max_send_mb`: PDF 目标大小。
- `pdf_compress_profiles`: PDF 压缩档位，格式如 `1600:68,1280:58,1000:48`。
- `encrypt_pdf`: 是否发送前加密 PDF。
- `pdf_password_digits`: 随机数字密码位数。
- `cleanup_download_images`: 生成 PDF 后是否清理图片目录。
- `send_to_current_session`: 是否发送回触发命令的当前会话。
- `target_sessions`: 额外发送目标，使用 AstrBot 统一会话 ID。
- `notify_password_to_target_sessions`: 是否向额外目标发送 PDF 密码。
- `enable_natural_language`: 是否开启自然语言搜索/下载。
- `group_natural_language_require_at`: 群聊自然语言是否需要 @ 机器人。

`enable_natural_language` 和更复杂的智能检索效果依赖 AstrBot LLM Provider。未配置 LLM 时，插件会使用内置规则做基础搜索。

额外发送目标示例：

```text
default:GroupMessage:123456789
default:FriendMessage:123456789
group:123456789
friend:123456789
```

完整格式推荐使用 `平台ID:消息类型:会话ID`，例如 `default:GroupMessage:123456789`。`group:` 和 `friend:` 是当前平台下的简写。

## 目录配置

默认目录按 AstrBot Docker 容器内路径设置：

```text
/AstrBot/data/jmcomic
/AstrBot/data/jmcomic_pdf
/AstrBot/data/jmcomic_compress
/AstrBot/data/plugin_data/astrbot_plugin_jmcomic
```

如果你使用 Docker 部署 AstrBot，通常保持默认即可。路径是容器内路径，不是宿主机路径；需要查看文件时，请确认 Docker volume 映射到宿主机的位置。

如果你不是 Docker 部署，或者本机没有 `/AstrBot/data` 写入权限，请在 AstrBot WebUI 中把这些配置改成 AstrBot 进程可写目录，例如：

```text
download_dir: ./data/jmcomic
pdf_dir: ./data/jmcomic_pdf
compress_temp_dir: ./data/jmcomic_compress
runtime_dir: ./data/plugin_data/astrbot_plugin_jmcomic
```

## 注意

- 本插件会下载并发送 NSFW 内容，请遵守你所在地区法律法规和聊天平台规则。
- PDF 文件较大时，OneBot/NapCat 可能出现 WebSocket API timeout。该情况不一定代表文件发送失败，插件会延迟清理本地 PDF。
- 额外目标会话是否支持文件组件取决于对应 AstrBot 平台适配器。

## 第三方项目

本插件使用并随包附带 `jmcomic` 模块，来源于 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)，其许可证为 MIT。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
