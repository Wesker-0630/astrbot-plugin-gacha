"""抽老婆插件（AstrBot）

功能：
- 每日抽卡，重复卡会累计持有数量
- 老婆图鉴（内置收集进度）、我的老婆、收集进度
- 兑换、NTR 抢夺（支持指定角色，NTR 成功附带角色图片）
- WebUI 插件配置（_conf_schema.json + AstrBotConfig）
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

# =============================================================================
# 路径
# =============================================================================
PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_ROOT, "data")
USER_DATA_PATH = os.path.join(DATA_DIR, "user_data.json")
USER_DATA_BACKUP_PATH = USER_DATA_PATH + ".bak"
WIVES_PATH = os.path.join(PLUGIN_ROOT, "wives.json")

# 抽老婆允许不带唤醒前缀触发；同时兼容 @机器人 后跟指令的情况。
DRAW_PATTERN = r"^(?:\s*@.*?\(\d+\)\s*)*/?\s*抽老婆\s*$"

# 默认值兜底（真正的默认值以 _conf_schema.json 为准）
FALLBACK_WEAR_LIST = ["崭新出厂", "略有磨损", "久经沙场"]

# jsDelivr 多 CDN 节点。部分网络会阻断 cdn.jsdelivr.net，gcore 节点通常可直连。
JSDELIVR_HOSTS = (
    "gcore.jsdelivr.net",
    "fastly.jsdelivr.net",
    "jsdelivr.b-cdn.net",
    "cdn.jsdelivr.net",
)

_SAVE_LOCK = threading.RLock()


def _load_schema_defaults() -> dict[str, dict[str, Any]]:
    """从 _conf_schema.json 读取各分组默认值，保证代码与 WebUI 配置一致。"""
    path = os.path.join(PLUGIN_ROOT, "_conf_schema.json")
    try:
        with open(path, encoding="utf-8-sig") as f:
            schema = json.load(f)
    except (OSError, ValueError):
        return {}

    defaults: dict[str, dict[str, Any]] = {}
    if not isinstance(schema, dict):
        return defaults
    for section, node in schema.items():
        if not isinstance(node, dict) or node.get("type") != "object":
            continue
        items = node.get("items")
        if not isinstance(items, dict):
            continue
        section_defaults = {}
        for key, item in items.items():
            if isinstance(item, dict) and "default" in item:
                section_defaults[key] = item["default"]
        defaults[section] = section_defaults
    return defaults


SCHEMA_DEFAULTS = _load_schema_defaults()


# =============================================================================
# 数据读写
# =============================================================================
def _default_user() -> dict[str, Any]:
    return {
        "wives": {},
        "daily_draw": 0,
        "last_draw_date": "",
        "rob_used": False,
        "last_rob_date": "",
        "ntr_fail_date": "",
        # 新字段：累计抽卡次数（仅统计 /抽老婆）
        "total_draws": 0,
    }


def _normalize_user_data(raw: Any) -> dict[str, Any]:
    """兼容旧存档：补字段、修正类型、清理非法数量。"""
    ud = raw if isinstance(raw, dict) else {}
    for key, value in _default_user().items():
        ud.setdefault(key, value)

    wives = ud.get("wives")
    if not isinstance(wives, dict):
        wives = {}
    clean_wives: dict[str, int] = {}
    for wid, cnt in wives.items():
        try:
            count = int(cnt)
        except (TypeError, ValueError):
            continue
        if count > 0:
            clean_wives[str(wid)] = count
    ud["wives"] = clean_wives

    for key in ("daily_draw", "total_draws"):
        try:
            value = int(ud.get(key, 0))
        except (TypeError, ValueError):
            value = 0
        ud[key] = max(0, value)

    for key in ("last_draw_date", "last_rob_date", "ntr_fail_date"):
        ud[key] = str(ud.get(key) or "")

    ud["rob_used"] = bool(ud.get("rob_used", False))
    return ud


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _atomic_write_json(path: str, payload: Any, *, indent: int = 2) -> None:
    """先写临时文件再替换，避免进程中断导致存档/卡池损坏。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=indent)
    fd, temp_path = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def load_wives_config() -> list[dict[str, Any]]:
    if not os.path.exists(WIVES_PATH):
        save_wives_config([])
        return []
    try:
        raw = _read_json(WIVES_PATH)
    except (OSError, ValueError) as exc:
        logger.error(f"[Gacha] 读取 wives.json 失败：{exc}")
        return []
    if not isinstance(raw, list):
        logger.error("[Gacha] wives.json 必须是数组，已忽略。")
        return []

    cleaned: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            wife_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if wife_id in seen_ids:
            logger.warning(f"[Gacha] wives.json 中存在重复 ID={wife_id}，已跳过重复项。")
            continue
        seen_ids.add(wife_id)
        cleaned.append(
            {
                "id": wife_id,
                "name": name,
                "img": str(item.get("img") or "").strip(),
            }
        )
    return cleaned


def save_wives_config(wives_list: list[dict[str, Any]]) -> None:
    with _SAVE_LOCK:
        _atomic_write_json(WIVES_PATH, wives_list)


def load_user_data() -> dict[str, dict[str, Any]]:
    raw: dict[str, Any] | None = None
    for path in (USER_DATA_PATH, USER_DATA_BACKUP_PATH):
        try:
            candidate = _read_json(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.error(f"[Gacha] 读取存档 {os.path.basename(path)} 失败：{exc}")
            continue
        if isinstance(candidate, dict):
            raw = candidate
            break
        logger.error(
            f"[Gacha] 存档 {os.path.basename(path)} 不是 JSON 对象，尝试下一份备份。"
        )
    if raw is None:
        return {}

    data: dict[str, dict[str, Any]] = {}
    for uid, ud in raw.items():
        data[str(uid)] = _normalize_user_data(ud)
    return data


def save_user_data(data: dict[str, dict[str, Any]]) -> None:
    with _SAVE_LOCK:
        # 只有当前存档是合法 JSON 时才把它备份为 .bak，
        # 避免用损坏文件覆盖掉原本可用的备份。
        try:
            if os.path.exists(USER_DATA_PATH) and os.path.getsize(USER_DATA_PATH) > 0:
                _read_json(USER_DATA_PATH)
                shutil.copy2(USER_DATA_PATH, USER_DATA_BACKUP_PATH)
        except (OSError, ValueError):
            pass
        _atomic_write_json(USER_DATA_PATH, data)


# =============================================================================
# 图片渲染
# =============================================================================
def _safe_color(value: Any, default: str) -> str:
    """校验十六进制颜色，配置写错时回退到默认色，避免整张图渲染失败。"""
    raw = str(value or "").strip()
    try:
        ImageColor.getrgb(raw)
        return raw
    except (TypeError, ValueError):
        return default


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path] if font_path else []
    candidates += ["simhei.ttf", "msyh.ttc", "msyhbd.ttc", "simsun.ttc"]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, ValueError):
            continue
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _new_temp_path(prefix: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=".jpg", dir=DATA_DIR)
    os.close(fd)
    return path


def _cleanup_old_temp_files(max_age_seconds: float = 24 * 3600) -> None:
    try:
        names = os.listdir(DATA_DIR)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.endswith(".jpg"):
            continue
        if not name.startswith(("grid_", "pool_", "progress_")):
            continue
        path = os.path.join(DATA_DIR, name)
        try:
            if now - os.path.getmtime(path) > max_age_seconds:
                os.remove(path)
        except OSError:
            continue


def _resolve_local_image(assets_dir: str, img_val: Any) -> str | None:
    raw = str(img_val or "").strip()
    if not raw or raw.startswith(("http://", "https://")):
        return None
    if os.path.isabs(raw):
        return raw if os.path.isfile(raw) else None
    for base in (assets_dir, PLUGIN_ROOT):
        path = os.path.join(base, raw)
        if os.path.isfile(path):
            return path
    return None


def _url_basename(url: str) -> str:
    try:
        path = urllib.parse.urlparse(url).path
        return urllib.parse.unquote(os.path.basename(path))
    except Exception:
        return ""


def _jsdelivr_url_candidates(url: str) -> list[str]:
    """为 jsDelivr URL 生成多节点候选，原节点优先。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return [url]
    host = (parsed.hostname or "").lower()
    if host not in JSDELIVR_HOSTS:
        return [url]

    ordered = [host] + [item for item in JSDELIVR_HOSTS if item != host]
    candidates: list[str] = []
    for item in ordered:
        netloc = item if parsed.port is None else f"{item}:{parsed.port}"
        candidates.append(
            urllib.parse.urlunsplit(
                (parsed.scheme or "https", netloc, parsed.path, parsed.query, "")
            )
        )
    return candidates


def _cache_key_url(url: str) -> str:
    """让 jsDelivr 各节点共用同一份缓存。"""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return url
    if (parsed.hostname or "").lower() in JSDELIVR_HOSTS:
        return urllib.parse.urlunsplit(
            ("https", "cdn.jsdelivr.net", parsed.path, parsed.query, "")
        )
    return url


def _download_image_cached(url: str, cache_dir: str) -> str | None:
    """下载远程图片到本地缓存；jsDelivr 节点失败时自动切换其他节点。"""
    os.makedirs(cache_dir, exist_ok=True)
    basename = _url_basename(url)
    ext = os.path.splitext(basename)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        ext = ".jpg"
    digest = hashlib.sha256(_cache_key_url(url).encode("utf-8")).hexdigest()[:24]
    cache_path = os.path.join(cache_dir, digest + ext)
    if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
        return cache_path

    last_error: Exception | None = None
    for candidate in _jsdelivr_url_candidates(url):
        try:
            request = urllib.request.Request(
                candidate, headers={"User-Agent": "AstrBot-Gacha/1.3.1"}
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read(20 * 1024 * 1024 + 1)
            if not data or len(data) > 20 * 1024 * 1024:
                raise ValueError("empty or larger than 20MB")
            # 简单校验确实是图片，避免把错误页缓存成图片
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            fd, temp_path = tempfile.mkstemp(
                dir=cache_dir, prefix=".download.", suffix=ext
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, cache_path)
            finally:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass
            return cache_path
        except Exception as exc:
            last_error = exc
            continue

    logger.warning(f"[Gacha] 远程图片下载失败 {url}: {last_error}")
    return None


def _resolve_render_image(
    assets_dir: str, img_val: Any, cache_dir: str
) -> str | None:
    """解析图鉴缩略图：本地文件优先，否则从 CDN 下载并缓存。"""
    local_path = _resolve_local_image(assets_dir, img_val)
    if local_path:
        return local_path

    raw = str(img_val or "").strip()
    if not raw.startswith(("http://", "https://")):
        return None

    # 有本地同名文件时直接使用，避免图鉴一次拉取上百张网络图
    basename = _url_basename(raw)
    if basename:
        candidate = os.path.join(assets_dir, basename)
        if os.path.isfile(candidate):
            return candidate
    return _download_image_cached(raw, cache_dir)


def _load_thumb(path: str, size: int, gray: bool = False) -> Image.Image | None:
    try:
        with Image.open(path) as image:
            thumb = image.convert("RGBA").resize(
                (size, size), Image.Resampling.LANCZOS
            )
    except Exception as exc:
        logger.warning(f"[Gacha] 图片读取失败 {path}: {exc}")
        return None
    if gray:
        overlay = Image.new("RGBA", thumb.size, (70, 70, 70, 165))
        thumb = Image.alpha_composite(thumb, overlay)
    return thumb


def _font_line_gap(font, default: int = 18) -> int:
    try:
        size = int(getattr(font, "size", default))
    except (TypeError, ValueError):
        size = default
    return max(12, size + 4)


def _fit_text(draw: ImageDraw.ImageDraw, text: Any, font, max_width: int) -> str:
    value = str(text)
    if max_width <= 0:
        return ""
    try:
        if draw.textlength(value, font=font) <= max_width:
            return value
    except Exception:
        return value
    suffix = "…"
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = value[:mid] + suffix
        try:
            width = draw.textlength(candidate, font=font)
        except Exception:
            return candidate
        if width <= max_width:
            low = mid
        else:
            high = mid - 1
    return value[:low] + suffix if low < len(value) else value


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    ratio: float,
    track_color: str = "#3a4252",
    fill_color: str = "#90ee90",
) -> None:
    ratio = max(0.0, min(1.0, float(ratio or 0.0)))
    try:
        draw.rounded_rectangle(
            [x, y, x + width, y + height], radius=height // 2, fill=track_color
        )
        if ratio > 0:
            fill_width = min(width, max(height, int(width * ratio)))
            draw.rounded_rectangle(
                [x, y, x + fill_width, y + height],
                radius=height // 2,
                fill=fill_color,
            )
    except Exception:
        draw.rectangle([x, y, x + width, y + height], fill=track_color)
        if ratio > 0:
            draw.rectangle([x, y, x + int(width * ratio), y + height], fill=fill_color)


def draw_progress_image(
    unique: int,
    total: int,
    acquired: int,
    duplicates: int,
    draws: int,
    *,
    font_path: str = "simhei.ttf",
    quality: int = 85,
    show_bar: bool = True,
) -> str:
    """生成收集进度表格图片。"""
    width, height = 660, 270
    img = Image.new("RGB", (width, height), color="#1f2430")
    draw = ImageDraw.Draw(img)

    font_title = _load_font(font_path, 36)
    font_label = _load_font(font_path, 18)
    font_value = _load_font(font_path, 28)

    percent = (unique / total * 100) if total else 0.0

    draw.text((30, 18), "老婆收集进度", font=font_title, fill="#ffffff")
    draw.text(
        (32, 66),
        f"已收集 {unique} / {total} 位角色，收集率 {percent:.1f}%",
        font=font_label,
        fill="#aab2c0",
    )

    cells = [
        (30, 102, "已收集", f"{unique} / {total}", "#87cefa"),
        (245, 102, "收集率", f"{percent:.1f}%", "#90ee90"),
        (460, 102, "累计获得", f"{acquired} 张", "#ffd700"),
        (30, 158, "重复获得", f"{duplicates} 张", "#ffb6c1"),
        (245, 158, "累计抽卡", f"{draws} 次", "#c9a7ff"),
        (460, 158, "未收集", f"{max(0, total - unique)} 位", "#b0b8c4"),
    ]
    for x, y, label, value, color in cells:
        draw.text((x, y), label, font=font_label, fill="#8b94a3")
        draw.text((x, y + 22), value, font=font_value, fill=color)

    if show_bar:
        _draw_progress_bar(draw, 30, 222, 600, 24, unique / total if total else 0.0)
    else:
        draw.text(
            (30, 224),
            "继续抽卡，集齐全部角色吧！",
            font=font_label,
            fill="#8b94a3",
        )

    path = _new_temp_path("progress")
    img.save(path, "JPEG", quality=quality, optimize=True)
    return path


def build_grid_image(
    item_list: list[dict[str, Any]],
    title_text: str,
    *,
    cols: int,
    thumb_size: int,
    text_height: int,
    asset_dir: str,
    cache_dir: str,
    font_path: str = "simhei.ttf",
    quality: int = 85,
    gold_color: str = "#ffd700",
    subtitle: str = "",
) -> str | None:
    """生成「我的老婆」网格图。"""
    if not item_list:
        return None

    cols = max(1, int(cols))
    thumb_size = max(80, int(thumb_size))
    font_title = _load_font(font_path, 32)
    font_subtitle = _load_font(font_path, 18)
    font_info = _load_font(font_path, max(14, min(24, thumb_size // 25)))
    # 根据字号自动撑高文字区，避免大缩略图时第二行被裁掉
    text_height = max(text_height, _font_line_gap(font_info) * 2 + 6, 30)
    header_height = 108 if subtitle else 84

    rows = (len(item_list) + cols - 1) // cols
    full_w = cols * thumb_size
    full_h = header_height + rows * (thumb_size + text_height) + 12

    canvas = Image.new("RGB", (full_w, full_h), color="#fffbe8")
    draw = ImageDraw.Draw(canvas)

    draw.text(
        (24, 18),
        _fit_text(draw, title_text, font_title, max(40, full_w - 48)),
        font=font_title,
        fill="#222222",
    )
    if subtitle:
        draw.text(
            (24, 62),
            _fit_text(draw, subtitle, font_subtitle, max(40, full_w - 48)),
            font=font_subtitle,
            fill="#666666",
        )

    for idx, item in enumerate(item_list):
        wife = item["wife"]
        count = int(item.get("count", 0))
        col = idx % cols
        row = idx // cols
        x0 = col * thumb_size
        y0 = header_height + row * (thumb_size + text_height)

        thumb = None
        local_path = _resolve_render_image(asset_dir, wife.get("img"), cache_dir)
        if local_path:
            thumb = _load_thumb(local_path, thumb_size, gray=False)

        if thumb is None:
            draw.rectangle(
                [x0, y0, x0 + thumb_size, y0 + thumb_size], fill="#d9d9d9"
            )
            draw.text(
                (x0 + 16, y0 + thumb_size // 2 - 10),
                "图片缺失",
                font=font_info,
                fill="#555555",
            )
        else:
            canvas.paste(thumb, (x0, y0), thumb)

        draw.rectangle(
            [x0, y0, x0 + thumb_size, y0 + thumb_size],
            outline=gold_color,
            width=3,
        )

        text_y = y0 + thumb_size + 6
        line1 = _fit_text(
            draw,
            f"ID:{wife['id']} {wife['name']}",
            font_info,
            thumb_size - 20,
        )
        draw.text((x0 + 10, text_y), line1, font=font_info, fill="#111111")
        if count > 0:
            line2 = _fit_text(
                draw, f"持有×{count}", font_info, thumb_size - 20
            )
            draw.text(
                (x0 + 10, text_y + _font_line_gap(font_info)),
                line2,
                font=font_info,
                fill="#8a6d00",
            )

    path = _new_temp_path("grid")
    canvas.save(path, "JPEG", quality=quality, optimize=True)
    return path


def build_pool_image(
    pool: list[dict[str, Any]],
    owned_counts: dict[str, int],
    *,
    cols: int,
    thumb_size: int,
    text_height: int,
    asset_dir: str,
    cache_dir: str,
    font_path: str = "simhei.ttf",
    quality: int = 85,
    gold_color: str = "#ffd700",
    show_count: bool = True,
    gray_unowned: bool = True,
    show_progress: bool = True,
    show_bar: bool = True,
) -> str | None:
    """生成「老婆图鉴」总览大图，并可把收集进度整合到图片顶部。"""
    if not pool:
        return None

    cols = max(1, int(cols))
    thumb_size = max(60, int(thumb_size))
    font_title = _load_font(font_path, 32)
    font_subtitle = _load_font(font_path, 18)
    font_info = _load_font(font_path, max(12, min(16, thumb_size // 7)))
    text_height = max(text_height, _font_line_gap(font_info) * 2 + 4, 26)

    total = len(pool)
    unique = len(owned_counts)
    acquired = sum(owned_counts.values())
    duplicates = max(0, acquired - unique)
    percent = (unique / total * 100) if total else 0.0

    header_height = 122 if show_progress else 84
    rows = (total + cols - 1) // cols
    full_w = cols * thumb_size
    full_h = header_height + rows * (thumb_size + text_height) + 12

    canvas = Image.new("RGB", (full_w, full_h), color="#fffbe8")
    draw = ImageDraw.Draw(canvas)

    draw.text(
        (20, 16),
        _fit_text(draw, "老婆图鉴总览", font_title, max(40, full_w - 40)),
        font=font_title,
        fill="#222222",
    )
    if show_progress:
        progress_line = (
            f"已收集 {unique}/{total}（{percent:.1f}%） · "
            f"累计获得 {acquired} 张 · 重复 {duplicates} 张"
        )
        draw.text(
            (22, 60),
            _fit_text(draw, progress_line, font_subtitle, max(40, full_w - 44)),
            font=font_subtitle,
            fill="#555555",
        )
        if show_bar:
            _draw_progress_bar(
                draw,
                22,
                92,
                max(20, full_w - 44),
                14,
                unique / total if total else 0.0,
                track_color="#e2dcc8",
                fill_color="#f0b400",
            )

    for idx, wife in enumerate(pool):
        wid = str(wife["id"])
        count = int(owned_counts.get(wid, 0))
        owned = count > 0
        col = idx % cols
        row = idx // cols
        x0 = col * thumb_size
        y0 = header_height + row * (thumb_size + text_height)

        thumb = None
        local_path = _resolve_render_image(asset_dir, wife.get("img"), cache_dir)
        if local_path:
            thumb = _load_thumb(
                local_path, thumb_size, gray=(gray_unowned and not owned)
            )

        if thumb is None:
            draw.rectangle(
                [x0, y0, x0 + thumb_size, y0 + thumb_size], fill="#d9d9d9"
            )
            draw.text(
                (x0 + 8, y0 + thumb_size // 2 - 8),
                "图片缺失",
                font=font_info,
                fill="#555555",
            )
        else:
            canvas.paste(thumb, (x0, y0), thumb)

        draw.rectangle(
            [x0, y0, x0 + thumb_size, y0 + thumb_size],
            outline=gold_color if owned else "#b8b8b8",
            width=3,
        )

        text_y = y0 + thumb_size + 4
        line1 = _fit_text(
            draw,
            f"ID:{wife['id']} {wife['name']}",
            font_info,
            thumb_size - 12,
        )
        draw.text((x0 + 6, text_y), line1, font=font_info, fill="#111111")

        if show_count:
            line2 = f"×{count}" if owned else "未获得"
            draw.text(
                (x0 + 6, text_y + _font_line_gap(font_info)),
                line2,
                font=font_info,
                fill="#8a6d00" if owned else "#999999",
            )

    path = _new_temp_path("pool")
    canvas.save(path, "JPEG", quality=quality, optimize=True)
    return path


# =============================================================================
# 插件主类
# =============================================================================
class WifeGachaPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config: dict[str, Any] = config if config is not None else {}
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(self.assets_path(), exist_ok=True)
        self.wives_pool = load_wives_config()
        self.user_data = load_user_data()
        _cleanup_old_temp_files()
        self.logger.info(
            f"[Gacha] 已加载 {len(self.wives_pool)} 个角色，"
            f"{len(self.user_data)} 位用户存档；图片目录：{self.assets_path()}"
        )

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    def cfg(self, section: str, key: str, default: Any = None) -> Any:
        if default is None:
            default = SCHEMA_DEFAULTS.get(section, {}).get(key)
        if isinstance(self.config, dict):
            section_data = self.config.get(section)
            if isinstance(section_data, dict):
                value = section_data.get(key)
                if value is not None:
                    return value
        return default

    def cfg_int(
        self,
        section: str,
        key: str,
        default: int,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        try:
            value = int(self.cfg(section, key, default))
        except (TypeError, ValueError):
            value = int(default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def cfg_float(
        self,
        section: str,
        key: str,
        default: float,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        try:
            value = float(self.cfg(section, key, default))
        except (TypeError, ValueError):
            value = float(default)
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def cfg_bool(self, section: str, key: str, default: bool) -> bool:
        value = self.cfg(section, key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "是", "开"}
        return bool(default)

    def cfg_str(self, section: str, key: str, default: str) -> str:
        value = self.cfg(section, key, default)
        if value is None:
            return default
        return str(value)

    def cfg_rate(self, section: str, key: str, default: float) -> float:
        """读取成功率。支持 0.6 或 60 两种写法，最终统一为 0~1。"""
        value = self.cfg_float(section, key, default, 0.0, 100.0)
        if value > 1.0:
            value /= 100.0
        return max(0.0, min(1.0, value))

    def assets_path(self) -> str:
        raw = self.cfg_str("basic", "assets_dir", "assets").strip() or "assets"
        if not os.path.isabs(raw):
            raw = os.path.join(PLUGIN_ROOT, raw)
        return os.path.abspath(raw)

    def image_cache_path(self) -> str:
        return os.path.join(DATA_DIR, "image_cache")

    def render_options(self) -> dict[str, Any]:
        return {
            "asset_dir": self.assets_path(),
            "cache_dir": self.image_cache_path(),
            "font_path": self.cfg_str("render", "font_path", "simhei.ttf"),
            "quality": self.cfg_int("render", "jpeg_quality", 85, 50, 100),
            "gold_color": _safe_color(
                self.cfg_str("basic", "gold_color", "#ffd700"), "#ffd700"
            ),
        }

    def wear_list(self) -> list[str]:
        raw = self.cfg_str("basic", "wear_list", ",".join(FALLBACK_WEAR_LIST))
        items = [part.strip() for part in re.split(r"[,，;；]", raw)]
        items = [part for part in items if part]
        return items or list(FALLBACK_WEAR_LIST)

    # ------------------------------------------------------------------
    # 数据辅助
    # ------------------------------------------------------------------
    def save_all_userdata(self) -> None:
        save_user_data(self.user_data)

    def reset_user_daily(self, user_id: str, today: str) -> dict[str, Any]:
        ud = self.user_data.get(user_id)
        if not isinstance(ud, dict):
            ud = _default_user()
        ud = _normalize_user_data(ud)
        # 先写回，保证新用户/修复后的字段不会因为提前 return 而丢失
        self.user_data[user_id] = ud

        if ud["last_draw_date"] != today:
            ud["daily_draw"] = 0
            ud["last_draw_date"] = today
        if ud["last_rob_date"] != today:
            ud["rob_used"] = False
            ud["last_rob_date"] = today
        return ud

    def _stats(self, ud: dict[str, Any] | None = None) -> dict[str, Any]:
        ud = ud if isinstance(ud, dict) else {}
        wives = ud.get("wives") if isinstance(ud.get("wives"), dict) else {}
        pool_ids = {str(wife["id"]) for wife in self.wives_pool}

        owned_counts: dict[str, int] = {}
        for wid, cnt in wives.items():
            try:
                count = int(cnt)
            except (TypeError, ValueError):
                continue
            if count > 0 and str(wid) in pool_ids:
                owned_counts[str(wid)] = count

        unique = len(owned_counts)
        total = len(self.wives_pool)
        acquired = sum(owned_counts.values())
        duplicates = max(0, acquired - unique)
        try:
            draws = max(0, int(ud.get("total_draws", 0)))
        except (TypeError, ValueError):
            draws = 0
        percent = (unique / total * 100) if total else 0.0
        return {
            "owned_counts": owned_counts,
            "unique": unique,
            "total": total,
            "acquired": acquired,
            "duplicates": duplicates,
            "draws": draws,
            "percent": percent,
        }

    @staticmethod
    def _progress_text(stats: dict[str, Any]) -> str:
        return (
            f"已收集 {stats['unique']}/{stats['total']}"
            f"（{stats['percent']:.1f}%） · "
            f"累计获得 {stats['acquired']} 张 · "
            f"重复 {stats['duplicates']} 张"
        )

    def _find_wife(self, keyword: str) -> dict[str, Any] | None:
        keyword = str(keyword or "").strip()
        if not keyword:
            return None
        if keyword.isdigit():
            target_id = int(keyword)
            for wife in self.wives_pool:
                if int(wife["id"]) == target_id:
                    return wife
        exact = [wife for wife in self.wives_pool if wife["name"] == keyword]
        if len(exact) == 1:
            return exact[0]
        low = keyword.lower()
        fuzzy = [wife for wife in self.wives_pool if low in wife["name"].lower()]
        return fuzzy[0] if fuzzy else None

    def _match_owned_wife(
        self, target_wives: dict[str, int], keyword: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """在目标用户已拥有的角色中查找指定角色。"""
        keyword = str(keyword or "").strip()
        owned: list[tuple[str, dict[str, Any]]] = []
        for wid, cnt in target_wives.items():
            try:
                count = int(cnt)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            wife = next(
                (w for w in self.wives_pool if str(w["id"]) == str(wid)), None
            )
            if wife:
                owned.append((str(wid), wife))

        if not owned or not keyword:
            return None, []

        if keyword.isdigit():
            for wid, wife in owned:
                if wid == keyword:
                    return wife, []
            return None, []

        exact = [wife for _, wife in owned if wife["name"] == keyword]
        if len(exact) == 1:
            return exact[0], []
        if len(exact) > 1:
            return None, exact

        low = keyword.lower()
        fuzzy = [wife for _, wife in owned if low in wife["name"].lower()]
        if len(fuzzy) == 1:
            return fuzzy[0], []
        return None, fuzzy

    async def _image_component(self, img_val: Any) -> Comp.Image | None:
        raw = str(img_val or "").strip()
        if not raw:
            return None

        path: str | None = None
        if raw.startswith(("http://", "https://")):
            # 先把 CDN URL 解析成本地文件/缓存再发送。
            # AstrBot 的 aiohttp 下载不一定走系统代理，直接传 URL 在部分网络会发送失败。
            try:
                path = await asyncio.to_thread(
                    _resolve_render_image,
                    self.assets_path(),
                    raw,
                    self.image_cache_path(),
                )
            except Exception as exc:
                self.logger.warning(f"[Gacha] 远程图片解析失败 {raw}: {exc}")
            if not path:
                # 本地没有同名图，且所有 CDN 节点都下载失败：
                # 跳过图片而不是把裸 URL 交给平台，避免整条消息链发送失败。
                self.logger.warning(
                    f"[Gacha] 图片无法下载且本地无同名文件，跳过图片：{raw}"
                )
                return None
        else:
            path = _resolve_local_image(self.assets_path(), raw)
            if not path:
                self.logger.warning(f"[Gacha] 图片不存在：{raw}")
                return None

        try:
            return Comp.Image.fromFileSystem(path)
        except Exception as exc:
            self.logger.warning(f"[Gacha] 图片加载失败 {path}: {exc}")
            return None

    async def _append_image(self, chain: list, img_val: Any) -> None:
        component = await self._image_component(img_val)
        if component is not None:
            chain.append(component)

    @staticmethod
    def _extract_plain_text(event: AstrMessageEvent) -> str:
        parts: list[str] = []
        try:
            for component in event.message_obj.message:
                if isinstance(component, Comp.Plain):
                    parts.append(component.text or "")
        except Exception:
            return ""
        return "".join(parts)

    @staticmethod
    def _extract_target_id(event: AstrMessageEvent) -> str | None:
        self_id = str(event.get_self_id() or "")
        try:
            for component in event.message_obj.message:
                if isinstance(component, Comp.At):
                    qq = str(component.qq)
                    if qq in {"", "all", self_id}:
                        continue
                    return qq
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # 管理员指令
    # ------------------------------------------------------------------
    @filter.command("添加老婆", priority=100)
    async def add_wife(self, event: AstrMessageEvent, name: str, img_path: str):
        if not event.is_admin():
            yield event.plain_result("权限不足，仅管理员可用！")
            return

        name = str(name or "").strip()
        img_path = str(img_path or "").strip()
        if not name or not img_path:
            yield event.plain_result("❌ 用法：/添加老婆 [角色名] [图片文件名或URL]")
            return

        new_id = max((int(wife["id"]) for wife in self.wives_pool), default=0) + 1
        new_item = {"id": new_id, "name": name, "img": img_path}
        self.wives_pool.append(new_item)
        save_wives_config(self.wives_pool)

        extra = ""
        if not img_path.startswith(("http://", "https://")):
            if not _resolve_local_image(self.assets_path(), img_path):
                extra = f"\n⚠️ 未在图片目录中找到 {img_path}，请确认文件已放入。"
        yield event.plain_result(
            f"✅ 添加成功！ID:{new_id}\n名称：{name}\n图片：{img_path}{extra}"
        )

    @filter.command("删除老婆", priority=100)
    async def del_wife(self, event: AstrMessageEvent, wife_id: int):
        if not event.is_admin():
            yield event.plain_result("权限不足，仅管理员可用！")
            return
        for idx, wife in enumerate(self.wives_pool):
            if int(wife["id"]) == int(wife_id):
                self.wives_pool.pop(idx)
                save_wives_config(self.wives_pool)
                yield event.plain_result(
                    f"✅ 已从卡池删除 ID={wife_id} 的角色。\n"
                    "用户存档中的历史记录会保留，但不再计入收集进度。"
                )
                return
        yield event.plain_result(f"❌ 找不到 ID={wife_id} 的角色")

    @filter.command("重置全部老婆记录", priority=100)
    async def reset_all_user_data(self, event: AstrMessageEvent):
        if not event.is_admin():
            yield event.plain_result("❌ 权限不足，仅管理员可用！")
            return
        self.user_data = {}
        save_user_data(self.user_data)
        yield event.plain_result(
            "✅ 已清空全部用户的老婆收集记录、每日抽卡与抢夺记录！卡池角色保留。"
        )

    # ------------------------------------------------------------------
    # 抽卡
    # ------------------------------------------------------------------
    # 使用 RegexFilter 而不是 CommandFilter，这样「抽老婆」不带 / 也能触发。
    @filter.regex(DRAW_PATTERN, priority=100)
    async def draw_wife(self, event: AstrMessageEvent):
        today = str(date.today())
        user_id = str(event.get_sender_id())
        if not self.wives_pool:
            yield event.plain_result("卡池为空，请先在 wives.json 添加角色！")
            return

        limit = self.cfg_int("basic", "daily_draw_limit", 3, 1, 1000)
        ud = self.reset_user_daily(user_id, today)
        if int(ud.get("daily_draw", 0)) >= limit:
            yield event.plain_result(
                f"❌ 今日抽卡次数已用完（{limit}/{limit}），明天再来！"
            )
            return

        picked = random.choice(self.wives_pool)
        wear = random.choice(self.wear_list())
        wid = str(picked["id"])
        before = int(ud["wives"].get(wid, 0))
        is_new = before == 0

        # 重复卡也累计数量（这是之前容易误以为“没记录”的地方）
        ud["wives"][wid] = before + 1
        ud["daily_draw"] = int(ud.get("daily_draw", 0)) + 1
        ud["total_draws"] = int(ud.get("total_draws", 0)) + 1
        ud["last_draw_date"] = today
        self.user_data[user_id] = ud
        self.save_all_userdata()

        stats = self._stats(ud)
        if is_new:
            tip = f"🎉 出新了！第 {stats['unique']}/{stats['total']} 位"
        else:
            tip = f"🔁 重复了！该角色持有×{before + 1}"

        body = (
            f"{tip}\n{picked['name']}【{wear}】\n"
            f"今日 {ud['daily_draw']}/{limit} 抽 · {self._progress_text(stats)}"
        )
        chain: list = []
        if self.cfg_bool("basic", "draw_at_sender", True):
            chain.append(Comp.At(qq=event.get_sender_id()))
            chain.append(Comp.Plain("\n" + body))
        else:
            chain.append(Comp.Plain(body))
        await self._append_image(chain, picked.get("img"))
        yield event.chain_result(chain)

    # ------------------------------------------------------------------
    # 查看角色
    # ------------------------------------------------------------------
    @filter.command("查看老婆", priority=100)
    async def view_wife(self, event: AstrMessageEvent, keyword: str):
        user_id = str(event.get_sender_id())
        ud = self.user_data.get(user_id, {"wives": {}})
        target_wife = self._find_wife(keyword)
        if target_wife is None:
            yield event.plain_result(f"❌ 没有找到关键词【{keyword}】对应的角色！")
            return

        wid = str(target_wife["id"])
        my_count = int(ud.get("wives", {}).get(wid, 0))
        owner_count = 0
        for user_data in self.user_data.values():
            try:
                if int(user_data.get("wives", {}).get(wid, 0)) > 0:
                    owner_count += 1
            except (TypeError, ValueError):
                continue

        stats = self._stats(ud)
        text_msg = (
            "🔍 角色详情\n"
            f"ID：{target_wife['id']}\n"
            f"名称：{target_wife['name']}\n"
            f"你的持有数量：{my_count}\n"
            f"全服拥有人数：{owner_count}\n"
            f"收集进度：{stats['unique']}/{stats['total']}"
            f"（{stats['percent']:.1f}%）"
        )
        chain: list = [Comp.Plain(text_msg)]
        await self._append_image(chain, target_wife.get("img"))
        yield event.chain_result(chain)

    # ------------------------------------------------------------------
    # 我的老婆
    # ------------------------------------------------------------------
    @filter.command("我的老婆", priority=100)
    async def my_wives(self, event: AstrMessageEvent, page: int = 1):
        user_id = str(event.get_sender_id())
        ud = self.user_data.get(user_id, {"wives": {}})
        owned = ud.get("wives", {})
        if not owned:
            yield event.plain_result(
                "你还没有抽到任何老婆，发送「抽老婆」或「/抽老婆」开始抽卡！"
            )
            return

        wife_list = []
        for wid, cnt in owned.items():
            wife = next(
                (w for w in self.wives_pool if str(w["id"]) == str(wid)), None
            )
            if wife:
                wife_list.append({"wife": wife, "count": int(cnt)})
        wife_list.sort(key=lambda item: int(item["wife"]["id"]))
        if not wife_list:
            yield event.plain_result(
                "你收藏中的角色已不在当前卡池中，请等待管理员补充卡池。"
            )
            return

        page_size = self.cfg_int("collection", "my_wife_page_size", 16, 1, 200)
        total = len(wife_list)
        total_page = (total + page_size - 1) // page_size

        if page < 1 or page > total_page:
            yield event.plain_result(f"❌ 页码超出范围！总页数 {total_page}")
            return

        start = (page - 1) * page_size
        page_data = wife_list[start : start + page_size]
        stats = self._stats(ud)
        title = f"📦 我的收藏 | 第{page}/{total_page}页"
        image_title = f"我的收藏 | 第{page}/{total_page}页"
        subtitle = self._progress_text(stats)

        render = self.render_options()
        grid_img_path = await asyncio.to_thread(
            build_grid_image,
            page_data,
            image_title,
            cols=self.cfg_int("collection", "my_wife_grid_cols", 4, 1, 10),
            thumb_size=self.cfg_int(
                "collection", "my_wife_thumb_size", 500, 100, 1000
            ),
            text_height=50,
            subtitle=subtitle,
            **render,
        )

        if grid_img_path and os.path.exists(grid_img_path):
            chain = [
                Comp.Plain(f"{title}\n{subtitle}"),
                Comp.Image.fromFileSystem(grid_img_path),
            ]
            yield event.chain_result(chain)
        else:
            yield event.plain_result("生成图片失败！")

    # ------------------------------------------------------------------
    # 老婆图鉴（内置收集进度）
    # ------------------------------------------------------------------
    @filter.command("老婆图鉴", priority=100)
    async def wife_list(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        ud = self.user_data.get(user_id, {"wives": {}})
        if not self.wives_pool:
            yield event.plain_result("卡池为空")
            return

        stats = self._stats(ud)
        show_progress = self.cfg_bool("progress", "show_in_gallery", True)

        text_msg = f"📜 老婆图鉴 | 共 {stats['total']} 位角色"
        if show_progress:
            text_msg += f"\n📊 {self._progress_text(stats)}"

        render = self.render_options()
        grid_img_path = await asyncio.to_thread(
            build_pool_image,
            self.wives_pool,
            stats["owned_counts"],
            cols=self.cfg_int("collection", "pool_grid_cols", 10, 1, 30),
            thumb_size=self.cfg_int(
                "collection", "pool_thumb_size", 100, 40, 300
            ),
            text_height=self.cfg_int(
                "collection", "pool_text_height", 38, 20, 120
            ),
            show_count=self.cfg_bool("collection", "show_owned_count", True),
            gray_unowned=self.cfg_bool("collection", "gray_unowned", True),
            show_progress=show_progress,
            show_bar=self.cfg_bool("progress", "show_progress_bar", True),
            **render,
        )

        chain: list = [Comp.Plain(text_msg)]
        if grid_img_path and os.path.exists(grid_img_path):
            chain.append(Comp.Image.fromFileSystem(grid_img_path))
        yield event.chain_result(chain)

    # ------------------------------------------------------------------
    # 收集进度
    # ------------------------------------------------------------------
    @filter.command("收集进度", priority=100)
    async def wife_progress(self, event: AstrMessageEvent):
        user_id = str(event.get_sender_id())
        ud = self.user_data.get(user_id, {"wives": {}})
        stats = self._stats(ud)

        text_msg = (
            "📊 老婆收集进度\n"
            f"已收集：{stats['unique']} / {stats['total']}"
            f"（{stats['percent']:.1f}%）\n"
            f"累计获得：{stats['acquired']} 张\n"
            f"重复获得：{stats['duplicates']} 张\n"
            f"累计抽卡：{stats['draws']} 次\n"
            f"未收集：{max(0, stats['total'] - stats['unique'])} 位"
        )

        chain: list = [Comp.Plain(text_msg)]
        if self.cfg_bool("progress", "send_progress_image", True):
            render = self.render_options()
            img_path = await asyncio.to_thread(
                draw_progress_image,
                stats["unique"],
                stats["total"],
                stats["acquired"],
                stats["duplicates"],
                stats["draws"],
                font_path=render["font_path"],
                quality=render["quality"],
                show_bar=self.cfg_bool("progress", "show_progress_bar", True),
            )
            if img_path and os.path.exists(img_path):
                chain.append(Comp.Image.fromFileSystem(img_path))
        yield event.chain_result(chain)

    # ------------------------------------------------------------------
    # 兑换
    # ------------------------------------------------------------------
    @filter.command("兑换老婆", priority=100)
    async def exchange_wife(self, event: AstrMessageEvent, wife_id: int):
        today = str(date.today())
        user_id = str(event.get_sender_id())
        ud = self.reset_user_daily(user_id, today)
        wid = str(wife_id)
        cost = self.cfg_int("exchange", "exchange_cost", 3, 1, 100)
        cnt = int(ud["wives"].get(wid, 0))
        if cnt < cost:
            yield event.plain_result(
                f"❌ 数量不足！你只有 {cnt} 张，兑换需要 {cost} 张相同角色。"
            )
            return

        pool_ids = {str(wife["id"]) for wife in self.wives_pool}
        owned_ids = set(ud["wives"].keys())
        not_own = sorted(pool_ids - owned_ids)
        if not not_own:
            yield event.plain_result("✅ 你已经集齐全部角色，无需兑换！")
            return

        ud["wives"][wid] = cnt - cost
        if ud["wives"][wid] <= 0:
            del ud["wives"][wid]

        new_wid = random.choice(not_own)
        new_wife = next(
            wife for wife in self.wives_pool if str(wife["id"]) == new_wid
        )
        ud["wives"][new_wid] = int(ud["wives"].get(new_wid, 0)) + 1
        self.user_data[user_id] = ud
        self.save_all_userdata()

        wear = random.choice(self.wear_list())
        stats = self._stats(ud)
        chain: list = [
            Comp.Plain(
                f"🎉 兑换成功！消耗 {cost} 张【{wid}】获得"
                f"【{new_wife['name']}】【{wear}】\n"
                f"📊 {self._progress_text(stats)}"
            )
        ]
        await self._append_image(chain, new_wife.get("img"))
        yield event.chain_result(chain)

    @filter.command(
        "兑换溢出",
        alias={"一键兑换", "兑换全部溢出"},
        priority=100,
    )
    async def exchange_overflow(self, event: AstrMessageEvent, times: int = 0):
        """把每个角色超过 1 张的部分按 cost:1 兑换成未拥有角色；times<=0 表示全部。"""
        today = str(date.today())
        user_id = str(event.get_sender_id())
        ud = self.reset_user_daily(user_id, today)
        cost = self.cfg_int("exchange", "exchange_cost", 3, 1, 100)
        wives = ud.get("wives") if isinstance(ud.get("wives"), dict) else {}
        pool_ids = {str(wife["id"]) for wife in self.wives_pool}
        pool_names = {str(wife["id"]): wife["name"] for wife in self.wives_pool}

        overflow: dict[str, int] = {}
        for wid, cnt in wives.items():
            if str(wid) not in pool_ids:
                continue
            try:
                count = int(cnt)
            except (TypeError, ValueError):
                continue
            extra = max(0, count - 1)
            if extra > 0:
                overflow[str(wid)] = extra

        total_overflow = sum(overflow.values())
        if total_overflow < cost:
            yield event.plain_result(
                f"❌ 当前溢出 {total_overflow} 张，不足 {cost} 张，无法兑换。\n"
                "（每个角色保留 1 张，超出的部分才算溢出）"
            )
            return

        owned_ids = set(wives.keys())
        not_own = sorted(pool_ids - owned_ids)
        if not not_own:
            yield event.plain_result(
                f"✅ 你已经集齐全部角色，当前溢出 {total_overflow} 张保留。"
            )
            return

        max_by_overflow = total_overflow // cost
        requested = max(0, int(times))
        max_exchanges = (
            max_by_overflow
            if requested <= 0
            else min(requested, max_by_overflow)
        )
        exchanges = min(max_exchanges, len(not_own))
        if exchanges <= 0:
            yield event.plain_result(
                f"❌ 当前可兑换次数为 0（溢出 {total_overflow} 张，"
                f"未拥有 {len(not_own)} 位）。"
            )
            return

        amount = exchanges * cost
        consumed: list[tuple[str, int]] = []
        remaining = amount
        sorted_overflow = sorted(
            overflow.items(),
            key=lambda item: (
                -item[1],
                int(item[0]) if item[0].isdigit() else 0,
            ),
        )
        for wid, extra in sorted_overflow:
            if remaining <= 0:
                break
            take = min(extra, remaining)
            if take <= 0:
                continue
            wives[wid] = int(wives[wid]) - take
            consumed.append((wid, take))
            remaining -= take

        new_ids = random.sample(not_own, exchanges)
        new_items: list[dict[str, Any]] = []
        for new_wid in new_ids:
            wives[new_wid] = int(wives.get(new_wid, 0)) + 1
            wife = next(
                (
                    item
                    for item in self.wives_pool
                    if str(item["id"]) == new_wid
                ),
                None,
            )
            if wife:
                new_items.append(wife)

        ud["wives"] = wives
        self.user_data[user_id] = ud
        self.save_all_userdata()

        consumed_parts = [
            f"{pool_names.get(wid, wid)}({wid})×{count}"
            for wid, count in consumed
        ]
        if len(consumed_parts) > 20:
            consumed_text = "、".join(consumed_parts[:20]) + f" 等 {len(consumed_parts)} 种"
        else:
            consumed_text = "、".join(consumed_parts)

        new_parts = [f"{wife['name']}({wife['id']})" for wife in new_items]
        if len(new_parts) > 20:
            new_text = "、".join(new_parts[:20]) + f" 等 {len(new_parts)} 张"
        else:
            new_text = "、".join(new_parts)

        remaining_overflow = sum(
            max(0, int(wives.get(wid, 0)) - 1)
            for wid in pool_ids
            if wid in wives
        )
        stats = self._stats(ud)
        note = ""
        if requested > 0:
            if exchanges < requested:
                note = (
                    f"\n⚠️ 你要求兑换 {requested} 次，实际只完成 {exchanges} 次"
                    "（溢出卡或未拥有角色不足）。"
                )
        elif exchanges < max_by_overflow:
            note = (
                f"\n⚠️ 未拥有角色只剩 {len(not_own)} 位，"
                f"本次只兑换了 {exchanges} 次。"
            )

        text = (
            "🎉 一键兑换完成！\n"
            f"消耗溢出卡 {amount} 张：{consumed_text}\n"
            f"兑换 {exchanges} 次，获得 {len(new_items)} 张新角色：\n{new_text}\n"
            f"剩余溢出 {remaining_overflow} 张（不足 {cost} 张的部分保留）\n"
            f"📊 {self._progress_text(stats)}"
            f"{note}"
        )
        yield event.plain_result(text)

    # ------------------------------------------------------------------
    # NTR
    # ------------------------------------------------------------------
    @filter.command("ntr", priority=100)
    async def ntr_wife(self, event: AstrMessageEvent):
        if not self.cfg_bool("ntr", "ntr_enabled", True):
            yield event.plain_result("❌ NTR 功能已关闭，请联系管理员。")
            return

        attacker_id = str(event.get_sender_id())
        target_id = self._extract_target_id(event)
        ntr_rate = self.cfg_rate("ntr", "ntr_success_rate", 0.6) * 100
        ntr_target_rate = self.cfg_rate(
            "ntr", "ntr_target_success_rate", 0.3
        ) * 100
        if target_id is None:
            yield event.plain_result(
                "❌ 用法错误！\n"
                f"示例：/ntr @某人            （随机牛一个，成功率 {ntr_rate:.0f}%）\n"
                f"示例：/ntr @某人 角色名     （指定角色，成功率 {ntr_target_rate:.0f}%）"
            )
            return

        if attacker_id == target_id:
            yield event.chain_result(
                [Comp.At(qq=attacker_id), Comp.Plain("\n❌ 为什么要 NTR 自己！")]
            )
            return

        today = str(date.today())
        ud_attacker = self.user_data.get(attacker_id)
        if not isinstance(ud_attacker, dict):
            ud_attacker = _default_user()
        ud_attacker = _normalize_user_data(ud_attacker)
        self.user_data[attacker_id] = ud_attacker

        if (
            self.cfg_bool("ntr", "ntr_fail_lock", True)
            and ud_attacker.get("ntr_fail_date") == today
        ):
            yield event.chain_result(
                [
                    Comp.At(qq=attacker_id),
                    Comp.Plain("\n❌ 黄毛被干掉了，今天不能再 NTR，等待复活。"),
                ]
            )
            return

        ud_target = self.user_data.get(target_id)
        if not isinstance(ud_target, dict):
            ud_target = _default_user()
        ud_target = _normalize_user_data(ud_target)
        target_wives = ud_target.get("wives", {})

        pool_ids = {str(wife["id"]) for wife in self.wives_pool}
        available = [
            (wid, int(cnt))
            for wid, cnt in target_wives.items()
            if str(wid) in pool_ids and int(cnt) > 0
        ]
        if not available:
            yield event.plain_result(
                f"❌ @{target_id} 还没有任何老婆，无法 NTR！"
            )
            return

        # 解析指令后面的角色名（忽略 @ 组件）。
        # 注意：QQ 的 Plain 片段可能是 "/ntr" + "藿藿"（@ 前后的空格不在 Plain 中），
        # 因此不能用 \b 判断 "ntr" 边界——中文字符在 Unicode 正则里也是 \w。
        plain_text = self._extract_plain_text(event)
        keyword = re.sub(
            r"^[\W_]*\s*ntr\s*", "", plain_text, count=1, flags=re.IGNORECASE
        ).strip()
        keyword = re.sub(r"\s+", " ", keyword).strip()

        specified = bool(keyword)
        if specified:
            wife_info, candidates = self._match_owned_wife(target_wives, keyword)
            if wife_info is None:
                if candidates:
                    names = "、".join(
                        f"{wife['name']}(ID:{wife['id']})" for wife in candidates
                    )
                    yield event.plain_result(
                        f"❌ 匹配到多个角色：{names}\n"
                        "请使用更精确的角色名或 ID 重新指定。"
                    )
                else:
                    yield event.plain_result(
                        f"❌ @{target_id} 没有持有【{keyword}】，无法指定 NTR！"
                    )
                return
            stolen_wid = str(wife_info["id"])
            success_rate = self.cfg_rate("ntr", "ntr_target_success_rate", 0.3)
        else:
            stolen_wid = random.choice(available)[0]
            wife_info = next(
                (w for w in self.wives_pool if str(w["id"]) == stolen_wid), None
            )
            if wife_info is None:
                yield event.plain_result("❌ 找不到该角色数据！")
                return
            success_rate = self.cfg_rate("ntr", "ntr_success_rate", 0.6)

        if random.random() < success_rate:
            # 成功：复制角色；如果攻击者已拥有，则累加数量而不是覆盖
            before = int(ud_attacker["wives"].get(stolen_wid, 0))
            ud_attacker["wives"][stolen_wid] = before + 1
            ud_attacker["ntr_fail_date"] = ""
            self.user_data[attacker_id] = ud_attacker
            self.save_all_userdata()

            mode = "指定角色 " if specified else ""
            stats = self._stats(ud_attacker)
            text = (
                f"\n✅ NTR 成功！{mode}你牛到了【{wife_info['name']}】！\n"
                f"该角色持有×{before + 1} · {self._progress_text(stats)}"
            )
            chain: list = [
                Comp.At(qq=attacker_id),
                Comp.Plain(" "),
                Comp.At(qq=target_id),
                Comp.Plain(text),
            ]
            if self.cfg_bool("ntr", "ntr_send_image", True):
                await self._append_image(chain, wife_info.get("img"))
            yield event.chain_result(chain)
        else:
            if self.cfg_bool("ntr", "ntr_fail_lock", True):
                ud_attacker["ntr_fail_date"] = today
            self.user_data[attacker_id] = ud_attacker
            self.save_all_userdata()
            extra = "，今天不能再 NTR" if self.cfg_bool(
                "ntr", "ntr_fail_lock", True
            ) else ""
            yield event.chain_result(
                [
                    Comp.At(qq=attacker_id),
                    Comp.Plain(f"\n💔 NTR 失败！黄毛被干掉了{extra}。"),
                ]
            )

    # ------------------------------------------------------------------
    # 帮助
    # ------------------------------------------------------------------
    @filter.command("老婆帮助", priority=100)
    async def help(self, event: AstrMessageEvent):
        limit = self.cfg_int("basic", "daily_draw_limit", 3, 1, 1000)
        cost = self.cfg_int("exchange", "exchange_cost", 3, 1, 100)
        ntr_rate = self.cfg_rate("ntr", "ntr_success_rate", 0.6) * 100
        ntr_target_rate = self.cfg_rate(
            "ntr", "ntr_target_success_rate", 0.3
        ) * 100
        help_text = f"""📖 抽老婆插件指令：
抽老婆 或 /抽老婆 - 每日最多 {limit} 次抽卡（不带 / 也能触发）
/查看老婆 [ID/角色名] - 查看角色详情、持有数量与图片
/我的老婆 [页码] - 生成网格大图查看收藏，支持分页
/老婆图鉴 - 生成全部角色总览大图，并显示收集进度
/收集进度 - 查看已收集、收集率、累计获得与重复数量
/兑换老婆 [ID] - 消耗 {cost} 张相同老婆兑换未拥有角色
/兑换溢出 [次数] - 一键把每个角色超过 1 张的溢出卡按 {cost}:1 兑换（省略次数=全部）
/ntr @某人 - 随机牛对方一个老婆（成功率 {ntr_rate:.0f}%）
/ntr @某人 角色名 - 指定角色 NTR（成功率 {ntr_target_rate:.0f}%）

管理员：
/添加老婆 [名字] [图片文件名]
/删除老婆 [ID]
/重置全部老婆记录 - 清空所有人收集记录

⚙️ 以上次数、成功率和排版均可在 AstrBot WebUI 的插件配置中修改。
        """
        yield event.plain_result(help_text)
