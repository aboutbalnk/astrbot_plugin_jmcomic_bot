import asyncio
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star


PLUGIN_DIR = Path(__file__).resolve().parent
VENDOR_DIR = PLUGIN_DIR / "vendor"
OPTION_FILE = PLUGIN_DIR / "option.yml"
RUNTIME_DIR = Path("/AstrBot/data/plugin_data/astrbot_plugin_jmcomic")
DOWNLOAD_DIR = Path("/AstrBot/data/jmcomic")
PDF_DIR = Path("/AstrBot/data/jmcomic_pdf")
COMPRESS_DIR = Path("/AstrBot/data/jmcomic_compress")
MAX_SEND_BYTES = 35 * 1024 * 1024
DELETE_AFTER_TIMEOUT_SECONDS = 15 * 60
MENTION_AFTER_TIMEOUT_SECONDS = 60
PDF_COMPRESS_STEPS = ((1600, 68), (1280, 58), (1000, 48))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SEARCH_RESULT_LIMIT = 10
SEARCH_CANDIDATE_LIMIT = 30
SEARCH_PLAN_LIMIT = 6
SEARCH_PAGE_LIMIT = 3
MAX_SEARCH_PAGE_COUNT = 100
SERIAL_TAG_PATTERN = re.compile(r"(连载|連載|连载中|連載中|连载狀態|連載狀態)")
SEARCH_ORDER_MAP = {
    "latest": "mr",
    "views": "mv",
    "likes": "tf",
    "pictures": "mp",
    "mr": "mr",
    "mv": "mv",
    "tf": "tf",
    "mp": "mp",
}
SEARCH_MAIN_TAGS = {0, 1, 2, 3, 4}
SEARCH_ORDER_LABELS = {
    "mr": "最新",
    "mv": "观看",
    "tf": "喜欢",
    "mp": "页数",
}
REFERENCE_AUTHOR_PATTERN = r"(同作者|同一作者|作者[^，,。.!！?]*(?:作品|其他|其它|別的|别的|更多)|(?:其他|其它|別的|别的|更多)[^，,。.!！?]*作者)"
REFERENCE_STYLE_PATTERN = r"(同风格|同画风|同類型|同类型|同题材|类似|相似|相关)"
REFERENCE_SEARCH_PATTERN = rf"(?:{REFERENCE_AUTHOR_PATTERN}|{REFERENCE_STYLE_PATTERN})"

DEFAULT_CONFIG = {
    "download_dir": str(DOWNLOAD_DIR),
    "pdf_dir": str(PDF_DIR),
    "compress_temp_dir": str(COMPRESS_DIR),
    "runtime_dir": str(RUNTIME_DIR),
    "max_send_mb": 35.0,
    "delete_after_timeout_seconds": DELETE_AFTER_TIMEOUT_SECONDS,
    "password_notice_delay_seconds": MENTION_AFTER_TIMEOUT_SECONDS,
    "encrypt_pdf": True,
    "pdf_password_digits": 4,
    "cleanup_download_images": True,
    "show_download_start_notice": True,
    "send_to_current_session": True,
    "target_sessions": [],
    "notify_password_to_target_sessions": True,
    "pdf_compress_profiles": "1600:68,1280:58,1000:48",
    "search_result_limit": SEARCH_RESULT_LIMIT,
    "search_candidate_limit": SEARCH_CANDIDATE_LIMIT,
    "search_plan_limit": SEARCH_PLAN_LIMIT,
    "search_page_limit": SEARCH_PAGE_LIMIT,
    "max_search_page_count": MAX_SEARCH_PAGE_COUNT,
    "filter_serial_tags": True,
    "enable_natural_language": True,
    "group_natural_language_require_at": True,
    "html_domains": ["jmcomic1.me", "jmcomic.me"],
    "image_thread_count": 8,
    "photo_thread_count": 2,
    "jm_log": False,
}

if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))


class JMComicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.download_dir = Path(self._cfg_str("download_dir", DEFAULT_CONFIG["download_dir"]))
        self.pdf_dir = Path(self._cfg_str("pdf_dir", DEFAULT_CONFIG["pdf_dir"]))
        self.compress_dir = Path(self._cfg_str("compress_temp_dir", DEFAULT_CONFIG["compress_temp_dir"]))
        self.runtime_dir = Path(self._cfg_str("runtime_dir", DEFAULT_CONFIG["runtime_dir"]))
        self.runtime_option_file = self.runtime_dir / "option.runtime.yml"
        self.max_send_bytes = int(self._cfg_float("max_send_mb", DEFAULT_CONFIG["max_send_mb"]) * 1024 * 1024)
        self.delete_after_timeout_seconds = self._cfg_int(
            "delete_after_timeout_seconds",
            DEFAULT_CONFIG["delete_after_timeout_seconds"],
            minimum=0,
        )
        self.password_notice_delay_seconds = self._cfg_int(
            "password_notice_delay_seconds",
            DEFAULT_CONFIG["password_notice_delay_seconds"],
            minimum=0,
        )
        self.encrypt_pdf = self._cfg_bool("encrypt_pdf", DEFAULT_CONFIG["encrypt_pdf"])
        self.pdf_password_digits = self._cfg_int(
            "pdf_password_digits",
            DEFAULT_CONFIG["pdf_password_digits"],
            minimum=1,
            maximum=12,
        )
        self.cleanup_download_images = self._cfg_bool(
            "cleanup_download_images",
            DEFAULT_CONFIG["cleanup_download_images"],
        )
        self.show_download_start_notice = self._cfg_bool(
            "show_download_start_notice",
            DEFAULT_CONFIG["show_download_start_notice"],
        )
        self.send_to_current_session = self._cfg_bool(
            "send_to_current_session",
            DEFAULT_CONFIG["send_to_current_session"],
        )
        self.target_sessions = self._cfg_list(
            "target_sessions",
            DEFAULT_CONFIG["target_sessions"],
        )
        self.notify_password_to_target_sessions = self._cfg_bool(
            "notify_password_to_target_sessions",
            DEFAULT_CONFIG["notify_password_to_target_sessions"],
        )
        self.pdf_compress_steps = self._parse_pdf_compress_profiles(
            self._cfg_str("pdf_compress_profiles", DEFAULT_CONFIG["pdf_compress_profiles"])
        )
        self.search_result_limit = self._cfg_int(
            "search_result_limit",
            DEFAULT_CONFIG["search_result_limit"],
            minimum=1,
            maximum=50,
        )
        self.search_candidate_limit = self._cfg_int(
            "search_candidate_limit",
            DEFAULT_CONFIG["search_candidate_limit"],
            minimum=self.search_result_limit,
            maximum=200,
        )
        self.search_plan_limit = self._cfg_int(
            "search_plan_limit",
            DEFAULT_CONFIG["search_plan_limit"],
            minimum=1,
            maximum=20,
        )
        self.search_page_limit = self._cfg_int(
            "search_page_limit",
            DEFAULT_CONFIG["search_page_limit"],
            minimum=1,
            maximum=10,
        )
        self.max_search_page_count = self._cfg_int(
            "max_search_page_count",
            DEFAULT_CONFIG["max_search_page_count"],
            minimum=1,
            maximum=1000,
        )
        self.filter_serial_tags = self._cfg_bool(
            "filter_serial_tags",
            DEFAULT_CONFIG["filter_serial_tags"],
        )
        self.enable_natural_language = self._cfg_bool(
            "enable_natural_language",
            DEFAULT_CONFIG["enable_natural_language"],
        )
        self.group_natural_language_require_at = self._cfg_bool(
            "group_natural_language_require_at",
            DEFAULT_CONFIG["group_natural_language_require_at"],
        )
        self.html_domains = self._cfg_list("html_domains", DEFAULT_CONFIG["html_domains"])
        self.image_thread_count = self._cfg_int(
            "image_thread_count",
            DEFAULT_CONFIG["image_thread_count"],
            minimum=1,
            maximum=64,
        )
        self.photo_thread_count = self._cfg_int(
            "photo_thread_count",
            DEFAULT_CONFIG["photo_thread_count"],
            minimum=1,
            maximum=16,
        )
        self.jm_log = self._cfg_bool("jm_log", DEFAULT_CONFIG["jm_log"])

        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.compress_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._handled_events: dict[tuple[str, str, str], float] = {}
        self._search_cache: dict[str, list[tuple[str, str]]] = {}

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = default
        return default if value is None else value

    def _cfg_str(self, key: str, default: Any) -> str:
        value = str(self._cfg(key, default)).strip()
        return value or str(default)

    def _cfg_int(
        self,
        key: str,
        default: Any,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        try:
            value = int(self._cfg(key, default))
        except (TypeError, ValueError):
            value = int(default)
        if minimum is not None:
            value = max(value, minimum)
        if maximum is not None:
            value = min(value, maximum)
        return value

    def _cfg_float(self, key: str, default: Any) -> float:
        try:
            value = float(self._cfg(key, default))
        except (TypeError, ValueError):
            value = float(default)
        return max(value, 0.1)

    def _cfg_bool(self, key: str, default: Any) -> bool:
        value = self._cfg(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "开启"}
        return bool(value)

    def _cfg_list(self, key: str, default: Any) -> list[str]:
        value = self._cfg(key, default)
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,，\n]", value) if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return list(default)

    @staticmethod
    def _parse_pdf_compress_profiles(raw_value: str) -> tuple[tuple[int, int], ...]:
        steps = []
        for part in re.split(r"[,，\n]", raw_value):
            part = part.strip()
            if not part:
                continue
            match = re.match(r"^(\d+)\s*[:xX/]\s*(\d+)$", part)
            if not match:
                continue
            max_side = max(int(match.group(1)), 100)
            quality = min(max(int(match.group(2)), 1), 95)
            steps.append((max_side, quality))
        return tuple(steps) or PDF_COMPRESS_STEPS

    async def help(self, event: AstrMessageEvent):
        """显示 JMComic 插件帮助。"""
        if not self._claim_event(event, "help", ""):
            return
        yield event.plain_result(
            self._help_text()
        )

    async def path(self, event: AstrMessageEvent):
        """查看 JMComic 下载目录。"""
        if not self._claim_event(event, "path", ""):
            return
        yield event.plain_result(
            "\n".join(
                [
                    f"JMComic 图片目录：{self.download_dir}",
                    f"JMComic PDF 目录：{self.pdf_dir}",
                ]
            )
        )

    async def path_cn(self, event: AstrMessageEvent):
        """查看 JMComic 下载目录。"""
        async for result in self.path(event):
            yield result

    async def info(self, event: AstrMessageEvent, album_id: str):
        """查询本子详情。"""
        if not self._claim_event(event, "info", album_id):
            return
        try:
            album = await asyncio.to_thread(self._get_album_detail, album_id)
            yield event.plain_result(self._format_album(album))
        except Exception as exc:
            logger.exception("JMComic info failed")
            yield event.plain_result(f"查询失败：{exc}")

    async def info_cn(self, event: AstrMessageEvent, album_id: str):
        """查询本子详情。"""
        async for result in self.info(event, album_id):
            yield result

    async def search(self, event: AstrMessageEvent, search_query: str):
        """搜索本子。"""
        search_query = search_query.strip()
        if not search_query:
            yield event.plain_result("请输入搜索关键词，例如：jm 搜索 <关键词>")
            return

        result = await self._perform_search(event, search_query, "search")
        if result is None:
            return

        try:
            yield event.plain_result(self._format_search_result(result))
        except Exception as exc:
            logger.exception("JMComic search failed")
            yield event.plain_result(f"搜索失败：{exc}")

    async def reference_search(self, event: AstrMessageEvent, album_id: str, mode: str):
        """根据参考本子搜索同作者/相似风格作品。"""
        claim_target = f"{mode}:{album_id}"
        if not self._claim_event(event, "reference_search", claim_target):
            return

        mode_label = self._reference_mode_label(mode)
        await event.send(event.plain_result(f"正在查找 JM{album_id} 的{mode_label}作品"))
        try:
            album = await asyncio.to_thread(self._get_album_detail, album_id)
            plan = self._build_reference_search_plan(album, mode, self._infer_order_by(event.message_str))
            if not plan:
                yield event.plain_result(f"没有从 JM{album_id} 提取到可用于搜索的作者、作品或标签。")
                return

            constraints = self._sanitize_search_constraints(
                {"exclude_ids": [str(album_id)]},
                event.message_str,
            )
            result = await asyncio.to_thread(
                self._run_search_plan,
                f"JM{album_id} {mode_label}",
                plan,
                constraints,
            )
            self._remember_search_items(event, result["items"])
            yield event.plain_result(
                "\n".join(
                    [
                        f"参考：JM{album_id} {self._truncate_text(getattr(album, 'name', ''))}",
                        self._format_search_result(result),
                    ]
                )
            )
        except Exception as exc:
            logger.exception("JMComic reference search failed")
            yield event.plain_result(f"查找失败：{exc}")

    async def download(self, event: AstrMessageEvent, album_id: str):
        """下载整个本子，导出 PDF 并发送到聊天。"""
        if not self._claim_event(event, "download", album_id):
            return
        if self.show_download_start_notice:
            await event.send(event.plain_result(f"开始下载 JM{album_id}"))
        try:
            album, pdf_path = await asyncio.to_thread(self._download_album, album_id)
            pdf_password = None
            if self.encrypt_pdf:
                pdf_password = self._generate_pdf_password()
                await asyncio.to_thread(self._encrypt_pdf, pdf_path, pdf_password)
            send_status = await self._send_pdf_file(event, pdf_path)
            await self._notify_pdf_password(event, send_status, pdf_password)
        except Exception as exc:
            logger.exception("JMComic album download failed")
            yield event.plain_result(f"下载失败：{exc}")

    async def _perform_search(
        self,
        event: AstrMessageEvent,
        search_query: str,
        claim_action: str,
    ) -> dict[str, Any] | None:
        if not self._claim_event(event, claim_action, search_query):
            return None

        await event.send(event.plain_result(f"正在搜索：{search_query}"))
        search_request = await self._build_search_request(event, search_query)
        plan = search_request["plan"]
        constraints = search_request["constraints"]
        result = await asyncio.to_thread(self._run_search_plan, search_query, plan, constraints)
        self._remember_search_items(event, result["items"])
        return result

    async def download_cn(self, event: AstrMessageEvent, album_id: str):
        """下载整个本子到服务器。"""
        async for result in self.download(event, album_id):
            yield result

    async def photo(self, event: AstrMessageEvent, photo_id: str):
        """下载单个章节，导出 PDF 并发送到聊天。"""
        if not self._claim_event(event, "photo", photo_id):
            return
        if self.show_download_start_notice:
            await event.send(event.plain_result(f"开始处理章节 P{photo_id}：下载 → 压缩 PDF → 上传，完成后自动清理本地文件。"))
        try:
            photo, pdf_path = await asyncio.to_thread(self._download_photo, photo_id)
            pdf_password = None
            if self.encrypt_pdf:
                pdf_password = self._generate_pdf_password()
                await asyncio.to_thread(self._encrypt_pdf, pdf_path, pdf_password)
            pdf_size = self._format_size(pdf_path.stat().st_size)
            yield event.plain_result(
                "\n".join(
                    [
                        f"章节下载完成：P{photo.photo_id}",
                        f"标题：{photo.name}",
                        f"图片数：{len(photo)}",
                        f"PDF：{pdf_path.name}",
                        f"大小：{pdf_size}",
                        *([f"密码：{pdf_password}"] if pdf_password else []),
                        "开始上传 PDF...",
                    ]
                )
            )
            send_status = await self._send_pdf_file(event, pdf_path)
            await self._notify_pdf_password(event, send_status, pdf_password)
        except Exception as exc:
            logger.exception("JMComic photo download failed")
            yield event.plain_result(f"章节下载失败：{exc}")

    async def photo_cn(self, event: AstrMessageEvent, photo_id: str):
        """下载单个章节到服务器。"""
        async for result in self.photo(event, photo_id):
            yield result

    @filter.regex(r"^\s*[jJ][mM]\s*$")
    async def plain_help(self, event: AstrMessageEvent):
        """兼容 jm。"""
        yield event.plain_result(self._help_text())

    @filter.regex(r"^\s*[jJ][mM]\s*(?:帮助|[hH][eE][lL][pP])\s*$")
    async def plain_help_cn(self, event: AstrMessageEvent):
        """兼容 jm 帮助。"""
        yield event.plain_result(self._help_text())

    @filter.regex(r"^.*[jJ][mM]\s*(?:查询|查|[iI][nN][fF][oO])\D*\d{3,}.*$")
    async def plain_info(self, event: AstrMessageEvent):
        """兼容 jm 查询 123 / JM123查一下。"""
        if self._is_reference_search_message(event.message_str):
            return
        album_id = self._extract_first_number(event.message_str)
        if album_id is None:
            yield event.plain_result("请提供 JM 车号，例如：jm 查询 123456")
            return
        async for result in self.info(event, album_id):
            yield result

    @filter.regex(r"^.*[jJ][mM]\s*(?:查询|查)\D*\d{3,}.*$")
    async def plain_info_cn(self, event: AstrMessageEvent):
        """兼容 jm 查询 123。"""
        if self._is_reference_search_message(event.message_str):
            return
        album_id = self._extract_first_number(event.message_str)
        if album_id is None:
            yield event.plain_result("请提供 JM 车号，例如：jm 查询 123456")
            return
        async for result in self.info(event, album_id):
            yield result

    @filter.regex(r"^.*[jJ][mM]\s*(?:搜索|搜|[sS][eE][aA][rR][cC][hH])\s*.+$")
    async def plain_search(self, event: AstrMessageEvent):
        """兼容 jm 搜索 关键词。"""
        search_query = self._extract_search_query(event.message_str)
        if not search_query:
            yield event.plain_result("请输入搜索关键词，例如：jm 搜索 <关键词>")
            return
        async for result in self.search(event, search_query):
            yield result

    @filter.regex(rf"^(?=.*[jJ][mM]\D*\d{{3,}})(?=.*{REFERENCE_SEARCH_PATTERN}).*$")
    async def plain_reference_search(self, event: AstrMessageEvent):
        """兼容 JM123 同作者 / JM123 同风格。"""
        album_id = self._extract_reference_album_id(event.message_str)
        if album_id is None:
            yield event.plain_result("请提供参考 JM 车号，例如：jm 123456 同作者")
            return
        mode = self._reference_search_mode(event.message_str)
        async for result in self.reference_search(event, album_id, mode):
            yield result

    @filter.regex(r"^(?!.*[jJ][mM])\s*\S[\s\S]*$")
    async def natural_language(self, event: AstrMessageEvent):
        """自然语言搜索/下载。"""
        if not self._should_consider_natural_language(event):
            return

        intent = await self._parse_natural_language_intent(event)
        action = intent.get("action", "none")

        if action == "search":
            search_query = str(intent.get("query", "")).strip()
            if search_query:
                async for result in self.search(event, search_query):
                    yield result
            return

        if action == "download":
            album_id = str(intent.get("album_id", "")).strip()
            if album_id:
                async for result in self.download(event, album_id):
                    yield result
            return

        if action == "download_result":
            index = self._parse_result_index(intent.get("index", 1))
            cached_album_id = self._get_cached_search_album_id(event, index)
            if cached_album_id:
                async for result in self.download(event, cached_album_id):
                    yield result
            else:
                yield event.plain_result("没有可下载的搜索结果，请先发送：jm 搜索 <关键词>")

    @filter.regex(r"^.*[jJ][mM]\s*(?:下载|[dD][oO][wW][nN][lL][oO][aA][dD]|[dD][lL])\D*\d{3,}.*$")
    async def plain_download(self, event: AstrMessageEvent):
        """兼容 jm 下载 123 / JM download 123。"""
        album_id = self._extract_first_number(event.message_str)
        if album_id is None:
            yield event.plain_result("请提供 JM 车号，例如：jm 下载 123456")
            return
        async for result in self.download(event, album_id):
            yield result

    @filter.regex(r"^.*[jJ][mM]\s*(?:章节|[pP][hH][oO][tT][oO])\D*\d{3,}.*$")
    async def plain_photo(self, event: AstrMessageEvent):
        """兼容 jm-photo 123 / jm photo 123。"""
        photo_id = self._extract_first_number(event.message_str)
        if photo_id is None:
            yield event.plain_result("请提供章节 ID，例如：jm-photo 123456")
            return
        async for result in self.photo(event, photo_id):
            yield result

    @filter.regex(r"^.*[jJ][mM]\s*(?:下载|下|要)?\s*(?:第\s*(?:\d+|[一二两三四五六七八九十])\s*个?|(?:\d+|[一二两三四五六七八九十])\s*(?:个|号)).*$")
    async def plain_download_result(self, event: AstrMessageEvent):
        """兼容 jm 第一个 / jm 下载第2个。"""
        index = self._extract_result_index(event.message_str)
        if index is None:
            yield event.plain_result("请提供结果序号，例如：jm 第一个")
            return

        cached_album_id = self._get_cached_search_album_id(event, index)
        if cached_album_id:
            async for result in self.download(event, cached_album_id):
                yield result
        else:
            yield event.plain_result("没有可下载的搜索结果，请先发送：jm 搜索 <关键词>")

    @filter.regex(r"^.*[jJ][mM](?!\s*(?:查询|查|[iI][nN][fF][oO]|搜索|搜|[sS][eE][aA][rR][cC][hH]|帮助|[hH][eE][lL][pP]|路径|[pP][aA][tT][hH]|章节|[pP][hH][oO][tT][oO]|下载|[dD][oO][wW][nN][lL][oO][aA][dD]|[dD][lL]))\D*\d{3,}.*$")
    async def plain_default_download(self, event: AstrMessageEvent):
        """兼容 jm 123 / JM123看过没，默认下载本子。"""
        if self._is_reference_search_message(event.message_str):
            return
        album_id = self._extract_first_number(event.message_str)
        if album_id is None:
            yield event.plain_result("请提供 JM 车号，例如：jm 123456")
            return
        async for result in self.download(event, album_id):
            yield result

    def _load_jmcomic(self):
        try:
            import jmcomic
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "缺少 jmcomic 依赖。请在 AstrBot 容器内安装插件 requirements.txt。"
            ) from exc
        return jmcomic

    @staticmethod
    def _extract_first_number(message: str) -> str | None:
        jm_match = re.search(r"(?i)jm", message)
        number_source = message[jm_match.end():] if jm_match else message
        numbers = re.findall(r"\d+", number_source)
        if numbers:
            return "".join(numbers)
        return None

    @staticmethod
    def _extract_reference_album_id(message: str) -> str | None:
        jm_match = re.search(r"(?i)jm", message)
        number_source = message[jm_match.end():] if jm_match else message
        match = re.search(r"\d{3,}", number_source)
        return match.group(0) if match else None

    @staticmethod
    def _is_reference_search_message(message: str) -> bool:
        return bool(re.search(REFERENCE_SEARCH_PATTERN, message))

    def _reference_search_mode(self, message: str) -> str:
        has_author = bool(re.search(REFERENCE_AUTHOR_PATTERN, message))
        has_style = bool(re.search(REFERENCE_STYLE_PATTERN, message))
        if has_author and has_style:
            return "all"
        if has_author:
            return "author"
        return "style"

    @staticmethod
    def _reference_mode_label(mode: str) -> str:
        return {
            "author": "同作者",
            "style": "相似风格",
            "all": "同作者/相似风格",
        }.get(mode, "相关")

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "JMComic 命令：",
                "jm 查询 <id> - 查询信息",
                "jm 搜索 <关键词> - 搜索本子",
                "jm <id> 同作者 - 查找同作者作品",
                "jm <id> 同风格 - 查找相似风格作品",
                "jm <id> 同作者 同风格 - 混合查找相关作品",
                "jm <id> - 下载并发送 PDF",
            ]
        )

    def _create_option(self):
        jmcomic = self._load_jmcomic()
        os.environ["JM_DOWNLOAD_DIR"] = str(self.download_dir)
        with OPTION_FILE.open("r", encoding="utf-8") as file:
            option_data = yaml.safe_load(file) or {}

        option_data["log"] = self.jm_log
        client_cfg = option_data.setdefault("client", {})
        domain_cfg = client_cfg.setdefault("domain", {})
        domain_cfg["html"] = self.html_domains

        download_cfg = option_data.setdefault("download", {})
        threading_cfg = download_cfg.setdefault("threading", {})
        threading_cfg["image"] = self.image_thread_count
        threading_cfg["photo"] = self.photo_thread_count

        dir_rule = option_data.setdefault("dir_rule", {})
        dir_rule["base_dir"] = str(self.download_dir)

        with self.runtime_option_file.open("w", encoding="utf-8") as file:
            yaml.safe_dump(option_data, file, allow_unicode=True, sort_keys=False)

        return jmcomic.create_option_by_file(str(self.runtime_option_file))

    def _get_album_detail(self, album_id: str):
        option = self._create_option()
        client = option.new_jm_client()
        return client.get_album_detail(album_id)

    def _search_album(self, search_query: str):
        option = self._create_option()
        client = option.new_jm_client()
        return client.search(
            search_query,
            page=1,
            main_tag=0,
            order_by="mr",
            time="a",
            category="0",
            sub_category=None,
        )

    async def _build_search_plan(
        self,
        event: AstrMessageEvent,
        search_query: str,
    ) -> list[dict[str, Any]]:
        request = await self._build_search_request(event, search_query)
        return request["plan"]

    async def _build_search_request(
        self,
        event: AstrMessageEvent,
        search_query: str,
    ) -> dict[str, Any]:
        fallback_plan = self._fallback_search_plan(search_query)
        fallback_constraints = self._sanitize_search_constraints(None, search_query)
        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=json.dumps({"user_query": search_query}, ensure_ascii=False),
                system_prompt=self._search_skill_system_prompt(),
            )
            parsed = self._parse_json_object(llm_resp.completion_text)
            searches = parsed.get("searches") if parsed else None
            llm_constraints = parsed.get("constraints") if parsed else None
            plan = self._sanitize_search_plan(searches)
            plan = self._adjust_search_plan(search_query, plan)
            constraints = self._sanitize_search_constraints(llm_constraints, search_query)
            if plan:
                logger.info(
                    f"JMComic smart search plan: query={search_query}, "
                    f"plan={plan}, constraints={constraints}"
                )
            return {
                "plan": plan or fallback_plan,
                "constraints": constraints or fallback_constraints,
            }
        except Exception:
            logger.exception("JMComic smart search planning failed")
            return {"plan": fallback_plan, "constraints": fallback_constraints}

    @staticmethod
    def _search_skill_system_prompt() -> str:
        return (
            "你是 JMComic Skill 检索规划器，只输出 JSON，不要输出解释。\n"
            "你不能直接搜索或下载，只负责生成工具参数，插件会执行 search_album 并展示结果。\n"
            "工具 search_album 参数：keyword, main_tag, order_by。\n"
            "main_tag: 0=站内综合, 1=作品, 2=作者, 3=标签, 4=角色。\n"
            "order_by: latest=最新, views=观看/浏览, likes=点赞/喜欢, pictures=页数。\n"
            "constraints 可选字段：max_chapters, max_pages, result_limit, search_pages。\n"
            "规则：\n"
            "- keyword 必须保留用户真正想搜的作者、作品、角色、标签或核心描述，去掉“本子/漫画/风格/题材/相关/这种/帮我/搜一下”等泛词。\n"
            "- 多个核心词组成一个短语时，优先保留完整短语；如果短语可能过窄，可追加一条用空格分隔核心词的检索。\n"
            "- 不需要凑满 6 条检索；关键词明确时优先输出 1 条高质量检索。\n"
            "- 如果是作者作品、按观看/喜欢排序、或需要更充分候选，可设置 constraints.search_pages=2 到 5。\n"
            "- 用户显式输入 JM 高级搜索语法（例如 +A +B、A -B）时，应原样保留 + 和 -。\n"
            "- “作者 XXX 的作品”必须 keyword=XXX 且 main_tag=2。\n"
            "- “角色 XXX”优先 keyword=XXX 且 main_tag=4；“标签 XXX”优先 keyword=XXX 且 main_tag=3。\n"
            "- “类似 某作品 / 某作品 画风”优先 keyword=某作品 且 main_tag=1。\n"
            "- “短篇”“不要超过 3 章”应设置 constraints.max_chapters=3；如果只说短篇，默认 max_chapters=3。\n"
            "- “前 5 个/前10篇/5个结果”应设置 constraints.result_limit，不要把“前10篇”放进 keyword，不要生成下载动作。\n"
            "- 用户说“最多喜欢/最多点赞/最多爱心/按喜欢/按点赞”时，所有主要检索 order_by 必须用 likes。\n"
            "- 用户说“看的人多/最多观看/最多浏览/热门/人气”时，主要检索 order_by 用 views；如果同时明确喜欢，则以 likes 为准。\n"
            "- 不要编造不存在的作者、作品、角色或标签；最多输出 6 个检索。\n"
            '输出格式：{"searches":[{"keyword":"XXX","main_tag":2,"order_by":"views"}],'
            '"constraints":{"result_limit":5,"max_chapters":3,"search_pages":2}}'
        )

    def _fallback_search_plan(self, search_query: str) -> list[dict[str, Any]]:
        keywords = [search_query]
        cleaned = self._strip_search_noise(search_query)
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)

        compact = re.sub(r"\s+", " ", cleaned or search_query).strip()
        if compact and compact not in keywords:
            keywords.append(compact)

        plan = self._sanitize_search_plan(
            [
                {"keyword": keyword, "main_tag": 0, "order_by": self._infer_order_by(search_query)}
                for keyword in keywords
            ]
        )
        return self._adjust_search_plan(search_query, plan)

    @staticmethod
    def _strip_search_noise(search_query: str) -> str:
        query = re.sub(
            r"(帮我|帮忙|麻烦|请|给我|我想|想|搜索一下|搜一下|找一下|搜索|搜|找|有没有|推荐|来点)",
            " ",
            search_query,
        )
        query = re.sub(
            r"(按照|按)?\s*(?:最多|最高)?\s*(?:喜欢|喜歡|点赞|點贊|爱心|愛心|收藏|观看|觀看|浏览|瀏覽|人气|人氣|热门|熱門)\s*(?:排序|排行)?",
            " ",
            query,
        )
        query = re.sub(r"(?:前|第)\s*\d+\s*(?:个|篇|条|本|部)?", " ", query)
        query = re.sub(
            r"(?:显示|展示|返回|列出|给我|來|来)\s*[一二两三四五六七八九十\d]+\s*(?:个|篇|条|本|部)?",
            " ",
            query,
        )
        query = re.sub(
            r"(本子|漫画|禁漫|JM|jm|车号|一下|看看|看过没|点|吗|啊|吧|的|风格|画风|类型|類型|题材|題材|主题|主題|相关|相關|有关|有關|这种|這種|那种|那種)",
            " ",
            query,
        )
        return re.sub(r"\s+", " ", query).strip(" ，,。.!！?")

    def _adjust_search_plan(
        self,
        search_query: str,
        plan: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        order_by = self._infer_order_by(search_query)
        protected_keyword = self._extract_protected_keyword(search_query)
        protected_main_tag = self._infer_protected_main_tag(search_query)
        adjusted: list[dict[str, Any]] = []
        has_advanced_keyword = any(
            re.search(r"(^|\s)[+-]\S+", str(item.get("keyword", "")))
            for item in plan
        )

        should_add_protected = not plan or bool(self._extract_advanced_search_keyword(search_query))
        for keyword in (
            []
            if has_advanced_keyword or not should_add_protected
            else self._expand_keyword_variants(protected_keyword)
        ):
            adjusted.append(
                {
                    "keyword": keyword,
                    "main_tag": protected_main_tag,
                    "order_by": order_by,
                }
            )

        for item in plan:
            copied = dict(item)
            if order_by != "mr":
                copied["order_by"] = order_by
            if protected_main_tag and int(copied.get("main_tag", 0) or 0) == 0:
                copied["main_tag"] = protected_main_tag
            adjusted.append(copied)

        return self._dedupe_search_plan(adjusted)

    @staticmethod
    def _expand_keyword_variants(keyword: str) -> list[str]:
        keyword = keyword.strip()
        if not keyword:
            return []

        variants = [keyword]
        separators = r"[\s,，、/|]+"
        if re.search(separators, keyword):
            parts = [part for part in re.split(separators, keyword) if part]
            spaced = " ".join(parts)
            if len(parts) >= 2 and spaced not in variants:
                variants.append(spaced)

        return variants

    @staticmethod
    def _infer_order_by(search_query: str) -> str:
        if re.search(r"(最多|按|排序|高).*(喜欢|喜歡|点赞|點贊|爱心|愛心|收藏)|(?:喜欢|喜歡|点赞|點贊|爱心|愛心).*(最多|排序|高)", search_query):
            return "tf"
        if re.search(r"(看的人|观看|觀看|浏览|瀏覽|人气|人氣|热门|熱門|最多看|最多浏览|最多觀看)", search_query):
            return "mv"
        return "mr"

    def _extract_protected_keyword(self, search_query: str) -> str:
        advanced_keyword = self._extract_advanced_search_keyword(search_query)
        if advanced_keyword:
            return advanced_keyword

        explicit_keyword = self._extract_explicit_keyword(search_query)
        if explicit_keyword:
            return explicit_keyword

        keyword = self._strip_search_noise(search_query)
        keyword = re.sub(
            r"(最多|按照|按|排序|高|喜欢|喜歡|点赞|點贊|爱心|愛心|看的人|观看|觀看|浏览|瀏覽|人气|人氣|热门|熱門|质量|高质量|不超过\s*\d+\s*章|不要超过\s*\d+\s*章|最多\s*\d+\s*章|前\s*\d+\s*(?:个|篇|条|本|部)?|第\s*\d+\s*(?:个|篇|条|本|部)?|(?:显示|展示|返回|列出|给我|來|来)\s*[一二两三四五六七八九十\d]+\s*(?:个|篇|条|本|部)?|下载|作品|短篇|故事|类似|相似|像|一些)",
            " ",
            keyword,
        )
        keyword = re.sub(r"\s+", " ", keyword).strip(" ，,。.!！?")
        if len(keyword) < 2:
            return ""
        return keyword

    @staticmethod
    def _extract_advanced_search_keyword(search_query: str) -> str:
        if re.search(r"(^|\s)[+-]\S+", search_query):
            keyword = JMComicPlugin._strip_search_noise(search_query)
            return re.sub(r"\s+", " ", keyword).strip(" ，,。.!！?")
        return ""

    @staticmethod
    def _extract_explicit_keyword(search_query: str) -> str:
        patterns = [
            r"作者\s*[\[【「《]?([^\]】」》,，。\s]+)",
            r"(?:类似|相似|像)\s*[\[【「《]?([^\]】」》,，。\s]+)",
            r"作品\s*[\[【「《]?([^\]】」》,，。\s]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, search_query)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _infer_protected_main_tag(search_query: str) -> int:
        if re.search(r"作者", search_query):
            return 2
        if re.search(r"(作品|类似|相似|像)", search_query):
            return 1
        if re.search(r"标签", search_query):
            return 3
        if re.search(r"角色", search_query):
            return 4
        return 0

    @staticmethod
    def _infer_search_constraints(search_query: str) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        chapter_match = re.search(r"(?:不超过|不要超过|最多|小于|少于)\s*(\d+)\s*章", search_query)
        if chapter_match:
            constraints["max_chapters"] = max(int(chapter_match.group(1)), 1)

        page_match = re.search(r"(?:不超过|不要超过|最多|小于|少于)\s*(\d+)\s*页", search_query)
        if page_match:
            constraints["max_pages"] = max(int(page_match.group(1)), 1)

        if "短篇" in search_query and "max_chapters" not in constraints:
            constraints["max_chapters"] = 3

        return constraints

    def _sanitize_search_constraints(
        self,
        llm_constraints: Any,
        search_query: str,
    ) -> dict[str, Any]:
        constraints = self._infer_search_constraints(search_query)
        if isinstance(llm_constraints, dict):
            for key in ("max_chapters", "max_pages", "result_limit", "search_pages"):
                value = self._coerce_positive_int(llm_constraints.get(key))
                if value is not None:
                    constraints[key] = value
            exclude_ids = llm_constraints.get("exclude_ids")
            if isinstance(exclude_ids, (list, tuple, set)):
                constraints["exclude_ids"] = {str(item) for item in exclude_ids if str(item).strip()}
            elif exclude_ids:
                constraints["exclude_ids"] = {str(exclude_ids)}

        result_limit = constraints.get("result_limit")
        if result_limit is None:
            result_limit_match = re.search(r"(?:前|top\s*)\s*(\d+)\s*(?:个|篇|条|本|部)?", search_query, re.I)
            if result_limit_match:
                result_limit = int(result_limit_match.group(1))
            else:
                chinese_limit_match = re.search(
                    r"(?:显示|展示|返回|列出|给我|來|来)\s*([一二两三四五六七八九十\d]+)\s*(?:个|篇|条|本|部)?",
                    search_query,
                )
                if chinese_limit_match:
                    result_limit = self._parse_chinese_number(chinese_limit_match.group(1))

        if result_limit is not None:
            constraints["result_limit"] = min(max(int(result_limit), 1), self.search_result_limit)

        if "max_chapters" in constraints:
            constraints["max_chapters"] = max(int(constraints["max_chapters"]), 1)
        if "max_pages" in constraints:
            constraints["max_pages"] = max(int(constraints["max_pages"]), 1)
        if "search_pages" in constraints:
            constraints["search_pages"] = min(
                max(int(constraints["search_pages"]), 1),
                self.search_page_limit,
            )

        return constraints

    def _build_reference_search_plan(
        self,
        album: Any,
        mode: str,
        order_by: str,
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []

        authors = self._reference_terms(getattr(album, "authors", []))
        works = self._reference_terms(getattr(album, "works", []))
        tags = self._reference_terms(getattr(album, "tags", []))

        author_steps = [
            {"keyword": author, "main_tag": 2, "order_by": order_by}
            for author in authors
        ]
        style_steps = [
            *({"keyword": work, "main_tag": 1, "order_by": order_by} for work in works),
            *({"keyword": tag, "main_tag": 3, "order_by": order_by} for tag in tags),
        ]

        if mode == "author":
            plan.extend(author_steps)
        elif mode == "style":
            plan.extend(style_steps)
        elif mode == "all":
            while len(plan) < self.search_plan_limit and (author_steps or style_steps):
                if author_steps:
                    plan.append(author_steps.pop(0))
                if style_steps and len(plan) < self.search_plan_limit:
                    plan.append(style_steps.pop(0))

        if not plan:
            plan.extend(
                {"keyword": author, "main_tag": 2, "order_by": order_by}
                for author in authors[: self.search_plan_limit]
            )

        return self._dedupe_search_plan(plan)

    @staticmethod
    def _reference_terms(values: Any) -> list[str]:
        if not values:
            return []

        generic_terms = {
            "中文",
            "漢化",
            "汉化",
            "翻译",
            "翻譯",
            "english",
            "chinese",
        }
        terms = []
        for value in values:
            term = str(value).strip()
            if len(term) < 2:
                continue
            if term.lower() in generic_terms:
                continue
            if term not in terms:
                terms.append(term)
        return terms

    @staticmethod
    def _coerce_positive_int(value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _parse_chinese_number(text: str) -> int | None:
        text = str(text).strip()
        if text.isdigit():
            return int(text)

        digits = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if text == "十":
            return 10
        if text.startswith("十") and len(text) == 2:
            return 10 + digits.get(text[1], 0)
        if text.endswith("十") and len(text) == 2:
            return digits.get(text[0], 0) * 10
        if "十" in text and len(text) == 3:
            return digits.get(text[0], 0) * 10 + digits.get(text[2], 0)
        return digits.get(text)

    def _dedupe_search_plan(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()
        for item in plan:
            keyword = str(item.get("keyword", "")).strip()
            if not keyword:
                continue
            key = (
                keyword.lower(),
                int(item.get("main_tag", 0) or 0),
                str(item.get("order_by", "mr")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= self.search_plan_limit:
                break
        return deduped

    def _sanitize_search_plan(self, searches: Any) -> list[dict[str, Any]]:
        if not isinstance(searches, list):
            return []

        plan: list[dict[str, Any]] = []
        seen: set[tuple[str, int, str]] = set()
        for item in searches:
            if isinstance(item, str):
                keyword = item
                main_tag = 0
                order_by = "latest"
            elif isinstance(item, dict):
                keyword = str(item.get("keyword", "")).strip()
                try:
                    main_tag = int(item.get("main_tag", 0))
                except (TypeError, ValueError):
                    main_tag = 0
                order_by = str(item.get("order_by", "latest")).strip().lower()
            else:
                continue

            keyword = self._strip_search_noise(keyword) or keyword.strip()
            if not keyword:
                continue

            if main_tag not in SEARCH_MAIN_TAGS:
                main_tag = 0
            order_by = SEARCH_ORDER_MAP.get(order_by, "mr")
            key = (keyword.lower(), main_tag, order_by)
            if key in seen:
                continue
            seen.add(key)
            plan.append({"keyword": keyword, "main_tag": main_tag, "order_by": order_by})
            if len(plan) >= self.search_plan_limit:
                break

        return plan

    def _run_search_plan(
        self,
        original_query: str,
        plan: list[dict[str, Any]],
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        option = self._create_option()
        client = option.new_jm_client()
        attempts = []
        items_by_id: dict[str, dict[str, Any]] = {}

        for step in plan[:self.search_plan_limit]:
            keyword = step["keyword"]
            main_tag = int(step.get("main_tag", 0))
            order_by = step.get("order_by", "mr")
            attempt = {"keyword": keyword, "main_tag": main_tag, "order_by": order_by, "total": 0, "pages": 0}
            step_added = 0
            search_pages = self._search_pages_for_step(step, plan, constraints or {})
            step_limit = min(self.search_candidate_limit, self.search_result_limit * search_pages)

            for page_number in range(1, search_pages + 1):
                try:
                    page = client.search(
                        keyword,
                        page=page_number,
                        main_tag=main_tag,
                        order_by=order_by,
                        time="a",
                        category="0",
                        sub_category=None,
                    )
                except Exception as exc:
                    logger.warning(
                        f"JMComic search step failed: keyword={keyword}, "
                        f"main_tag={main_tag}, page={page_number}, error={exc}"
                    )
                    break

                if page_number == 1:
                    attempt["total"] = int(getattr(page, "total", 0) or 0)
                attempt["pages"] = page_number

                for album in self._iter_search_page_items(page, keyword, main_tag):
                    album["order_by"] = order_by
                    if album["id"] not in items_by_id:
                        items_by_id[album["id"]] = album
                        step_added += 1
                    if step_added >= step_limit or len(items_by_id) >= self.search_candidate_limit:
                        break

                if step_added >= step_limit or len(items_by_id) >= self.search_candidate_limit:
                    break

                page_count = int(getattr(page, "page_count", 0) or 0)
                if page_count and page_number >= page_count:
                    break

            attempts.append(attempt)
            if len(items_by_id) >= self.search_candidate_limit:
                break

        items = list(items_by_id.values())
        self._enrich_search_items(client, items)
        items = self._filter_search_items(items, constraints or {})
        items = self._rank_search_items(items, attempts)
        result_limit = min(
            max(int((constraints or {}).get("result_limit") or self.search_result_limit), 1),
            self.search_result_limit,
        )
        return {
            "query": original_query,
            "attempts": attempts,
            "items": items[:result_limit],
            "constraints": constraints or {},
        }

    def _search_pages_for_step(
        self,
        step: dict[str, Any],
        plan: list[dict[str, Any]],
        constraints: dict[str, Any],
    ) -> int:
        requested = self._coerce_positive_int(constraints.get("search_pages"))
        if requested is not None:
            return min(max(requested, 1), self.search_page_limit)

        if self.search_page_limit <= 1:
            return 1

        main_tag = int(step.get("main_tag", 0) or 0)
        order_by = str(step.get("order_by", "mr"))

        if len(plan) >= 3:
            return 1
        if main_tag == 2:
            return min(self.search_page_limit, 3)
        if order_by in {"mv", "tf"}:
            return min(self.search_page_limit, 2)
        if constraints.get("max_chapters") or constraints.get("max_pages"):
            return min(self.search_page_limit, 2)
        return 1

    @staticmethod
    def _enrich_search_items(client: Any, items: list[dict[str, Any]]) -> None:
        for item in items:
            try:
                album = client.get_album_detail(str(item["id"]))
            except Exception as exc:
                logger.warning(f"JMComic album detail enrich failed: id={item.get('id')}, error={exc}")
                continue
            item["views"] = int(getattr(album, "views", 0) or 0)
            item["likes"] = int(getattr(album, "likes", 0) or 0)
            item["page_count"] = int(getattr(album, "page_count", 0) or 0)
            item["chapter_count"] = len(album)
            item["tags"] = [str(tag) for tag in getattr(album, "tags", []) or item.get("tags", [])]
            if not item.get("title"):
                item["title"] = str(getattr(album, "name", "") or "")

    def _filter_search_items(
        self,
        items: list[dict[str, Any]],
        constraints: dict[str, Any],
    ) -> list[dict[str, Any]]:
        max_pages = int(constraints.get("max_pages") or self.max_search_page_count)
        max_chapters = int(constraints.get("max_chapters") or self.max_search_page_count)
        exclude_ids = {str(item) for item in constraints.get("exclude_ids", set())}
        return [
            item
            for item in items
            if str(item.get("id", "")) not in exclude_ids
            if self._is_short_enough(item, max_pages, max_chapters)
            and (not self.filter_serial_tags or not self._has_serial_tag(item))
        ]

    @staticmethod
    def _is_short_enough(item: dict[str, Any], max_pages: int, max_chapters: int) -> bool:
        page_count = int(item.get("page_count", 0) or 0)
        chapter_count = int(item.get("chapter_count", 0) or 0)
        if page_count > max_pages:
            return False
        return chapter_count <= max_chapters

    @staticmethod
    def _has_serial_tag(item: dict[str, Any]) -> bool:
        tags = item.get("tags") or []
        return any(SERIAL_TAG_PATTERN.search(str(tag)) for tag in tags)

    @staticmethod
    def _rank_search_items(
        items: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        orders = {str(attempt.get("order_by", "")) for attempt in attempts}
        if "mv" not in orders and "tf" not in orders:
            return items

        def score(item: dict[str, Any]) -> tuple[int, int, int]:
            views = int(item.get("views", 0) or 0)
            likes = int(item.get("likes", 0) or 0)
            if "mv" in orders and "tf" in orders:
                return (views + likes * 20, views, likes)
            if "tf" in orders:
                return (likes, views, 0)
            return (views, likes, 0)

        return sorted(items, key=score, reverse=True)

    @staticmethod
    def _iter_search_page_items(page: Any, keyword: str, main_tag: int) -> list[dict[str, Any]]:
        items = []
        content = getattr(page, "content", None)
        if content:
            for album_id, album_info in content:
                title = ""
                tags = []
                if isinstance(album_info, dict):
                    title = str(album_info.get("name") or album_info.get("title") or "")
                    tags = album_info.get("tags") or []
                items.append(
                    {
                        "id": str(album_id),
                        "title": title,
                        "tags": [str(tag) for tag in tags],
                        "keyword": keyword,
                        "main_tag": main_tag,
                    }
                )
            return items

        for album_id, title in page.iter_id_title():
            items.append(
                {
                    "id": str(album_id),
                    "title": str(title),
                    "tags": [],
                    "keyword": keyword,
                    "main_tag": main_tag,
                }
            )
        return items

    async def _parse_natural_language_intent(self, event: AstrMessageEvent) -> dict[str, Any]:
        message = event.message_str.strip()
        fallback = self._rule_parse_natural_language_intent(message)

        try:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=message,
                system_prompt=self._intent_system_prompt(),
            )
            parsed = self._parse_json_object(llm_resp.completion_text)
            if parsed and parsed.get("action") in {"none", "search", "download", "download_result"}:
                if parsed.get("action") == "none" and fallback.get("action") != "none":
                    return fallback
                return parsed
        except Exception:
            logger.exception("JMComic natural language intent parsing failed")

        return fallback

    def _should_consider_natural_language(self, event: AstrMessageEvent) -> bool:
        if not self.enable_natural_language:
            return False

        message = event.message_str.strip()
        if not message:
            return False

        if not event.get_group_id():
            return True

        if not self.group_natural_language_require_at:
            return True

        return self._is_at_bot(event)

    @staticmethod
    def _is_at_bot(event: AstrMessageEvent) -> bool:
        self_id = str(event.get_self_id() or "")
        if not self_id:
            return False

        message_obj = getattr(event, "message_obj", None)
        for component in getattr(message_obj, "message", []) or []:
            if isinstance(component, Comp.At) and str(getattr(component, "qq", "")) == self_id:
                return True

        return f"[At:{self_id}]" in getattr(event, "message_str", "")

    @staticmethod
    def _intent_system_prompt() -> str:
        return (
            "你是 JMComic 插件的意图解析器，只输出 JSON，不要输出解释。\n"
            "可用动作：\n"
            "1. search：用户想搜索/找/推荐本子、漫画、JM 内容。字段 query 为搜索关键词。\n"
            "2. download：用户给出明确 JM 车号并要求下载。字段 album_id 为数字字符串。\n"
            "3. download_result：用户要求下载上一次搜索结果中的第几个。字段 index 为 1 开始的整数。\n"
            "4. none：不是 JMComic 搜索或下载需求。\n"
            "规则：\n"
            "- “帮我搜一下某个标签”“找点某位作者的作品”“有没有某类本子” => search。\n"
            "- “我想看某个主题，并且看的人比较多的高质量内容” => search，query 保留主题和排序偏好。\n"
            "- “想看某某画风/某某题材/某某类型/热门高质量” => search。\n"
            "- “搜索作者 XXX 的作品，按浏览量排序，下载前 5 个最热门的本子” => search，不要真的下载，query 保留作者、浏览量、前5。\n"
            "- “找画风类似 某作品 的短篇故事，不超过 3 章” => search，query 保留作品名、短篇、不超过3章。\n"
            "- “下载第一个”“下第二个”“要第3个” => download_result。\n"
            "- 只有明确数字 JM 车号时，“下载 123456”“下车号 123456” => download。\n"
            "- 不要输出 search_download，本插件不做批量下载，只展示搜索结果。\n"
            "- query 去掉“帮我/搜一下/找一下/本子/漫画/有没有/推荐/来点/想看”等口语，但必须保留作者、作品、角色、标签、排序方式、前N个、不超过N章/页等约束。\n"
            '输出格式示例：{"action":"search","query":"关键词"}'
        )

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        text = text.strip()
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _rule_parse_natural_language_intent(self, message: str) -> dict[str, Any]:
        album_id_match = re.search(r"(?:下载|下|车号)\D*(\d{3,})", message)
        if album_id_match:
            return {"action": "download", "album_id": album_id_match.group(1)}

        index = self._extract_result_index(message)
        if index is not None and self._has_cached_search_results_for_message(message):
            return {"action": "download_result", "index": index}

        if re.search(r"(搜|搜索|找|有没有|推荐|来点|想看|看看|风格|画风|类型|题材|主题|高质量|热门|人气|看的人|排行|榜)", message):
            query = self._cleanup_search_query(message)
            if query:
                return {"action": "search", "query": query}

        return {"action": "none"}

    @staticmethod
    def _cleanup_search_query(message: str) -> str:
        query = message.strip()
        query = re.sub(r"^(帮我|帮忙|麻烦|请|给我|想)?\s*", "", query)
        query = re.sub(r"(搜一下|搜索一下|找一下|搜|搜索|找|有没有|推荐|来点|想看|看看)", " ", query)
        query = re.sub(r"(本子|漫画|禁漫|JM|jm|车号|一下|看过没|点|吗|啊|吧|的)", " ", query)
        query = re.sub(r"\s+", " ", query).strip(" ，,。.!！?")
        return query

    @staticmethod
    def _extract_result_index(message: str) -> int | None:
        number_match = re.search(r"第\s*(\d+)\s*个|(\d+)\s*号|第\s*(\d+)", message)
        if number_match:
            for group in number_match.groups():
                if group:
                    return int(group)

        chinese_digits = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        chinese_match = re.search(r"第?\s*([一二两三四五六七八九十])\s*个", message)
        if chinese_match:
            return chinese_digits.get(chinese_match.group(1))
        return None

    def _has_cached_search_results_for_message(self, message: str) -> bool:
        return bool(
            re.search(
                r"(下载|下|要|来|发).*(第\s*(?:\d+|[一二两三四五六七八九十])|(?:\d+|[一二两三四五六七八九十])\s*(?:个|号))",
                message,
            )
        )

    @staticmethod
    def _parse_result_index(index: Any) -> int:
        try:
            value = int(index)
        except (TypeError, ValueError):
            value = 1
        return max(value, 1)

    def _remember_search_results(self, event: AstrMessageEvent, page: Any) -> None:
        results = list(page.iter_id_title())[:self.search_result_limit]
        self._search_cache[self._cache_key(event)] = [
            (str(album_id), str(title)) for album_id, title in results
        ]

    def _remember_search_items(self, event: AstrMessageEvent, items: list[dict[str, Any]]) -> None:
        self._search_cache[self._cache_key(event)] = [
            (str(item["id"]), str(item.get("title", "")))
            for item in items[:self.search_result_limit]
        ]

    def _get_cached_search_album_id(self, event: AstrMessageEvent, index: int) -> str | None:
        results = self._search_cache.get(self._cache_key(event), [])
        if 1 <= index <= len(results):
            return results[index - 1][0]
        return None

    @staticmethod
    def _cache_key(event: AstrMessageEvent) -> str:
        return event.get_session_id() or event.unified_msg_origin

    def _download_album(self, album_id: str):
        jmcomic = self._load_jmcomic()
        option = self._create_option()
        album, _downloader = jmcomic.download_album(album_id, option=option)
        image_dirs = [Path(option.decide_image_save_dir(photo)) for photo in album]
        try:
            pdf_path = self._build_compressed_pdf(album.album_id, album.name, image_dirs)
            return album, pdf_path
        finally:
            if self.cleanup_download_images:
                self._cleanup_download_dirs(image_dirs)

    def _download_photo(self, photo_id: str):
        jmcomic = self._load_jmcomic()
        option = self._create_option()
        photo, _downloader = jmcomic.download_photo(photo_id, option=option)
        image_dirs = [Path(option.decide_image_save_dir(photo))]
        try:
            pdf_path = self._build_compressed_pdf(photo.photo_id, photo.name, image_dirs)
            return photo, pdf_path
        finally:
            if self.cleanup_download_images:
                self._cleanup_download_dirs(image_dirs)

    async def _send_pdf_file(self, event: AstrMessageEvent, pdf_path: Path) -> dict[str, Any]:
        pdf_size = pdf_path.stat().st_size
        targets = self._build_pdf_send_targets(event)
        sent_targets: list[dict[str, str]] = []
        has_timeout = False

        if not targets:
            try:
                await event.send(
                    event.plain_result(
                        "\n".join(
                            [
                                "PDF 没有可发送的目标会话。",
                                f"文件：{pdf_path.name}",
                                f"大小：{self._format_size(pdf_size)}",
                            ]
                        )
                    )
                )
            except Exception:
                logger.exception("JMComic PDF send target notice failed")
            self._delete_file(pdf_path)
            return {"status": "failed", "targets": []}

        for target in targets:
            try:
                await self._send_components_to_target(
                    event,
                    target,
                    [Comp.File(name=pdf_path.name, file=str(pdf_path))],
                )
                sent_targets.append({**target, "send_status": "sent"})
                logger.info(f"JMComic PDF sent to {target['label']}: {pdf_path}")
            except Exception as exc:
                if self._is_websocket_timeout(exc):
                    has_timeout = True
                    sent_targets.append({**target, "send_status": "timeout"})
                    logger.warning(
                        "JMComic PDF upload API timed out, but platform upload may still continue. "
                        f"target={target['label']}, file={pdf_path}, size={pdf_size}"
                    )
                    continue

                logger.exception(f"JMComic PDF send failed: target={target['label']}")

        if has_timeout:
            asyncio.create_task(self._delete_file_later(pdf_path))
        else:
            self._delete_file(pdf_path)

        if not sent_targets:
            await self._send_pdf_failure_notice(event, pdf_path.name, pdf_size)
            return {"status": "failed", "targets": []}

        return {
            "status": "timeout" if has_timeout else "sent",
            "targets": sent_targets,
        }

    async def _send_pdf_failure_notice(
        self,
        event: AstrMessageEvent,
        filename: str,
        pdf_size: int,
    ) -> None:
        try:
            await event.send(
                event.plain_result(
                    "\n".join(
                        [
                            "PDF 上传失败，没有目标成功接收。",
                            f"文件：{filename}",
                            f"大小：{self._format_size(pdf_size)}",
                        ]
                    )
                )
            )
        except Exception:
            logger.exception("JMComic PDF send failure notice failed")

    def _build_pdf_send_targets(self, event: AstrMessageEvent) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        seen: set[str] = set()
        current_session = str(getattr(event, "unified_msg_origin", "") or "")

        if self.send_to_current_session:
            key = current_session or "current"
            seen.add(key)
            targets.append(
                {
                    "kind": "current",
                    "session": current_session,
                    "label": current_session or "current session",
                }
            )

        for raw_session in self.target_sessions:
            session = self._normalize_target_session(raw_session, event)
            if not session or session in seen:
                continue
            seen.add(session)
            targets.append({"kind": "session", "session": session, "label": session})

        return targets

    def _normalize_target_session(self, raw_session: str, event: AstrMessageEvent) -> str:
        session = str(raw_session).strip()
        if not session:
            return ""

        if session.count(":") >= 2:
            return session

        platform_id = ""
        try:
            platform_id = str(event.get_platform_id() or "")
        except Exception:
            platform_id = ""

        match = re.fullmatch(r"(?i)(group|群)[:：](\d+)", session)
        if match and platform_id:
            return f"{platform_id}:GroupMessage:{match.group(2)}"

        match = re.fullmatch(r"(?i)(friend|private|私聊|好友)[:：](\d+)", session)
        if match and platform_id:
            return f"{platform_id}:FriendMessage:{match.group(2)}"

        logger.warning(
            "JMComic ignored invalid target session. Use full unified session like "
            f"default:GroupMessage:123456, got={raw_session}"
        )
        return ""

    async def _send_components_to_target(
        self,
        event: AstrMessageEvent,
        target: dict[str, str],
        components: list[Any],
    ) -> None:
        if target["kind"] == "current":
            await event.send(event.chain_result(components))
            return

        matched = await self.context.send_message(
            target["session"],
            MessageChain(chain=components),
        )
        if not matched:
            raise RuntimeError(f"未找到目标平台：{target['session']}")

    async def _delete_file_later(self, path: Path) -> None:
        await asyncio.sleep(self.delete_after_timeout_seconds)
        self._delete_file(path)

    async def _notify_pdf_password(
        self,
        event: AstrMessageEvent,
        send_result: dict[str, Any],
        password: str | None,
    ) -> None:
        if not password:
            return

        targets = [
            target
            for target in send_result.get("targets", [])
            if target.get("kind") == "current" or self.notify_password_to_target_sessions
        ]
        if not targets or send_result.get("status") == "failed":
            return

        if any(target.get("send_status") == "timeout" for target in targets):
            asyncio.create_task(self._notify_pdf_password_later(event, password, targets))
            return

        await self._send_pdf_password_notice(event, password, targets)

    async def _notify_pdf_password_later(
        self,
        event: AstrMessageEvent,
        password: str,
        targets: list[dict[str, str]],
    ) -> None:
        await asyncio.sleep(self.password_notice_delay_seconds)
        await self._send_pdf_password_notice(event, password, targets)

    async def _send_pdf_password_notice(
        self,
        event: AstrMessageEvent,
        password: str,
        targets: list[dict[str, str]],
    ) -> None:
        for target in targets:
            try:
                await self._send_components_to_target(
                    event,
                    target,
                    self._build_pdf_password_components(event, target, password),
                )
            except Exception:
                logger.exception(f"JMComic PDF password notice failed: target={target['label']}")

    def _build_pdf_password_components(
        self,
        event: AstrMessageEvent,
        target: dict[str, str],
        password: str,
    ) -> list[Any]:
        notice_text = f" 已发送，PDF 密码：{password}"
        if target.get("kind") != "current" or not self._current_event_supports_qq_at(event):
            return [Comp.Plain(notice_text.strip())]

        sender_id = self._get_sender_id(event)
        if not sender_id:
            return [Comp.Plain(notice_text.strip())]
        return [Comp.At(qq=sender_id), Comp.Plain(notice_text)]

    @staticmethod
    def _current_event_supports_qq_at(event: AstrMessageEvent) -> bool:
        if not event.get_group_id():
            return False
        try:
            platform_name = str(event.get_platform_name() or "").lower()
        except Exception:
            platform_name = ""
        return platform_name in {"aiocqhttp", "qq"} or "cqhttp" in platform_name

    @staticmethod
    def _get_sender_id(event: AstrMessageEvent) -> str:
        sender_id = event.get_sender_id()
        if sender_id:
            return str(sender_id)

        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        sender_id = getattr(sender, "user_id", "")
        return str(sender_id) if sender_id else ""

    def _build_compressed_pdf(self, entity_id: Any, title: str, image_dirs: list[Path]) -> Path:
        image_files = self._collect_image_files(image_dirs)
        if not image_files:
            raise RuntimeError("没有找到已下载图片，无法生成 PDF。")

        output_path = self.pdf_dir / f"{self._safe_filename(f'JM{entity_id}-{title}')}.pdf"
        last_error: Exception | None = None

        for max_side, quality in self.pdf_compress_steps:
            try:
                self._write_compressed_pdf(image_files, output_path, max_side, quality)
                if output_path.stat().st_size <= self.max_send_bytes:
                    logger.info(
                        f"JMComic compressed PDF generated: {output_path}, "
                        f"quality={quality}, max_side={max_side}, size={output_path.stat().st_size}"
                    )
                    return output_path
            except Exception as exc:
                last_error = exc
                logger.exception(
                    f"JMComic compressed PDF generation failed: quality={quality}, max_side={max_side}"
                )

        if output_path.exists():
            logger.warning(
                f"JMComic compressed PDF is still large: {output_path}, size={output_path.stat().st_size}"
            )
            return output_path

        raise RuntimeError(f"PDF 生成失败：{last_error}")

    def _generate_pdf_password(self) -> str:
        upper_bound = 10 ** self.pdf_password_digits
        return f"{secrets.randbelow(upper_bound):0{self.pdf_password_digits}d}"

    @staticmethod
    def _encrypt_pdf(pdf_path: Path, password: str) -> None:
        import pikepdf

        temp_path = pdf_path.with_name(f"{pdf_path.stem}.encrypted{pdf_path.suffix}")
        try:
            with pikepdf.open(pdf_path) as pdf:
                pdf.save(
                    temp_path,
                    encryption=pikepdf.Encryption(user=password, owner=password),
                )
            temp_path.replace(pdf_path)
            logger.info(f"JMComic PDF encrypted: {pdf_path}")
        finally:
            temp_path.unlink(missing_ok=True)

    def _write_compressed_pdf(
        self,
        image_files: list[Path],
        output_path: Path,
        max_side: int,
        quality: int,
    ) -> None:
        import img2pdf
        from PIL import Image

        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.compress_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="jm_pdf_", dir=str(self.compress_dir)) as temp_dir:
            temp_path = Path(temp_dir)
            compressed_images = []
            for index, image_path in enumerate(image_files, start=1):
                with Image.open(image_path) as image:
                    image.load()
                    if image.mode in ("RGBA", "LA") or (
                        image.mode == "P" and "transparency" in image.info
                    ):
                        background = Image.new("RGB", image.size, (255, 255, 255))
                        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
                        image = background
                    elif image.mode != "RGB":
                        image = image.convert("RGB")

                    if max(image.size) > max_side:
                        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

                    compressed_path = temp_path / f"{index:05d}.jpg"
                    image.save(
                        compressed_path,
                        "JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    compressed_images.append(str(compressed_path))

            with output_path.open("wb") as file:
                file.write(img2pdf.convert(compressed_images))

    @staticmethod
    def _collect_image_files(image_dirs: list[Path]) -> list[Path]:
        image_files: list[Path] = []
        for image_dir in image_dirs:
            if not image_dir.exists():
                continue
            image_files.extend(
                path
                for path in image_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            )
        return sorted(image_files, key=JMComicPlugin._natural_key)

    @staticmethod
    def _natural_key(path: Path) -> list[Any]:
        return [
            int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", str(path))
        ]

    def _cleanup_download_dirs(self, image_dirs: list[Path]) -> None:
        for image_dir in image_dirs:
            try:
                shutil.rmtree(image_dir, ignore_errors=True)
            except Exception:
                logger.exception(f"Failed to cleanup JMComic image dir: {image_dir}")
        self._prune_empty_dirs(self.download_dir)

    @staticmethod
    def _prune_empty_dirs(root: Path) -> None:
        if not root.exists():
            return

        for path in sorted(
            [path for path in root.rglob("*") if path.is_dir()],
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                continue
            except Exception:
                logger.exception(f"Failed to prune empty JMComic dir: {path}")

    @staticmethod
    def _delete_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
            logger.info(f"JMComic temporary PDF deleted: {path}")
        except Exception:
            logger.exception(f"Failed to delete JMComic temporary PDF: {path}")

    @staticmethod
    def _is_websocket_timeout(exc: Exception) -> bool:
        return "WebSocket API call timeout" in str(exc)

    def _claim_event(self, event: AstrMessageEvent, action: str, target: str) -> bool:
        now = time.monotonic()
        for key, created_at in list(self._handled_events.items()):
            if now - created_at > 300:
                self._handled_events.pop(key, None)

        key = (self._event_identity(event), action, str(target))
        if key in self._handled_events:
            return False

        self._handled_events[key] = now
        return True

    @staticmethod
    def _event_identity(event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        message_id = getattr(message_obj, "message_id", None)
        if message_id is not None:
            return str(message_id)
        return f"{id(event)}:{getattr(event, 'message_str', '')}"

    @staticmethod
    def _safe_filename(name: str, max_length: int = 120) -> str:
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
        return (filename or "jmcomic")[:max_length]

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
            value /= 1024

    @staticmethod
    def _extract_search_query(message: str) -> str:
        match = re.search(
            r"(?i)jm\s*(?:搜索|搜|search)\s*(.+)$",
            message.strip(),
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _truncate_text(text: Any, limit: int = 42) -> str:
        value = str(text).replace("\n", " ").strip()
        return value if len(value) <= limit else f"{value[:limit]}..."

    def _format_search_page(self, search_query: str, page: Any) -> str:
        rows = [
            f"搜索：{search_query}",
            f"结果：{page.total}，第 1/{page.page_count or 1} 页",
        ]

        results = list(page.iter_id_title())[:self.search_result_limit]
        if not results:
            rows.append("没有找到结果。")
            return "\n".join(rows)

        for index, (album_id, title) in enumerate(results, start=1):
            rows.append(f"{index}. JM{album_id} {self._truncate_text(title)}")

        rows.append("发送 jm <id> 下载。")
        return "\n".join(rows)

    def _format_search_result(self, result: dict[str, Any]) -> str:
        rows = [f"搜索：{result['query']}"]

        attempts = result.get("attempts") or []
        if attempts:
            query_labels = []
            for attempt in attempts:
                keyword = attempt["keyword"]
                main_tag = int(attempt.get("main_tag", 0))
                order_by = str(attempt.get("order_by", "mr"))
                order_label = SEARCH_ORDER_LABELS.get(order_by, order_by)
                label = f"标签:{keyword}" if main_tag == 3 else keyword
                label = f"{label}({order_label})"
                if label not in query_labels:
                    query_labels.append(label)
            rows.append(f"智能检索：{' / '.join(query_labels[:self.search_plan_limit])}")

        items = result.get("items") or []
        rows.append(f"结果：{len(items)}")
        if not items:
            rows.append("没有找到结果。")
            return "\n".join(rows)

        for index, item in enumerate(items[:self.search_result_limit], start=1):
            metrics = self._format_search_metrics(item)
            rows.append(
                f"{index}. JM{item['id']} {self._truncate_text(item.get('title', ''))}{metrics}"
            )

        rows.append("发送 jm <id> 下载。")
        return "\n".join(rows)

    def _format_search_metrics(self, item: dict[str, Any]) -> str:
        views = item.get("views")
        likes = item.get("likes")
        page_count = item.get("page_count")
        chapter_count = item.get("chapter_count")
        parts = []
        if isinstance(page_count, int) and page_count > 0:
            parts.append(f"{page_count}页")
        elif isinstance(chapter_count, int) and chapter_count > 1:
            parts.append(f"{chapter_count}章")
        if isinstance(views, int) and views > 0:
            parts.append(f"观看 {self._format_count(views)}")
        if isinstance(likes, int) and likes > 0:
            parts.append(f"喜欢 {self._format_count(likes)}")
        return f"（{' / '.join(parts)}）" if parts else ""

    @staticmethod
    def _format_count(value: int) -> str:
        if value >= 10000:
            return f"{value / 10000:.1f}万"
        return str(value)

    def _pdf_state(self) -> dict[Path, tuple[int, int]]:
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        return {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in self.pdf_dir.glob("*.pdf")
            if path.is_file()
        }

    def _find_generated_pdf(self, entity_id: Any, before_state: dict[Path, tuple[int, int]]) -> Path:
        candidates = [
            path
            for path in self.pdf_dir.glob("*.pdf")
            if path.is_file() and before_state.get(path) != (path.stat().st_mtime_ns, path.stat().st_size)
        ]

        entity_id_text = str(entity_id)
        id_candidates = [path for path in candidates if entity_id_text in path.name]
        if id_candidates:
            return max(id_candidates, key=lambda path: path.stat().st_mtime_ns)

        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime_ns)

        existing = [
            path
            for path in self.pdf_dir.glob("*.pdf")
            if path.is_file() and entity_id_text in path.name
        ]
        if existing:
            return max(existing, key=lambda path: path.stat().st_mtime_ns)

        raise RuntimeError("PDF 生成失败，请确认容器内已安装 img2pdf，或查看 AstrBot 日志。")

    @staticmethod
    def _format_list(items: list[Any], limit: int = 8) -> str:
        if not items:
            return "-"
        values = [str(item) for item in items[:limit]]
        if len(items) > limit:
            values.append("...")
        return "、".join(values)

    def _format_album(self, album: Any) -> str:
        episodes = []
        for episode in album.episode_list[:8]:
            photo_id, index, title, *_rest = episode
            episodes.append(f"{index}. {title} (P{photo_id})")
        if len(album.episode_list) > 8:
            episodes.append("...")

        return "\n".join(
            [
                f"JM{album.album_id}",
                f"标题：{album.name}",
                f"作者：{self._format_list(album.authors)}",
                f"标签：{self._format_list(album.tags)}",
                f"作品：{self._format_list(album.works)}",
                f"页数：{album.page_count}",
                f"章节数：{len(album)}",
                f"发布：{album.pub_date}",
                f"更新：{album.update_date}",
                f"观看：{album.views} / 喜欢：{album.likes} / 评论：{album.comment_count}",
                "章节：",
                *episodes,
            ]
        )

    async def terminate(self):
        logger.info("JMComic plugin terminated")
