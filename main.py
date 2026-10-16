import json
import os
import random
from datetime import date
from PIL import Image, ImageDraw, ImageFont
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

# 插件根目录
PLUGIN_ROOT = os.path.dirname(__file__)
ASSETS_DIR = os.path.join(PLUGIN_ROOT, "assets")

# 用户存档目录
DATA_DIR = os.path.join(PLUGIN_ROOT, "data")
USER_DATA_PATH = os.path.join(DATA_DIR, "user_data.json")
PROGRESS_IMG_PATH = os.path.join(DATA_DIR, "progress_temp.png")
GRID_TMP_PATH = os.path.join(DATA_DIR, "grid_temp.png")
POOL_TMP_PATH = os.path.join(DATA_DIR, "pool_full_grid.png")

# === 配置项 ===
MY_WIFE_PAGE_SIZE = 16        # 我的老婆每页16个角色
MY_WIFE_GRID_COLS = 4         # 我的老婆网格列数：4列
POOL_GRID_COLS = 4            # 老婆池总览网格列数：4列
THUMB_W = 200                 # 缩略图宽度，压缩体积
THUMB_H = 260                 # 缩略图高度
TEXT_HEIGHT = 50              # 底部文字区高度
GOLD_COLOR = "#ffd700"        # 金色边框
WEAR_LIST = ["崭新出厂", "略有磨损", "久经沙场"]

# 初始化文件夹
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)


def load_wives_config():
    wives_config_path = os.path.join(PLUGIN_ROOT, "wives.json")
    if not os.path.exists(wives_config_path):
        with open(wives_config_path, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
    with open(wives_config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_wives_config(wives_list):
    wives_config_path = os.path.join(PLUGIN_ROOT, "wives.json")
    with open(wives_config_path, "w", encoding="utf-8") as f:
        json.dump(wives_list, f, ensure_ascii=False, indent=2)


def load_user_data():
    if not os.path.exists(USER_DATA_PATH):
        return {}
    with open(USER_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_data(data):
    with open(USER_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def draw_progress_image(owned_count: int, total_count: int):
    width = 520
    height = 220
    img = Image.new("RGB", (width, height), color="#222222")
    draw = ImageDraw.Draw(img)
    try:
        font_title = ImageFont.truetype("simhei.ttf", 36)
        font_text = ImageFont.truetype("simhei.ttf", 28)
    except Exception:
        font_title = ImageFont.load_default(size=36)
        font_text = ImageFont.load_default(size=28)

    draw.text((30, 25), "📊 老婆收集进度", font=font_title, fill="#ffffff")
    table_top = 90
    cell_w = 230
    cell_h = 70
    draw.rectangle([30, table_top, 30 + cell_w, table_top + cell_h], outline="#dddddd", width=2)
    draw.rectangle([30 + cell_w, table_top, 30 + cell_w * 2, table_top + cell_h], outline="#dddddd", width=2)
    draw.text((50, table_top + 15), "已收集", font=font_text, fill="#87CEFA")
    draw.text((30 + cell_w + 20, table_top + 15), "总数量", font=font_text, fill="#FFC0CB")

    row2_y = table_top + cell_h
    draw.rectangle([30, row2_y, 30 + cell_w, row2_y + cell_h], outline="#dddddd", width=2)
    draw.rectangle([30 + cell_w, row2_y, 30 + cell_w * 2, row2_y + cell_h], outline="#dddddd", width=2)
    draw.text((50, row2_y + 15), f"{owned_count}", font=font_text, fill="#87CEFA")
    draw.text((30 + cell_w + 20, row2_y + 15), f"{total_count}", font=font_text, fill="#FFC0CB")

    if total_count > 0:
        percent = (owned_count / total_count * 100)
    else:
        percent = 0
    draw.text((30, row2_y + 85), f"收集率：{percent:.1f}%", font=font_text, fill="#90EE90")
    img.save(PROGRESS_IMG_PATH, "JPEG", quality=85)
    return PROGRESS_IMG_PATH


def build_grid_image(item_list, title_text: str, cols: int, owned_ids: set = None):
    """
    通用网格生成函数
    item_list: [{"wife": dict, "count": int}, ...]
    cols: 每行多少个
    owned_ids: 已拥有角色ID集合，用于卡池列表灰化
    """
    item_num = len(item_list)
    if item_num == 0:
        return None

    rows = (item_num + cols - 1) // cols
    full_w = cols * THUMB_W
    full_h = rows * (THUMB_H + TEXT_HEIGHT) + 80

    canvas = Image.new("RGB", (full_w, full_h), color="#fffbe8")
    draw = ImageDraw.Draw(canvas)

    try:
        font_title = ImageFont.truetype("simhei.ttf", 32)
        font_info = ImageFont.truetype("simhei.ttf", 20)
    except Exception:
        font_title = ImageFont.load_default(size=32)
        font_info = ImageFont.load_default(size=20)

    draw.text((20, 20), title_text, font=font_title, fill="#222222")

    for idx, item in enumerate(item_list):
        wife = item["wife"]
        cnt = item.get("count", 0)
        col = idx % cols
        row = idx // cols

        x0 = col * THUMB_W
        y0 = 80 + row * (THUMB_H + TEXT_HEIGHT)

        img_val = wife["img"]
        thumb_img = None

        if img_val:
            if not img_val.startswith(("http://", "https://")):
                img_path = os.path.join(ASSETS_DIR, img_val)
                if os.path.exists(img_path):
                    try:
                        thumb_img = Image.open(img_path).convert("RGBA").resize(
                            (THUMB_W, THUMB_H), Image.Resampling.LANCZOS
                        )

                        # 卡池列表未收集角色灰化
                        if owned_ids is not None and str(wife["id"]) not in owned_ids:
                            gray_mask = Image.new("RGBA", thumb_img.size, (80, 80, 80, 160))
                            thumb_img = Image.alpha_composite(thumb_img, gray_mask)

                        canvas.paste(thumb_img, (x0, y0), mask=thumb_img if thumb_img.mode == "RGBA" else None)
                    except Exception as e:
                        print(f"图片读取失败 {img_path}: {e}")
                        thumb_img = None

        if thumb_img is None:
            draw.rectangle([x0, y0, x0 + THUMB_W, y0 + THUMB_H], fill="#cccccc")
            draw.text((x0 + 20, y0 + THUMB_H // 2), "图片缺失", font=font_info, fill="#333333")

        # 金色边框
        draw.rectangle([x0, y0, x0 + THUMB_W, y0 + THUMB_H], outline=GOLD_COLOR, width=3)

        # 文字信息
        text_y = y0 + THUMB_H + 5
        text_line1 = f"ID:{wife['id']} {wife['name']}"
        draw.text((x0 + 10, text_y), text_line1, font=font_info, fill="#111111")

        if cnt > 0:
            text_line2 = f"持有×{cnt}"
            draw.text((x0 + 10, text_y + 24), text_line2, font=font_info, fill="#555555")

    # 压缩保存，降低体积
    canvas.save(GRID_TMP_PATH, "JPEG", quality=85, optimize=True)
    return GRID_TMP_PATH


def build_full_pool_image(pool, owned_ids: set = None):
    """
    老婆池列表：一次性生成全部角色超长总览大图
    """
    item_list = [{"wife": w, "count": 0} for w in pool]
    item_num = len(item_list)
    if item_num == 0:
        return None

    cols = POOL_GRID_COLS
    rows = (item_num + cols - 1) // cols
    full_w = cols * THUMB_W
    full_h = rows * (THUMB_H + TEXT_HEIGHT) + 80

    canvas = Image.new("RGB", (full_w, full_h), color="#fffbe8")
    draw = ImageDraw.Draw(canvas)

    try:
        font_title = ImageFont.truetype("simhei.ttf", 32)
        font_info = ImageFont.truetype("simhei.ttf", 20)
    except Exception:
        font_title = ImageFont.load_default(size=32)
        font_info = ImageFont.load_default(size=20)

    draw.text((20, 20), "📜 老婆池全部角色总览", font=font_title, fill="#222222")

    for idx, item in enumerate(item_list):
        wife = item["wife"]
        col = idx % cols
        row = idx // cols

        x0 = col * THUMB_W
        y0 = 80 + row * (THUMB_H + TEXT_HEIGHT)

        img_val = wife["img"]
        thumb_img = None

        if img_val:
            if not img_val.startswith(("http://", "https://")):
                img_path = os.path.join(ASSETS_DIR, img_val)
                if os.path.exists(img_path):
                    try:
                        thumb_img = Image.open(img_path).convert("RGBA").resize(
                            (THUMB_W, THUMB_H), Image.Resampling.LANCZOS
                        )

                        if owned_ids is not None and str(wife["id"]) not in owned_ids:
                            gray_mask = Image.new("RGBA", thumb_img.size, (80, 80, 80, 160))
                            thumb_img = Image.alpha_composite(thumb_img, gray_mask)

                        canvas.paste(thumb_img, (x0, y0), mask=thumb_img if thumb_img.mode == "RGBA" else None)
                    except Exception as e:
                        print(f"图片读取失败 {img_path}: {e}")
                        thumb_img = None

        if thumb_img is None:
            draw.rectangle([x0, y0, x0 + THUMB_W, y0 + THUMB_H], fill="#cccccc")
            draw.text((x0 + 20, y0 + THUMB_H // 2), "图片缺失", font=font_info, fill="#333333")

        draw.rectangle([x0, y0, x0 + THUMB_W, y0 + THUMB_H], outline=GOLD_COLOR, width=3)

        text_y = y0 + THUMB_H + 5
        text_line1 = f"ID:{wife['id']} {wife['name']}"
        draw.text((x0 + 10, text_y), text_line1, font=font_info, fill="#111111")

    # 压缩保存
    canvas.save(POOL_TMP_PATH, "JPEG", quality=85, optimize=True)
    return POOL_TMP_PATH


class WifeGachaPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.wives_pool = load_wives_config()
        self.user_data = load_user_data()
        self.logger.info(f"[Gacha] 插件assets目录: {ASSETS_DIR}")

    def save_all_userdata(self):
        save_user_data(self.user_data)

    def reset_user_daily(self, user_id: str, today: str):
        ud = self.user_data.get(user_id, {
            "wives": {},
            "daily_draw": 0,
            "last_draw_date": "",
            "rob_used": False,
            "last_rob_date": ""
        })
        if ud["last_draw_date"] != today:
            ud["daily_draw"] = 0
            ud["last_draw_date"] = today
        if ud["last_rob_date"] != today:
            ud["rob_used"] = False
            ud["last_rob_date"] = today
        return ud

    # 管理员指令：添加老婆
    @filter.command("添加老婆")
    async def add_wife(self, event: AstrMessageEvent, name: str, img_path: str):
        if not event.is_admin():
            yield event.plain_result("权限不足，仅管理员可用！")
            return
        wives = self.wives_pool
        new_id = max([w["id"] for w in wives], default=0) + 1
        new_item = {"id": new_id, "name": name, "img": img_path}
        wives.append(new_item)
        save_wives_config(wives)
        self.wives_pool = wives
        yield event.plain_result(f"✅ 添加成功！ID:{new_id}\n名称：{name}\n图片名：{img_path}")

    # 管理员指令：删除老婆
    @filter.command("删除老婆")
    async def del_wife(self, event: AstrMessageEvent, wife_id: int):
        if not event.is_admin():
            yield event.plain_result("权限不足，仅管理员可用！")
            return
        wives = self.wives_pool
        for idx, w in enumerate(wives):
            if w["id"] == wife_id:
                wives.pop(idx)
                save_wives_config(wives)
                self.wives_pool = wives
                yield event.plain_result(f"✅ 已删除ID={wife_id}的角色")
                return
        yield event.plain_result("❌ 找不到该ID角色")

    # 管理员指令：清空全部用户记录
    @filter.command("重置全部老婆记录")
    async def reset_all_user_data(self, event: AstrMessageEvent):
        if not event.is_admin():
            yield event.plain_result("❌权限不足，仅管理员可用！")
            return
        self.user_data = {}
        save_user_data(self.user_data)
        yield event.plain_result("✅ 已清空全部用户的老婆收集记录、每日抽卡、抢夺记录！卡池角色保留。")

    @filter.command("抽老婆")
    async def draw_wife(self, event: AstrMessageEvent):
        today = str(date.today())
        user_id = event.get_sender_id()
        ud = self.reset_user_daily(user_id, today)
        wives = self.wives_pool
        if len(wives) == 0:
            yield event.plain_result("卡池为空，请先在wives.json添加角色！")
            return
        if ud["daily_draw"] >= 3:
            yield event.plain_result("❌ 今日抽卡次数用完，明天再来！")
            return

        picked = random.choice(wives)
        wear = random.choice(WEAR_LIST)
        wid = str(picked["id"])
        is_new = wid not in ud["wives"]
        tip = "出新了" if is_new else "重复了！"

        ud["wives"][wid] = ud["wives"].get(wid, 0) + 1
        ud["daily_draw"] += 1
        self.user_data[user_id] = ud
        self.save_all_userdata()

        chain = [Comp.Plain(f"{tip}\n{picked['name']}【{wear}】")]
        img_val = picked["img"]
        self.logger.info(f"[Gacha Debug]角色:{picked['name']}, img字段:{img_val}")
        if img_val:
            if img_val.startswith(("http://", "https://")):
                chain.append(Comp.Image.fromURL(img_val))
                self.logger.info(f"[Gacha Debug]添加网络图片: {img_val}")
            else:
                img_full_path = os.path.join(ASSETS_DIR, img_val)
                self.logger.info(f"[Gacha Debug]本地图片完整路径: {img_full_path}, 文件存在? {os.path.exists(img_full_path)}")
                if os.path.exists(img_full_path):
                    chain.append(Comp.Image.fromFileSystem(img_full_path))
                    self.logger.info("[Gacha Debug]✅成功追加本地图片组件到消息链")
                else:
                    self.logger.info("[Gacha Debug]❌图片文件不存在，跳过图片")
        yield event.chain_result(chain)

    # ========== 查看老婆：支持输入ID或者角色名称搜索 ==========
    @filter.command("查看老婆")
    async def view_wife(self, event: AstrMessageEvent, keyword: str):
        user_id = event.get_sender_id()
        ud = self.user_data.get(user_id, {"wives": {}})
        target_wife = None

        # 优先按ID匹配
        if keyword.isdigit():
            target_id = int(keyword)
            target_wife = next((w for w in self.wives_pool if w["id"] == target_id), None)
        # ID找不到，按名称模糊匹配
        if target_wife is None:
            for w in self.wives_pool:
                if keyword in w["name"]:
                    target_wife = w
                    break

        if target_wife is None:
            yield event.plain_result(f"❌ 没有找到关键词【{keyword}】对应的角色！")
            return

        wid = str(target_wife["id"])
        my_count = ud["wives"].get(wid, 0)

        # 统计全服拥有该角色的人数
        owner_count = 0
        for u_data in self.user_data.values():
            if wid in u_data.get("wives", {}):
                owner_count += 1

        text_msg = (
            f"🔍角色详情\n"
            f"ID：{target_wife['id']}\n"
            f"名称：{target_wife['name']}\n"
            f"你的持有数量：{my_count}\n"
            f"全服拥有人数：{owner_count}"
        )
        chain = [Comp.Plain(text_msg)]
        img_val = target_wife["img"]
        if img_val:
            if img_val.startswith(("http://", "https://")):
                chain.append(Comp.Image.fromURL(img_val))
            else:
                img_full_path = os.path.join(ASSETS_DIR, img_val)
                if os.path.exists(img_full_path):
                    chain.append(Comp.Image.fromFileSystem(img_full_path))
        yield event.chain_result(chain)

    # ========== 我的老婆：每页16个，4列 ==========
    @filter.command("我的老婆")
    async def my_wives(self, event: AstrMessageEvent, page: int = 1):
        user_id = event.get_sender_id()
        ud = self.user_data.get(user_id, {"wives": {}})
        owned = ud["wives"]
        if not owned:
            yield event.plain_result("你还没有抽到任何老婆，发送 /抽老婆 开始抽卡！")
            return

        wife_list = []
        for wid, cnt in owned.items():
            wife = next((w for w in self.wives_pool if str(w["id"]) == wid), None)
            if wife:
                wife_list.append({"wife": wife, "count": cnt})

        total = len(wife_list)
        total_page = (total + MY_WIFE_PAGE_SIZE - 1) // MY_WIFE_PAGE_SIZE

        if page < 1 or page > total_page:
            yield event.plain_result(f"❌页码超出范围！总页数 {total_page}")
            return

        start = (page - 1) * MY_WIFE_PAGE_SIZE
        end = start + MY_WIFE_PAGE_SIZE
        page_data = wife_list[start:end]

        title = f"📦 我的收藏 | 第{page}/{total_page}页"
        grid_img_path = build_grid_image(
            page_data,
            title_text=title,
            cols=MY_WIFE_GRID_COLS,
            owned_ids=None
        )

        if grid_img_path and os.path.exists(grid_img_path):
            chain = [Comp.Plain(title), Comp.Image.fromFileSystem(grid_img_path)]
            yield event.chain_result(chain)
        else:
            yield event.plain_result("生成图片失败！")

    # ========== 老婆池列表：全部角色超长总览大图 ==========
    @filter.command("老婆池列表")
    async def wife_list(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        ud = self.user_data.get(user_id, {"wives": {}})
        owned_ids = set(ud["wives"].keys())

        pool = self.wives_pool
        if not pool:
            yield event.plain_result("卡池为空")
            return

        grid_img_path = build_full_pool_image(pool, owned_ids=owned_ids)

        if grid_img_path and os.path.exists(grid_img_path):
            chain = [
                Comp.Plain(f"📜 老婆池全部角色总览（共{len(pool)}个）"),
                Comp.Image.fromFileSystem(grid_img_path)
            ]
            yield event.chain_result(chain)
        else:
            yield event.plain_result("生成图片失败！")

    @filter.command("老婆进度")
    async def wife_progress(self, event: AstrMessageEvent):
        user_id = event.get_sender_id()
        ud = self.user_data.get(user_id, {"wives": {}})
        total = len(self.wives_pool)
        owned_unique = len(ud["wives"].keys())
        img_path = draw_progress_image(owned_unique, total)
        self.logger.info(f"[Gacha Debug]进度图路径:{img_path}, exist:{os.path.exists(img_path)}")
        chain = [Comp.Plain(f"📊收集进度：{owned_unique}/{total}")]
        if os.path.exists(img_path):
            chain.append(Comp.Image.fromFileSystem(img_path))
        yield event.chain_result(chain)

    @filter.command("兑换老婆")
    async def exchange_wife(self, event: AstrMessageEvent, wife_id: int):
        today = str(date.today())
        user_id = event.get_sender_id()
        ud = self.reset_user_daily(user_id, today)
        wid = str(wife_id)
        cnt = ud["wives"].get(wid, 0)
        if cnt < 3:
            yield event.plain_result(f"❌ 数量不足！你只有{cnt}张，兑换需要3张相同角色。")
            return
        all_ids = set(str(w["id"]) for w in self.wives_pool)
        owned_ids = set(ud["wives"].keys())
        not_own = list(all_ids - owned_ids)
        if len(not_own) == 0:
            yield event.plain_result("✅你已经集齐全部角色，无需兑换！")
            return
        ud["wives"][wid] -= 3
        if ud["wives"][wid] <= 0:
            del ud["wives"][wid]
        new_wid = random.choice(not_own)
        new_wife = next(w for w in self.wives_pool if str(w["id"]) == new_wid)
        ud["wives"][new_wid] = ud["wives"].get(new_wid, 0) + 1
        self.user_data[user_id] = ud
        self.save_all_userdata()
        wear = random.choice(WEAR_LIST)
        chain = [Comp.Plain(f"🎉兑换成功！获得【{new_wife['name']}】【{wear}】")]
        img_val = new_wife["img"]
        if img_val:
            if img_val.startswith(("http://", "https://")):
                chain.append(Comp.Image.fromURL(img_val))
            else:
                img_full_path = os.path.join(ASSETS_DIR, img_val)
                if os.path.exists(img_full_path):
                    chain.append(Comp.Image.fromFileSystem(img_full_path))
        yield event.chain_result(chain)

    @filter.command("ntr")
    async def rob_wife(self, event: AstrMessageEvent, target_user_id: str):
        today = str(date.today())
        attacker_id = event.get_sender_id()
        ud_attacker = self.reset_user_daily(attacker_id, today)
        if ud_attacker["rob_used"]:
            yield event.plain_result("❌ 今天ntr次数已用完，明天再来！")
            return
        ud_target = self.user_data.get(target_user_id, {"wives": {}})
        target_wives = ud_target["wives"]
        if not target_wives:
            yield event.plain_result("❌对方没有任何老婆可以牛！")
            return
        ud_attacker["rob_used"] = True
        roll = random.random()
        if roll <= 0.3:
            rob_wid = random.choice(list(target_wives.keys()))
            rob_wife = next(w for w in self.wives_pool if str(w["id"]) == rob_wid)
            target_wives[rob_wid] -= 1
            if target_wives[rob_wid] <= 0:
                del target_wives[rob_wid]
            ud_target["wives"] = target_wives
            ud_attacker["wives"][rob_wid] = ud_attacker["wives"].get(rob_wid, 0) + 1
            self.user_data[target_user_id] = ud_target
            self.user_data[attacker_id] = ud_attacker
            self.save_all_userdata()
            tip = "ntr！"
            wear = random.choice(WEAR_LIST)
            chain = [Comp.Plain(f"{tip}\n抢到【{rob_wife['name']}】【{wear}】")]
            img_val = rob_wife["img"]
            if img_val:
                if img_val.startswith(("http://", "https://")):
                    chain.append(Comp.Image.fromURL(img_val))
                else:
                    img_full_path = os.path.join(ASSETS_DIR, img_val)
                    if os.path.exists(img_full_path):
                        chain.append(Comp.Image.fromFileSystem(img_full_path))
            yield event.chain_result(chain)
        else:
            self.user_data[attacker_id] = ud_attacker
            self.save_all_userdata()
            yield event.plain_result("ntr失败，黄毛被干掉了！")

    @filter.command("老婆帮助")
    async def help(self, event: AstrMessageEvent):
        help_text = """📖抽老婆插件指令：
/抽老婆 - 每日最多3次抽卡
/查看老婆 [ID/角色名] - 输入ID或者角色名字，查看角色详情与图片
/我的老婆 [页码] - 生成网格大图查看收藏，每页16个，支持分页
/老婆进度 - 查看收集进度
/老婆池列表 - 一次性生成全部角色超长总览大图
/兑换老婆 [ID] - 消耗3张相同老婆兑换未拥有角色
/ntr [对方ID] - 抢夺对方老婆（每日1次，成功率30%）
管理员：
/添加老婆 [名字] [图片文件名]
/删除老婆 [ID]
/重置全部老婆记录 - 清空所有人收集记录
"""
        yield event.plain_result(help_text)
