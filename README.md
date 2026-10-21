# 抽老婆插件（AstrBot）

一个基于 AstrBot 框架的趣味抽卡收集插件。发送 `抽老婆` 或 `/抽老婆` 随机抽取角色卡，收集你的「老婆」图鉴，支持兑换、NTR 抢夺、收集进度统计，并自动生成网格大图。

## 功能特性

- 🎰 **每日抽卡**：每日次数可在 WebUI 配置，随机抽取卡池角色，包含 3 种磨损品质
- 🌐 **图片 CDN**：卡池图片使用 GitHub + jsDelivr，`wives.json` 直接保存完整 URL，不再依赖本地图片路径；本地 `assets/` 仅作为缩略图加速与兜底
- 🔁 **重复卡累计**：抽到已拥有的角色会累加持有数量，抽卡结果直接显示「持有×N」
- 📜 **老婆图鉴（含收集进度）**：一次性生成全部角色总览大图，未拥有角色自动灰化；消息和图片顶部都会显示已收集、收集率、累计获得、重复数量与进度条
- 📊 **收集进度**：独立进度表格图片，显示已收集 / 总数 / 收集率 / 累计获得 / 重复获得 / 累计抽卡
- 🗂️ **我的老婆**：分页网格查看个人收藏，每页数量、列数、缩略图尺寸均可配置
- 🔍 **查看老婆**：支持按 ID 或角色名称模糊搜索，显示持有数量与全服拥有该角色的人数
- 🔄 **兑换老婆**：消耗若干张相同角色，兑换 1 个未拥有角色（消耗数量可配置）
- 💢 **NTR 抢夺**：随机牛对方一个老婆，或指定角色名 / ID 精准 NTR；成功后附带角色图片
- ⚙️ **WebUI 配置**：通过 `_conf_schema.json` 在 AstrBot 管理面板可视化修改全部主要参数
- 👑 **管理员功能**：添加 / 删除角色、清空全服记录

## 目录结构

```
astrbot_plugin_gacha/
├── main.py                 # 插件主代码
├── _conf_schema.json       # WebUI 可视化配置 schema
├── wives.json              # 卡池角色配置（图片为 jsDelivr URL）
├── assets/                 # 卡池图片（已提交到 GitHub，供 jsDelivr CDN 使用）
└── data/                   # 用户存档目录（自动创建）
    ├── user_data.json      # 用户收集记录
    ├── user_data.json.bak  # 上一次存档的自动备份
    ├── image_cache/        # 远程图片本地缓存（图鉴缩略图使用）
    └── *_*.jpg             # 临时生成的图片文件（插件启动时清理超过 24 小时的旧图）
```

## 安装

1. 将插件文件夹放到 AstrBot 的 `plugins` 目录
2. 首次启动自动创建 `data/` 等目录；`assets/` 中的图片随仓库提供
3. 卡池图片默认使用 jsDelivr CDN URL，不依赖本地图片也能正常发图；本地 `assets/` 用于图鉴缩略图加速与兜底
4. 在 AstrBot 管理界面加载 / 重载插件
5. 打开 AstrBot WebUI → 插件管理 → 抽老婆插件 → 配置，即可看到可视化配置项

## 角色配置（wives.json）

卡池图片已改为 **GitHub + jsDelivr CDN**，`img` 直接保存完整 URL：

```json
[
  {
    "id": 1,
    "name": "角色名称",
    "img": "https://cdn.jsdelivr.net/gh/Wesker-0630/astrbot-plugin-gacha@main/assets/keqing.png"
  },
  {
    "id": 2,
    "name": "角色名称",
    "img": "https://example.com/xxx.jpg"
  }
]
```

- `id`：角色唯一 ID，自动递增
- `name`：角色名称
- `img`：**完整图片 URL**（推荐 jsDelivr CDN）；也兼容本地文件名作为兜底

### 新增角色图片

1. 把图片放到 `assets/`；
2. 提交并推送到 GitHub `main`；
3. 使用 `https://cdn.jsdelivr.net/gh/Wesker-0630/astrbot-plugin-gacha@main/assets/<文件名>`；
4. 文件名含 `[]`、空格等字符时做 URL 编码，例如 `artoria[alter].png` → `artoria%5Balter%5D.png`；
5. 修改 `wives.json` 后在 WebUI 重载插件。

> jsDelivr 会缓存 `@main` 分支。更新图片后若仍显示旧图，可访问
> `https://purge.jsdelivr.net/gh/Wesker-0630/astrbot-plugin-gacha@main/assets/<文件名>`
> 刷新缓存，或改用**完整 commit hash** / tag 的 URL。

## 指令说明

### 玩家指令

| 指令 | 说明 |
| --- | --- |
| `抽老婆` 或 `/抽老婆` | 每日抽卡，回复自动 @本人并附带角色图片；重复角色显示「持有×N」 |
| `/查看老婆 [ID/角色名]` | 查看角色详情（按 ID 精确、按名称模糊匹配） |
| `/我的老婆 [页码]` | 分页查看收藏网格大图 |
| `/老婆图鉴` | 生成全部角色总览大图，**同时显示收集进度** |
| `/收集进度` | 查看收集进度统计图 |
| `/兑换老婆 [ID]` | 消耗相同角色兑换 1 个未拥有角色 |
| `/ntr @某人` | 随机牛对方一个老婆 |
| `/ntr @某人 角色名` | 指定角色 NTR（也可填角色 ID） |
| `/老婆帮助` | 查看全部指令说明 |

> 「抽老婆」使用正则触发器，**不带 `/` 也能触发**；带 `/` 时按正常指令处理。

### 管理员指令

| 指令 | 说明 |
| --- | --- |
| `/添加老婆 [名字] [图片文件名或URL]` | 添加角色（仅管理员） |
| `/删除老婆 [ID]` | 删除角色（仅管理员） |
| `/重置全部老婆记录` | 清空所有用户的收集、每日抽卡与抢夺记录（卡池保留） |

## NTR 规则

1. **发起条件**：`/ntr` 后必须 `@` 一个目标用户，且不能对自己使用
2. **随机 NTR**：`/ntr @某人` 从对方已拥有的角色中随机复制一个，默认成功率 **60%**
3. **指定 NTR**：`/ntr @某人 角色名`（或角色 ID）只对目标已拥有的该角色发起，默认成功率 **30%**
4. **名称匹配**：先精确匹配名称，再模糊匹配；匹配到多个角色时会提示列出候选，不会消耗当天次数
5. **成功结果**：复制角色给自己，**对方不损失**；如果自己已拥有则数量累加，不会覆盖
6. **成功提示**：消息中附带牛到的角色图片（可在配置中关闭）
7. **失败锁定**：默认 NTR 失败后当天不能再发起 NTR，次日自动解除（可在配置中关闭）

## WebUI 配置项

配置保存在 `data/config/astrbot_plugin_gacha_config.json`，修改后保存会自动热重载插件。

| 分组 | 配置项 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 基础 | `daily_draw_limit` | `3` | 每日抽卡次数 |
| 基础 | `draw_at_sender` | `true` | 抽卡结果是否 @ 抽卡人 |
| 基础 | `assets_dir` | `assets` | 本地图片兜底/缩略图加速目录（相对插件目录或绝对路径）；`img` 为 URL 时直接走 CDN |
| 基础 | `wear_list` | `崭新出厂,略有磨损,久经沙场` | 磨损品质列表 |
| 基础 | `gold_color` | `#ffd700` | 卡片金色边框 |
| 收藏 | `my_wife_page_size` | `16` | 「我的老婆」每页数量 |
| 收藏 | `my_wife_grid_cols` | `4` | 「我的老婆」每行列数 |
| 收藏 | `my_wife_thumb_size` | `500` | 「我的老婆」缩略图边长 |
| 收藏 | `pool_grid_cols` | `10` | 「老婆图鉴」每行列数 |
| 收藏 | `pool_thumb_size` | `100` | 「老婆图鉴」缩略图边长 |
| 收藏 | `pool_text_height` | `38` | 「老婆图鉴」文字区高度 |
| 收藏 | `show_owned_count` | `true` | 图鉴显示持有数量 |
| 收藏 | `gray_unowned` | `true` | 未拥有角色灰化 |
| 进度 | `show_in_gallery` | `true` | 在老婆图鉴中显示收集进度 |
| 进度 | `show_progress_bar` | `true` | 显示进度条 |
| 进度 | `send_progress_image` | `true` | 收集进度附带图片 |
| 兑换 | `exchange_cost` | `3` | 兑换消耗数量 |
| NTR | `ntr_enabled` | `true` | 启用 NTR |
| NTR | `ntr_success_rate` | `0.6` | 随机 NTR 成功率（0.6 = 60%） |
| NTR | `ntr_target_success_rate` | `0.3` | 指定角色 NTR 成功率（0.3 = 30%） |
| NTR | `ntr_fail_lock` | `true` | 失败后当天锁定 |
| NTR | `ntr_send_image` | `true` | NTR 成功发送角色图片 |
| 渲染 | `font_path` | `simhei.ttf` | 字体文件或绝对路径 |
| 渲染 | `jpeg_quality` | `85` | JPEG 压缩质量 |

## 数据存储

- 所有数据保存在 `data/user_data.json`，写入采用「临时文件 + 原子替换」，并自动保留 `user_data.json.bak`
- 每个用户记录结构：

```json
{
  "用户ID": {
    "wives": { "角色ID": 持有数量 },
    "daily_draw": 0,
    "last_draw_date": "",
    "rob_used": false,
    "last_rob_date": "",
    "ntr_fail_date": "",
    "total_draws": 0
  }
}
```

- `wives`：角色 ID 到持有数量的映射，重复抽到会累加
- `total_draws`：累计抽卡次数（仅统计 `/抽老婆`）
- 旧存档升级后，「累计获得 / 重复获得」会立即按历史持有数量计算；「累计抽卡」从 0 开始重新精确统计
- 每日抽卡次数、NTR 锁定均按日期自动重置

## 本次修复 / 改动

- ✅ 抽到重复角色时会正确累计持有数量，并在抽卡结果中显示「持有×N」
- ✅ 图鉴和收集进度都会展示重复数量、累计获得和累计抽卡，不再是「看起来没记录」
- ✅ NTR 成功时数量改为累加，不再用对方数量覆盖自己的数量
- ✅ NTR 成功后发送牛到的角色图片
- ✅ 新增 `/ntr @某人 角色名` 指定角色 NTR，成功率 30%（可配置）
- ✅ 「抽老婆」不带 `/` 也能触发
- ✅ 老婆图鉴整合收集进度（消息文字 + 图片顶部统计 + 进度条）
- ✅ 新增 WebUI 插件配置（`_conf_schema.json`）
- ✅ 存档原子写入、损坏兜底与 `.bak` 备份
- ✅ 临时图片使用唯一文件名，避免多人同时生成时互相覆盖
- ✅ 修正 JPEG 内容保存为 `.png` 后缀导致的格式不一致
- ✅ NTR 解析 At 时跳过机器人自身，避免 @机器人 后接指令时误判
- ✅ 移除调试用 `print`，统一使用插件日志
- ✅ 卡池图片切换为 GitHub + jsDelivr CDN，`wives.json` 使用完整 URL
- ✅ 图鉴缩略图支持远程 URL：本地同名图片优先，缺失时下载到 `data/image_cache/` 缓存

## 注意事项

- 图片读取失败时会显示「图片缺失」占位，不影响其他角色
- 直接发图使用 `wives.json` 中的 URL；图鉴缩略图会优先使用本地 `assets/` 同名文件，缺失时自动下载到 `data/image_cache/`
- 如果删除本地 `assets/` 以做成纯 CDN 部署，首次生成图鉴会逐张下载并缓存，可能较慢；之后会复用缓存
- 生成的大图为 JPEG 压缩保存，降低体积便于 QQ 发送
- 修改 `wives.json` 或配置后，建议在 WebUI 重载插件
- 当前适配 AstrBot v4.28+（NapCat），消息链中 @ 组件使用 `Comp.At(qq=xxx)` 写法
