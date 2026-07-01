"""
scyxjy.py — 三川约战记录工具

功能：
  1. checkActiveTea   — 批量轮询茶馆昨日战绩总数，筛选活跃茶馆
  2. downloadTeaReplay — 下载指定茶馆昨日全部战绩回放(.video)
  3. parseVideoReplay  — 解析 .video 回放文件为结构化数据

用法示例：
    python scyxjy.py
"""

import datetime
import hashlib
import json
import math
import struct
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import platform
import numpy as np

import requests

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
BASE_URL    = "http://phone.foxuc.com"
API_PATH    = "/Ashx/GroService.ashx"
PRIVATE_KEY = "WH3001"
STATION_ID  = 2000

# 同时并发的请求数，避免压垮服务器
MAX_WORKERS = 20

# HTTP 单次请求超时（秒）
HTTP_TIMEOUT = 10


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _md5_upper(s: str) -> str:
    """返回字符串的 MD5 大写摘要（与 Lua md5() 行为一致）。"""
    return hashlib.md5(s.encode()).hexdigest().upper()


def _token_by_group(group_id: int, server_time: int) -> str:
    """构造 getgroupbattleinfo / getgroupbattlebalance 接口的 token。"""
    raw = f"groupid={group_id}&privatekey={PRIVATE_KEY}&servertime={server_time}"
    return _md5_upper(raw)


def _token_by_id(record_id: str, server_time: int) -> str:
    """构造 getgroupbattlegame 接口的 token。"""
    raw = f"id={record_id}&privatekey={PRIVATE_KEY}&servertime={server_time}"
    return _md5_upper(raw)


def _post_gro(action: str, payload: str) -> dict | None:
    """发起一次 GroService POST 请求，返回解析后的 JSON 字典，失败返回 None。"""
    url = BASE_URL + API_PATH + f"?action={action}"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  [网络错误] {action}: {e}", file=sys.stderr)
        return None
    except ValueError:
        print(f"  [解析错误] {action}: 响应非 JSON", file=sys.stderr)
        return None


def get_yesterday_battle_count(group_id: int) -> int | None:
    """
    查询指定茶馆昨天的战绩总数。

    typeid 含义：0=今天, 1=昨天, 2=本周, 3=上周
    """
    server_time = int(time.time())
    token = _token_by_group(group_id, server_time)
    payload = (
        f"groupid={group_id}&typeid=1&servertime={server_time}"
        f"&pageIndex=1&PageSize=6&StationID={STATION_ID}&token={token}"
    )
    data = _post_gro("getgroupbattleinfo", payload)
    if not data or not data.get("Result"):
        return None
    t_battle = data.get("Data", {}).get("tBattle")
    return int(t_battle) if t_battle is not None else None


def _fetch_battle_page(group_id: int, type_id: int, page: int) -> list[str]:
    """
    获取一页战绩列表，返回 RecordID 列表。
    服务端 PageSize 只允许 6（硬编码，大值会被拒绝）。
    """
    server_time = int(time.time())
    token = _token_by_group(group_id, server_time)
    payload = (
        f"groupid={group_id}&typeid={type_id}&servertime={server_time}"
        f"&pageIndex={page}&PageSize=6&StationID={STATION_ID}&token={token}"
    )
    data = _post_gro("getgroupbattleinfo", payload)
    if not data or not data.get("Result"):
        return []
    lst = data.get("Data", {}).get("list") or []
    return [item["RecordID"] for item in lst if item.get("RecordID") and item.get("KindID") == "150"]


def _fetch_video_numbers(record_id: str) -> list[str]:
    """
    通过 getgroupbattlegame 获取一条约战记录下的所有 VideoNumber。
    返回 VideoNumber 字符串列表（过滤掉 0 值）。
    """
    server_time = int(time.time())
    token = _token_by_id(record_id, server_time)
    payload = f"id={record_id}&servertime={server_time}&token={token}"
    data = _post_gro("getgroupbattlegame", payload)
    if not data or not data.get("Result"):
        return []
    g_list = data.get("Data", {}).get("gList") or []
    result = []
    for item in g_list:
        vn = str(item.get("VideoNumber") or "0")
        if vn and vn != "0":
            result.append(vn)
    return result


def _get_video_download_url(video_number: str) -> str | None:
    """
    通过 GetVideoInfo 接口获取 .video 文件的实际下载 URL。
    响应体是纯文本 URL 字符串（非 JSON）。

    注意：必须用 ProService.ashx（返回 video.foxuc.com CDN 地址），
    GroService.ashx 会返回 testwww.foxuc.com 测试地址（文件不存在）。
    """
    server_time = int(time.time())
    url = (
        BASE_URL + "/Ashx/ProService.ashx"
        + f"?action=GetVideoInfo"
        f"&VideoNumber={video_number}"
        f"&ServerTime={server_time}"
        f"&StationID={STATION_ID}"
    )
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        text = resp.text.strip()
        if text and "video" in text.lower():
            return text
        return None
    except requests.RequestException as e:
        print(f"  [GetVideoInfo 错误] {video_number}: {e}", file=sys.stderr)
        return None


def _download_file(url: str, save_path: Path) -> bool:
    """下载二进制文件到 save_path，返回是否成功。"""
    if save_path.exists():
        return True  # 已存在，跳过
    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        return save_path.stat().st_size >= 12  # 最小有效 .video 文件头 12 字节
    except Exception as e:
        print(f"  [下载错误] {url}: {e}", file=sys.stderr)
        if save_path.exists():
            save_path.unlink()
        return False


# ──────────────────────────────────────────────
# .video 回放解析
# ──────────────────────────────────────────────

# 主命令 ID（来自 df.lua）
MDM_GR_USER  = 3    # 用户信息
MDM_GF_FRAME = 100  # 框架命令
MDM_GF_GAME  = 200  # 游戏命令

# 框架子命令
SUB_GF_GAME_SCENE  = 101  # 游戏场景
SUB_GF_TABLE_GRADE = 152  # 桌子战绩

# 用户子命令
SUB_GR_USER_ENTER  = 100  # 用户进入
SUB_GR_USER_SCORE  = 101  # 用户积分
SUB_GR_USER_STATUS = 102  # 用户状态

# 用户动态字段类型
DTP_GR_NICK_NAME = 10  # 用户昵称

COMPRESS_NONE = 0x00
COMPRESS_ZIP  = 0x01

_CMD_NAME = {MDM_GR_USER: "USER", MDM_GF_FRAME: "FRAME", MDM_GF_GAME: "GAME"}


class _Buf:
    """轻量级字节流读取器，对应 Lua CCmd_Data 的 read* 系列方法。"""

    def __init__(self, data: bytes):
        self._data = data
        self._pos  = 0

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def read_bytes(self, n: int) -> bytes:
        raw = self._data[self._pos: self._pos + n]
        self._pos += n
        return raw

    def read_byte(self) -> int:
        v = self._data[self._pos]
        self._pos += 1
        return v

    def read_word(self) -> int:
        v = struct.unpack_from("<H", self._data, self._pos)[0]
        self._pos += 2
        return v

    def read_dword(self) -> int:
        v = struct.unpack_from("<I", self._data, self._pos)[0]
        self._pos += 4
        return v

    def read_int(self) -> int:
        """对应 Lua readint：读取有符号 int32 (4 bytes)。"""
        v = struct.unpack_from("<i", self._data, self._pos)[0]
        self._pos += 4
        return v

    def read_int64(self) -> int:
        """对应 Lua df.readScore：读取 int64 (8 bytes)。"""
        v = struct.unpack_from("<q", self._data, self._pos)[0]
        self._pos += 8
        return v

    def read_wstring(self, n_bytes: int) -> str:
        """读取 n_bytes 字节的 UTF-16 LE 字符串，在第一个 null 宽字符处截断。"""
        raw = self.read_bytes(n_bytes)
        try:
            s = raw.decode("utf-16-le")
            # 在第一个 null 宽字符处截断（C 风格 null-terminated wide string）
            null_pos = s.find("\x00")
            return s[:null_pos] if null_pos >= 0 else s
        except UnicodeDecodeError:
            return raw.hex()

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        self._pos = pos


@dataclass
class VideoUser:
    uid:      int
    chair_id: int
    nick:     str    # 玩家昵称（来自头部用户区 szNickName，LEN_ACCOUNTS=32 宽字符=64字节）


@dataclass
class VideoPacket:
    insert_time: int        # 距游戏开始的毫秒偏移
    chair_id:    int        # 发包玩家椅子号（0xFFFF = 系统）
    main_cmd:    int        # 主命令 ID
    sub_cmd:     int        # 子命令 ID
    cmd_name:    str        # 主命令文字说明
    payload:     bytes      # 原始 payload bytes
    parsed:      dict = field(default_factory=dict)  # 已解析的字段（部分包）


@dataclass
class VideoReplay:
    # 文件头
    start_time:    int          # Unix 时间戳（游戏开始时间）
    pkt_count:     int          # 数据包数量
    user_count:    int          # 玩家数量
    compress_kind: int          # 压缩类型
    # 解压后头部
    kind_id:       int          # 游戏类型 ID
    chair_count:   int          # 椅子数
    process_name:  str          # 游戏进程名
    server_id:     int
    server_type:   int
    server_rule:   int
    room_name:     str
    # 玩家列表
    users:         list[VideoUser]
    # 数据包列表
    packets:       list[VideoPacket]


def _parse_user_enter(buf: _Buf, size: int) -> dict:
    """解析 SUB_GR_USER_ENTER payload（来自 Lua onEventVideoUserEnter）。"""
    start_pos = buf.tell()
    result: dict[str, Any] = {}
    result["game_id"]      = buf.read_dword()
    result["user_id"]      = buf.read_dword()
    result["group_id"]     = buf.read_dword()
    result["face_id"]      = buf.read_word()
    result["custom_id"]    = buf.read_dword()
    result["age"]          = buf.read_byte()
    result["gender"]       = buf.read_byte()
    result["member_order"] = buf.read_byte()
    result["master_order"] = buf.read_byte()
    result["table_id"]     = buf.read_word()
    result["chair_id"]     = buf.read_word()
    result["user_status"]  = buf.read_byte()
    result["gold"]         = buf.read_int64()
    result["score"]        = buf.read_int64()
    result["win_count"]    = buf.read_dword()
    result["lost_count"]   = buf.read_dword()
    result["draw_count"]   = buf.read_dword()
    result["flee_count"]   = buf.read_dword()
    result["experience"]   = buf.read_dword()
    result["loveliness"]   = buf.read_dword()
    # 动态扩展字段（昵称等）
    consumed = buf.tell() - start_pos
    while consumed < size:
        tmp_size = buf.read_word()
        tmp_cmd  = buf.read_word()
        if tmp_size == 0 or tmp_cmd == 0:
            break
        if tmp_cmd == DTP_GR_NICK_NAME:
            result["nick"] = buf.read_wstring(tmp_size)
        else:
            buf.read_bytes(tmp_size)
        consumed = buf.tell() - start_pos
    return result


def _parse_user_score(buf: _Buf, size: int) -> dict:
    """解析 SUB_GR_USER_SCORE payload。"""
    result = {}
    result["user_id"]    = buf.read_dword()
    result["gold"]       = buf.read_int64()
    result["score"]      = buf.read_int64()
    result["win_count"]  = buf.read_dword()
    result["lost_count"] = buf.read_dword()
    result["draw_count"] = buf.read_dword()
    result["flee_count"] = buf.read_dword()
    result["experience"] = buf.read_dword()
    if size == 44:
        buf.read_dword()
    return result


def _parse_table_grade(buf: _Buf, size: int) -> dict:
    """解析 SUB_GF_TABLE_GRADE payload（桌子战绩）。"""
    count = size // 78
    entries = []
    for _ in range(count):
        entry = {
            "chair_id": buf.read_word(),
            "user_id":  buf.read_dword(),
            "score":    buf.read_int64(),    # lAddpuScore (int64)
            "nick":     buf.read_wstring(32 * 2),  # 32 wide chars
        }
        entries.append(entry)
    return {"table_grade": entries}


# ──────────────────────────────────────────────
# KindID=150 红中断勾卡 游戏指令解析
# ──────────────────────────────────────────────

# 游戏子命令 ID（来自 CMD_Game.lua SUB_S_* 系列）
_HZDGK_CMD = {
    100: "游戏开始",
    101: "用户出牌",
    102: "发送扑克",
    104: "操作提示",
    105: "操作结果",
    107: "吃胡命令",
    108: "游戏结束",
    109: "用户托管",
    110: "语音短语",
    112: "动作提醒",
    113: "规则设置",
    114: "规则提醒",
    115: "报胡提醒",
    116: "呼叫转移",
}

# 牌面编码（参考 GameLogic.lua INDEX_DATA）
# 0x11-0x19: 1万-9万  |  0x21-0x29: 1条-9条  |  0x35: 红中（鬼牌）
_CARD_SUIT = {0x1: "万", 0x2: "条", 0x3: "饼"}
_SPECIAL_CARD = {0x35: "红中", 0xFF: "--", 0x00: "--"}


def _card_str(byte_val: int) -> str:
    """将 1 字节牌编码转换为可读字符串，如 0x15→'5万'，0x35→'红中'。"""
    if byte_val in _SPECIAL_CARD:
        return _SPECIAL_CARD[byte_val]
    suit  = (byte_val >> 4) & 0x0F
    value = byte_val & 0x0F
    suit_name = _CARD_SUIT.get(suit, f"?{suit}")
    return f"{value}{suit_name}"


def _cards_str(card_list: list[int]) -> str:
    """将牌列表转成可读字符串，去掉末尾空牌。"""
    result = [_card_str(c) for c in card_list if c not in (0x00, 0xFF)]
    return " ".join(result) if result else "(空)"


# WIK 操作掩码（cbOperateCode / cbActionMask）
_WIK_FLAGS: list[tuple[int, str]] = [
    (0x0001, "左吃"),   (0x0002, "碰"),    (0x0004, "杠"),
    (0x0008, "加杠"),   (0x0010, "报胡"),  (0x0020, "请胡"),
    (0x0040, "吃胡"),   (0x0080, "点炮"),
    (0x0100, "天胡"),   (0x0200, "地胡"),  (0x0400, "放炮"),
    (0x0800, "吃胡动作"), (0x1000, "自摸"), (0x2000, "杠上炮"),
    (0x4000, "杠上花"), (0x8000, "请胡2"),
]

# CHR 胡牌牌型掩码（dwChiHuKind / dwChiHuRight）
_CHR_FLAGS: list[tuple[int, str]] = [
    (0x00000001, "平胡"),     (0x00000002, "对对胡"),   (0x00000004, "清对对"),
    (0x00000008, "将对对"),   (0x00000010, "清一色"),   (0x00000020, "五小对"),
    (0x00000040, "清五对"),   (0x00000080, "将五对"),   (0x00000100, "龙五对"),
    (0x00000200, "清龙五对"), (0x00000400, "将龙五对"), (0x00000800, "清将龙五对"),
    (0x00001000, "带幺九"),   (0x00002000, "对对带幺九"), (0x00004000, "龙五带幺九"),
    (0x00008000, "清幺九"),   (0x00010000, "天胡"),     (0x00020000, "地胡"),
    (0x00100000, "断幺九"),   (0x00200000, "杠上花"),   (0x00400000, "杠上炮"),
    (0x00800000, "抢杠"),     (0x01000000, "海底捞"),   (0x02000000, "海底炮"),
    (0x04000000, "报叫"),
]


def _wik_names(mask: int) -> list[str]:
    return [name for flag, name in _WIK_FLAGS if mask & flag]


def _chr_names(mask: int) -> list[str]:
    return [name for flag, name in _CHR_FLAGS if mask & flag]


MAX_COUNT = 11  # 最大手牌数（cmd.MAX_COUNT）


def _parse_hzdgk_game_start(buf: _Buf, size: int) -> dict:
    """SUB_S_GAME_START (100) — 游戏开始"""
    d: dict[str, Any] = {}
    d["sice_count"]    = buf.read_int()    # 骰子数量
    d["banker_chair"]  = buf.read_word()   # 庄家椅子
    d["current_chair"] = buf.read_word()   # 当前行动椅子
    d["bao_chair"]     = buf.read_word()   # 报叫椅子
    d["action_mask"]   = buf.read_byte()   # 动作掩码
    d["magic_card"]    = _card_str(buf.read_byte())  # 鬼牌
    d["hand_cards"]    = _cards_str([buf.read_byte() for _ in range(MAX_COUNT)])
    # 录像附加数据（视频回放文件固定包含）
    if buf.remaining() >= 12:
        d["heap_head"]  = buf.read_word()
        d["heap_tail"]  = buf.read_word()
        d["heap_info"]  = [[buf.read_byte(), buf.read_byte()] for _ in range(4)]
    return d


def _parse_hzdgk_out_card(buf: _Buf, size: int) -> dict:
    """SUB_S_OUT_CARD (101) — 用户出牌"""
    return {
        "trustee_out": buf.read_byte(),
        "out_chair":   buf.read_word(),
        "card":        _card_str(buf.read_byte()),
    }


def _parse_hzdgk_send_card(buf: _Buf, size: int) -> dict:
    """SUB_S_SEND_CARD (102) — 发送扑克（抓牌）"""
    return {
        "card":          _card_str(buf.read_byte()),
        "action_mask":   _wik_names(buf.read_byte()),
        "current_chair": buf.read_word(),
        "is_tail":       buf.read_byte(),
    }


def _parse_hzdgk_operate_notify(buf: _Buf, size: int) -> dict:
    """SUB_S_OPERATE_NOTIFY (104) — 操作提示"""
    return {
        "resume_chair": buf.read_word(),
        "action_mask":  _wik_names(buf.read_byte()),
        "action_card":  _card_str(buf.read_byte()),
    }


def _parse_hzdgk_operate_result(buf: _Buf, size: int) -> dict:
    """SUB_S_OPERATE_RESULT (105) — 操作结果（吃/碰/杠）"""
    d: dict[str, Any] = {}
    d["operate_chair"] = buf.read_word()
    d["provide_chair"] = buf.read_word()
    d["operate_code"]  = _wik_names(buf.read_byte())
    d["operate_cards"] = _cards_str([buf.read_byte() for _ in range(3)])
    d["user_action"]   = buf.read_byte()
    d["exclude_card"]  = _card_str(buf.read_byte())
    return d


def _parse_hzdgk_chihu_result(buf: _Buf, size: int) -> dict:
    """SUB_S_CHIHU_RESULT (107) — 吃胡命令"""
    d: dict[str, Any] = {}
    d["operate_chair"] = buf.read_word()
    d["provide_chair"] = buf.read_word()
    d["hu_kind"]       = _wik_names(buf.read_word())
    d["operate_card"]  = _card_str(buf.read_byte())
    d["multi_pao"]     = buf.read_byte()      # 一炮多响
    d["qing_hu_flag"]  = buf.read_byte()      # 请胡标志
    card_count         = buf.read_byte()
    d["card_count"]    = card_count
    # 固定读取 MAX_COUNT 字节（协议填充），只显示实际 card_count 张
    all_cards = [buf.read_byte() for _ in range(MAX_COUNT)]
    d["cards"] = _cards_str(all_cards[:card_count])
    return d


def _parse_hzdgk_game_end(buf: _Buf, size: int) -> dict:
    """SUB_S_GAME_END (108) — 游戏结束（完整结算）"""
    d: dict[str, Any] = {}
    d["cell_score"]      = buf.read_int64()                                # 单元积分
    d["provide_chairs"]  = [buf.read_word()  for _ in range(4)]            # 供应用户
    d["escape_chair"]    = buf.read_word()                                 # 逃跑玩家
    d["escape_fan"]      = buf.read_byte()                                 # 逃跑番数
    d["geng_count"]      = [buf.read_byte() for _ in range(4)]             # 根牌数目
    d["chihu_order"]     = [buf.read_byte() for _ in range(4)]             # 胡牌顺序
    # dwChiHuKind: WIK flags，表示胡牌动作（自摸/请胡/吃胡等）
    d["chihu_kind"]      = [_wik_names(buf.read_dword()) for _ in range(4)]
    # dwChiHuRight[i][1]: CHR flags = 真正的手牌牌型（清五对/平胡等）
    # dwChiHuRight[i][2]: 扩展信息
    chihu_right_raw = [[buf.read_dword(), buf.read_dword()] for _ in range(4)]
    d["chihu_right"]     = chihu_right_raw
    d["hand_type"]       = [_chr_names(r[0]) for r in chihu_right_raw]
    d["game_score"]      = [buf.read_int64() for _ in range(4)]            # 游戏积分
    d["zimo_add_score"]  = [[buf.read_int64() for _ in range(4)] for _ in range(4)]  # 自摸加分
    card_counts          = [buf.read_byte() for _ in range(4)]             # 手牌数目
    d["card_count"]      = card_counts
    # 固定读取 MAX_COUNT 字节（协议填充），按各自 card_count 截断显示
    d["card_data"]       = [
        _cards_str([buf.read_byte() for _ in range(MAX_COUNT)][:card_counts[i]])
        for i in range(4)
    ]
    d["card_type"]       = [buf.read_byte() for _ in range(4)]             # 牌型(0花猪/1未叫/2下叫/3胡牌)
    d["geng_count2"]     = [buf.read_byte() for _ in range(4)]             # 玩家根数目
    d["qia_count"]       = [buf.read_byte() for _ in range(4)]             # 掐数目
    d["jiao_bei_shu"]    = [buf.read_byte() for _ in range(4)]             # 查叫倍数
    settle = []
    for _ in range(4):
        s: dict[str, Any] = {}
        s["wind_fan"]    = [buf.read_int() for _ in range(2)]              # 刮风番数
        s["rain_fan"]    = [buf.read_int() for _ in range(2)]              # 下雨番数
        s["chihu_fan"]   = [buf.read_int() for _ in range(4)]             # 吃胡番数
        s["transfer_score"] = buf.read_int64()                            # 转移积分
        settle.append(s)
    d["settlement"] = settle
    return d


def _parse_hzdgk_trustee(buf: _Buf, size: int) -> dict:
    """SUB_S_TRUSTEE (109) — 用户托管"""
    return {"trustee": buf.read_byte(), "chair": buf.read_word()}


def _parse_hzdgk_voice(buf: _Buf, size: int) -> dict:
    """SUB_S_VOICEPHRASE (110) — 语音短语"""
    return {"phrase_index": buf.read_word(), "chair": buf.read_word()}


def _parse_hzdgk_game_action(buf: _Buf, size: int) -> dict:
    """SUB_S_GAME_ACTION_NOTIFY (112) — 动作提醒（刮风/下雨/胡牌等）"""
    action_type_name = {0x01: "刮风", 0x02: "下雨", 0x04: "胡牌"}
    atype = buf.read_byte()
    return {
        "action_type":   action_type_name.get(atype, f"?{atype:#04x}"),
        "action_code":   buf.read_byte(),
        "operater_chair": buf.read_word(),
        "provide_chair":  buf.read_word(),
        "per_user_fan":   [buf.read_int() for _ in range(4)],  # 每人番数
    }


def _parse_hzdgk_rule_setting(buf: _Buf, size: int) -> dict:
    """SUB_S_GAME_RULE_SETTING (113) — 规则设置"""
    return {"cell_score": buf.read_int64()}


def _parse_hzdgk_rule_notify(buf: _Buf, size: int) -> dict:
    """SUB_S_GAME_RULE_NOTIFY (114) — 规则提醒"""
    return {
        "force_exit":    bool(buf.read_byte()),
        "prompt_notice": bool(buf.read_byte()),
        "player_count":  buf.read_byte(),
        "cell_score":    buf.read_int64(),
    }


def _parse_hzdgk_bao_hu_notify(buf: _Buf, size: int) -> dict:
    """SUB_S_BAO_HU_NOTIFY (115) — 报胡提醒"""
    return {
        "current_chair": buf.read_word(),
        "last_chair":    buf.read_word(),
        "bao_hu_flag":   buf.read_byte(),
        "card":          _card_str(buf.read_byte()),
    }


def _parse_hzdgk_transfer_notify(buf: _Buf, size: int) -> dict:
    """SUB_S_TRANSFER_NOTIFY (116) — 呼叫转移"""
    return {
        "action_index":   buf.read_byte(),
        "transfer_count": buf.read_byte(),
        "transfer_score": [buf.read_int64() for _ in range(4)],
    }


_HZDGK_PARSERS: dict[int, Any] = {
    100: _parse_hzdgk_game_start,
    101: _parse_hzdgk_out_card,
    102: _parse_hzdgk_send_card,
    104: _parse_hzdgk_operate_notify,
    105: _parse_hzdgk_operate_result,
    107: _parse_hzdgk_chihu_result,
    108: _parse_hzdgk_game_end,
    109: _parse_hzdgk_trustee,
    110: _parse_hzdgk_voice,
    112: _parse_hzdgk_game_action,
    113: _parse_hzdgk_rule_setting,
    114: _parse_hzdgk_rule_notify,
    115: _parse_hzdgk_bao_hu_notify,
    116: _parse_hzdgk_transfer_notify,
}


def parseVideoReplay(filepath: str | Path) -> VideoReplay:
    """
    解析 .video 格式的战绩回放文件，返回 VideoReplay 结构化对象。

    .video 格式（参考 GameFrameEngine.lua onVideoAnalysis）：

    文件头（12 字节，未压缩）：
      [0-3]  dword   start_time     游戏开始 Unix 时间戳
      [4-7]  dword   data_size      原始数据总大小（含文件头）
      [8-9]  word    pkt_count      数据包数量
      [10]   byte    user_count     玩家数量
      [11]   byte    compress_kind  压缩类型（0=无，1=ZIP）

    解压后主体（little-endian）：
      注意：Lua readstring(n) 读取 n 个宽字符 = n×2 字节（UTF-16 LE）
      word    kind_id          游戏类型 ID
      word    chair_count      椅子数
      bytes64 process_name     进程名（UTF-16 LE，LEN_PROCESS=32 wchars = 64 字节）
      word    server_id
      word    server_type
      dword   server_rule
      bytes64 room_name        房间名（UTF-16 LE，LEN_SERVER=32 wchars = 64 字节）
      用户区（user_count 条，对应 Lua onVideoAnalysis）：
        dword   uid            用户 ID
        word    chair_id       椅子号
        bytes64 nick           昵称（UTF-16 LE，LEN_ACCOUNTS=32 wchars = 64 字节）
      数据包区（pkt_count 条）：
        word    pkt_size       整包大小（Lua 侧忽略，仅供参考）
        dword   pkt_pos        包位置
        dword   insert_time    距游戏开始的时间偏移（ms）
        word    chair_id       发包椅子号
        word    main_cmd       主命令 ID
        word    sub_cmd        子命令 ID
        word    data_size      payload 大小
        bytes   payload        data_size 字节的有效载荷

    参数：
        filepath — .video 文件路径

    返回：
        VideoReplay 实例
    """
    filepath = Path(filepath)
    raw = filepath.read_bytes()

    # ── 解析文件头 ──────────────────────────────────────────
    if len(raw) < 12:
        raise ValueError(f"文件太小（{len(raw)} 字节），不是有效的 .video 文件")

    start_time    = struct.unpack_from("<I", raw, 0)[0]
    data_size     = struct.unpack_from("<I", raw, 4)[0]
    pkt_count     = struct.unpack_from("<H", raw, 8)[0]
    user_count    = raw[10]
    compress_kind = raw[11]

    # ── 解压主体 ─────────────────────────────────────────────
    compressed = raw[12:]
    if compress_kind == COMPRESS_ZIP:
        body = zlib.decompress(compressed)
    elif compress_kind == COMPRESS_NONE:
        body = compressed
    else:
        raise ValueError(f"不支持的压缩类型: {compress_kind}")

    buf = _Buf(body)

    # ── 读取固定头部 ─────────────────────────────────────────
    # readstring(n) 在 Lua/Cocos 绑定中读取 n 个宽字符 = n*2 字节
    # LEN_PROCESS = LEN_SERVER = LEN_ACCOUNTS = 32 宽字符 = 64 字节
    kind_id      = buf.read_word()
    chair_count  = buf.read_word()
    process_name = buf.read_wstring(64)   # LEN_PROCESS=32 wide chars = 64 bytes
    server_id    = buf.read_word()
    server_type  = buf.read_word()
    server_rule  = buf.read_dword()
    room_name    = buf.read_wstring(64)   # LEN_SERVER=32 wide chars = 64 bytes

    # ── 读取用户区 ───────────────────────────────────────────
    # 结构（与 Lua onVideoAnalysis 一致）：
    #   uid(4) + chairID(2) + szNickName(64) = 70 字节/用户
    # szNickName 就是真实玩家昵称，room_name 字段才是房间名
    users: list[VideoUser] = []
    for _ in range(user_count):
        uid      = buf.read_dword()
        chair_id = buf.read_word()
        nick     = buf.read_wstring(64)   # LEN_ACCOUNTS=32 wide chars = 64 bytes
        users.append(VideoUser(uid=uid, chair_id=chair_id, nick=nick))

    # 包数据紧跟在用户区之后，无需扫描

    # ── 解析数据包 ───────────────────────────────────────────
    packets: list[VideoPacket] = []
    for _ in range(pkt_count):
        if buf.remaining() < 18:
            break
        _pkt_size   = buf.read_word()   # 整包大小，Lua 侧未使用
        _pkt_pos    = buf.read_dword()  # 包位置
        insert_time = buf.read_dword()  # 时间偏移
        chair_id    = buf.read_word()
        main_cmd    = buf.read_word()
        sub_cmd     = buf.read_word()
        data_size   = buf.read_word()
        payload     = buf.read_bytes(data_size)

        parsed: dict[str, Any] = {}
        try:
            pb = _Buf(payload)
            if main_cmd == MDM_GR_USER:
                if sub_cmd == SUB_GR_USER_ENTER:
                    parsed = _parse_user_enter(pb, data_size)
                elif sub_cmd == SUB_GR_USER_SCORE:
                    parsed = _parse_user_score(pb, data_size)
            elif main_cmd == MDM_GF_FRAME and sub_cmd == SUB_GF_TABLE_GRADE:
                parsed = _parse_table_grade(pb, data_size)
            elif main_cmd == MDM_GF_GAME and kind_id == 150:
                # 红中断勾卡 游戏指令
                parser = _HZDGK_PARSERS.get(sub_cmd)
                if parser:
                    parsed = {"_cmd": _HZDGK_CMD.get(sub_cmd, f"?{sub_cmd}"),
                              **parser(pb, data_size)}
        except Exception:
            pass  # 解析失败保留 payload 原始字节

        packets.append(VideoPacket(
            insert_time=insert_time,
            chair_id=chair_id,
            main_cmd=main_cmd,
            sub_cmd=sub_cmd,
            cmd_name=_CMD_NAME.get(main_cmd, f"UNKNOWN({main_cmd})"),
            payload=payload,
            parsed=parsed,
        ))

    # ── 用 USER_ENTER 包里的昵称补全/覆盖玩家列表 ──────────────
    # 头部用户区的 szNickName 已是玩家真实昵称；USER_ENTER 包提供二次确认，
    # 若头部昵称为空则用 USER_ENTER 里的值填入。
    uid_to_user = {u.uid: u for u in users}
    for pkt in packets:
        if (pkt.main_cmd == MDM_GR_USER
                and pkt.sub_cmd == SUB_GR_USER_ENTER
                and pkt.parsed):
            uid  = pkt.parsed.get("user_id")
            nick = pkt.parsed.get("nick", "")
            if uid and uid in uid_to_user and not uid_to_user[uid].nick:
                uid_to_user[uid].nick = nick

    return VideoReplay(
        start_time=start_time,
        pkt_count=pkt_count,
        user_count=user_count,
        compress_kind=compress_kind,
        kind_id=kind_id,
        chair_count=chair_count,
        process_name=process_name,
        server_id=server_id,
        server_type=server_type,
        server_rule=server_rule,
        room_name=room_name,
        users=users,
        packets=packets,
    )


# ──────────────────────────────────────────────
# 核心函数
# ──────────────────────────────────────────────

def checkActiveTea(start_id: int, count: int, threshold: int = 1000) -> list[int]:
    """
    轮询从 start_id 开始的 count 个茶馆，打印昨天战绩总数超过 threshold 的茶馆号。

    参数：
        start_id  — 起点茶馆号（含）
        count     — 轮询数量
        threshold — 战绩总数阈值，超过此值才打印（默认 1000）

    返回：
        满足条件的茶馆号列表
    """
    end_id = start_id + count  # 不含
    print(f"开始轮询茶馆 {start_id} ~ {end_id - 1}（共 {count} 个），昨日战绩 > {threshold} 的茶馆将被打印")
    print("-" * 60)

    active: list[int] = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {
            pool.submit(get_yesterday_battle_count, gid): gid
            for gid in range(start_id, end_id)
        }

        for future in as_completed(future_map):
            gid = future_map[future]
            total = future.result()
            done += 1

            if done % 100 == 0:
                print(f"  进度：{done}/{count} ...")

            if total is not None and total > threshold:
                active.append(gid)
                print(f"  ★ 茶馆 {gid}  昨日战绩：{total} 局")

    active.sort()
    print("-" * 60)
    print(f"轮询完成，共发现 {len(active)} 个活跃茶馆（昨日战绩 > {threshold}）：")
    for gid in active:
        print(f"  茶馆号：{gid}")

    return active


def downloadTeaReplay(group_id: int, limit: int | None = None) -> None:
    """
    下载指定茶馆昨日战绩的回放文件。

    保存路径：res/battleReplay/<茶馆号>_<日期>/
    文件命名：<VideoNumber>.video

    流程：
      1. 分页拉取昨日所有 RecordID（或前 limit 条）
      2. 并发请求 getgroupbattlegame 取得每条记录的 VideoNumber 列表
      3. 并发请求 GetVideoInfo 取得各 VideoNumber 的真实下载 URL
      4. 并发下载 .video 文件

    参数：
        group_id — 茶馆号
        limit    — 仅处理前 N 条战绩；None 表示全量下载
    """
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y%m%d")
    sys_os = platform.system()
    if sys_os == "Windows":
        save_dir = Path("E:\\Train") / f"{group_id}_{date_str}"
        save_dir.mkdir(parents=True, exist_ok=True)
    elif sys_os == "Darwin":
        save_dir = Path("/Users/kebiaoy/Documents/MjTrainData") / f"{group_id}_{date_str}"
        save_dir.mkdir(parents=True, exist_ok=True)

    print(f"茶馆 {group_id}  昨日（{date_str}）回放下载")
    print(f"保存目录：{save_dir}")
    print("-" * 60)

    # ── Step 1：获取总数与总页数 ──────────────────────────────
    # 服务端 PageSize 只允许 6
    PAGE_SIZE = 6
    server_time = int(time.time())
    token = _token_by_group(group_id, server_time)
    payload = (
        f"groupid={group_id}&typeid=1&servertime={server_time}"
        f"&pageIndex=1&PageSize={PAGE_SIZE}&StationID={STATION_ID}&token={token}"
    )
    first = _post_gro("getgroupbattleinfo", payload)
    if not first or not first.get("Result"):
        print("获取战绩列表失败，请检查茶馆号或网络。")
        return

    t_battle = int(first.get("Data", {}).get("tBattle") or 0)
    if t_battle == 0:
        print("昨日无战绩记录。")
        return

    target = min(t_battle, limit) if limit else t_battle
    total_pages = math.ceil(target / PAGE_SIZE)
    print(f"昨日共 {t_battle} 条战绩，本次处理前 {target} 条（{total_pages} 页）...")

    # 先把第 1 页的 RecordID 存入
    first_ids = [item["RecordID"] for item in (first.get("Data", {}).get("list") or [])
                 if item.get("RecordID") and item.get("KindID") == "150"]

    all_record_ids: list[str] = list(first_ids)

    # ── Step 2：并发拉取剩余页 ────────────────────────────────
    if total_pages > 1:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_fetch_battle_page, group_id, 1, p): p
                for p in range(2, total_pages + 1)
            }
            fetched = 1
            for fut in as_completed(futures):
                all_record_ids.extend(fut.result())
                fetched += 1
                if fetched % 100 == 0:
                    print(f"  列表拉取进度：{fetched}/{total_pages} 页 ...")

    # 截断到目标数量
    all_record_ids = all_record_ids[:target]

    print(f"实际获取到 {len(all_record_ids)} 条 RecordID，开始获取 VideoNumber...")

    # ── Step 3：并发获取每条记录的 VideoNumber ────────────────
    all_video_numbers: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_video_numbers, rid): rid for rid in all_record_ids}
        for fut in as_completed(futures):
            all_video_numbers.extend(fut.result())
            done += 1
            if done % 200 == 0:
                print(f"  获取进度：{done}/{len(all_record_ids)} 条记录，"
                      f"已收集 {len(all_video_numbers)} 个 VideoNumber ...")

    # 去重（同一局可能被多条记录引用）
    all_video_numbers = list(dict.fromkeys(all_video_numbers))
    print(f"共 {len(all_video_numbers)} 个唯一 VideoNumber，开始获取下载 URL...")

    # ── Step 4：并发获取下载 URL ──────────────────────────────
    video_url_map: dict[str, str] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_get_video_download_url, vn): vn for vn in all_video_numbers}
        for fut in as_completed(futures):
            vn = futures[fut]
            url = fut.result()
            if url:
                video_url_map[vn] = url
            done += 1
            if done % 200 == 0:
                print(f"  URL 获取进度：{done}/{len(all_video_numbers)} ...")

    print(f"成功获取 {len(video_url_map)} 个下载 URL，开始下载 .video 文件...")

    # ── Step 5：并发下载 .video 文件 ──────────────────────────
    DOWNLOAD_WORKERS = 10
    success = 0
    fail = 0
    done = 0
    total = len(video_url_map)

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_file, url, save_dir / f"{vn}.video"): vn
            for vn, url in video_url_map.items()
        }
        for fut in as_completed(futures):
            ok = fut.result()
            if ok:
                success += 1
            else:
                fail += 1
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  下载进度：{done}/{total}  成功 {success}  失败 {fail}")

    print("-" * 60)
    print(f"下载完成！成功 {success} 个，失败 {fail} 个")
    print(f"文件保存在：{save_dir}")


def testParseVideoReplay(filePath:str):
    import datetime as _dt
    from collections import Counter

    # 使用一个 KindID=150（红中断勾卡）的文件测试

    # 有报胡测试
    #TEST_FILE = "res/battleReplay/61263_20260618/09856202606180204803.video"

    TEST_FILE = filePath
    replay = parseVideoReplay(TEST_FILE)

    print("=" * 60)
    print("【文件头】")
    print(f"  游戏开始时间: {_dt.datetime.fromtimestamp(replay.start_time)}")
    print(f"  游戏类型 KindID: {replay.kind_id}"
          + (" (红中断勾卡)" if replay.kind_id == 150 else ""))
    print(f"  椅子数: {replay.chair_count}  玩家数: {replay.user_count}")
    print(f"  进程名: {replay.process_name}")
    print(f"  数据包总数: {replay.pkt_count}")
    print(f"  房间名称: {repr(replay.room_name)}")

    print()
    print("【玩家列表】")
    for u in replay.users:
        print(f"  uid={u.uid}  chairID={u.chair_id}  nick={repr(u.nick)}")

    print()
    print("【数据包统计】")
    cmd_stat: Counter = Counter()
    for p in replay.packets:
        if p.main_cmd == MDM_GF_GAME and replay.kind_id == 150 and p.parsed:
            cmd_stat[p.parsed.get("_cmd", f"GAME/{p.sub_cmd}")] += 1
        else:
            cmd_stat[f"{p.cmd_name}/sub={p.sub_cmd}"] += 1
    for name, cnt in sorted(cmd_stat.items(), key=lambda x: -x[1]):
        print(f"  {name}: {cnt} 包")

    if replay.kind_id == 150:
        print()
        print("【游戏指令详情（红中断勾卡）】")
        game_pkts = [p for p in replay.packets
                     if p.main_cmd == MDM_GF_GAME and p.parsed]
        for i, p in enumerate(game_pkts):
            d = p.parsed
            cmd_label = d.get("_cmd", f"sub={p.sub_cmd}")
            # 按指令类型输出关键字段
            if p.sub_cmd == 100:  # 游戏开始
                bao = d.get('bao_chair', 0xFFFF)
                bao_str = "无" if bao == 0xFFFF else str(bao)
                mask = d.get('action_mask') or []
                mask_str = f"  报胡掩码={mask}" if mask else ""
                print(f"  [{i:3d}] {cmd_label}  庄={d.get('banker_chair')}  "
                      f"鬼牌={d.get('magic_card')}  "
                      f"首个报胡决策玩家={bao_str}{mask_str}  "
                      f"手牌={d.get('hand_cards')}")
            elif p.sub_cmd == 101:  # 出牌
                trustee_str = "AI托管" if d.get('trustee_out') else "手动"
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('out_chair')}  "
                      f"牌={d.get('card')}  [{trustee_str}]")
            elif p.sub_cmd == 102:  # 抓牌
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('current_chair')}  "
                      f"牌={d.get('card')}  可操作={d.get('action_mask')}")
            elif p.sub_cmd == 104:  # 操作提示
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('resume_chair')}  "
                      f"操作={d.get('action_mask')}  牌={d.get('action_card')}")
            elif p.sub_cmd == 105:  # 操作结果
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('operate_chair')}←{d.get('provide_chair')}  "
                      f"操作={d.get('operate_code')}  牌={d.get('operate_cards')}")
            elif p.sub_cmd == 107:  # 胡牌
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('operate_chair')}←{d.get('provide_chair')}  "
                      f"牌型={d.get('hu_kind')}  牌={d.get('operate_card')}  "
                      f"手牌({d.get('card_count')}张)={d.get('cards')}")
                # ── 诊断：输出原始 payload hex 供核对 ──
                raw = p.payload
                print(f"         [诊断] 包大小={len(raw)}字节 (期望21)  hex={raw.hex()}")
                # 按字段逐字节标注
                if len(raw) >= 21:
                    fields = [
                        ("operate_chair", raw[0:2].hex()),
                        ("provide_chair",  raw[2:4].hex()),
                        ("wUserHuKind",    raw[4:6].hex()),
                        ("cbOperateCard",  f"{raw[6]:02x}={_card_str(raw[6])}"),
                        ("bMultplePao",    f"{raw[7]:02x}"),
                        ("bQingHuFlag",    f"{raw[8]:02x}"),
                        ("cbCardCount",    f"{raw[9]:02x}={raw[9]}"),
                        ("cbCardData",     raw[10:21].hex()),
                    ]
                    for fname, fval in fields:
                        print(f"           {fname:15s}: {fval}")
            elif p.sub_cmd == 108:  # 游戏结束
                scores = d.get("game_score", [])
                print(f"  [{i:3d}] {cmd_label}  得分={scores}  "
                      f"胡动作={d.get('chihu_kind')}  手牌型={d.get('hand_type')}")
                # ── 诊断：输出 game_end 关键偏移处的字节 ──
                raw = p.payload
                print(f"         [诊断] 包大小={len(raw)}字节 (期望459)")
                if len(raw) >= 75:
                    def _v32(b, off): return int.from_bytes(b[off:off+4], 'little')
                    print(f"                dwChiHuKind(WIK) offset[27:43]: "
                          f"P0={_wik_names(_v32(raw,27))}  P1={_wik_names(_v32(raw,31))}")
                    print(f"                dwChiHuRight[0] offset[43:51]: "
                          f"CHR={_chr_names(_v32(raw,43))} raw={_v32(raw,43):#010x}  "
                          f"ext={_v32(raw,47):#010x}")
                    print(f"                dwChiHuRight[1] offset[51:59]: "
                          f"CHR={_chr_names(_v32(raw,51))} raw={_v32(raw,51):#010x}")
            elif p.sub_cmd == 112:  # 动作提醒
                print(f"  [{i:3d}] {cmd_label}  类型={d.get('action_type')}  "
                      f"chair={d.get('operater_chair')}←{d.get('provide_chair')}  "
                      f"fan={d.get('per_user_fan')}")
            elif p.sub_cmd == 115:  # 报胡提醒
                # wCurrentUser=65535(INVALID_CHAIR) 表示本轮报胡环节已结束/无人需决策
                # wLastUser = 刚刚做出报胡/不报胡决策的玩家
                cur = d.get('current_chair', 0)
                cur_str = "无(本轮结束)" if cur == 0xFFFF else str(cur)
                last = d.get('last_chair', 0)
                bao = "报胡✓" if d.get('bao_hu_flag') else "不报"
                print(f"  [{i:3d}] {cmd_label}  "
                      f"决策玩家={cur_str}  刚决策={last}({bao})  "
                      f"牌={d.get('card')}")
            else:
                print(f"  [{i:3d}] {cmd_label}  {d}")

    print("=" * 60)

# ──────────────────────────────────────────────
# 训练数据生成
# ──────────────────────────────────────────────

# 通道/牌型常量
CHAN_TOTAL  = 220   # 通道数 0-219（Ch204 已删除，原 Ch205-220 下移为 Ch204-219）
CARD_TYPES  = 19   # 19 种牌：9万 + 9条 + 红中

# WIK 动作掩码（与游戏 CMD_Game.lua 一致）
_WIK_BAO_HU  = 0x10
_WIK_PENG    = 0x02
_WIK_GANG    = 0x04
_WIK_JIA_GANG = 0x08   # 加杠（对已有碰副露加一张变四张）

# 无效椅子
_INVALID_CHAIR = 0xFFFF

# 河牌通道起始偏移（与 wiki 通道设计对齐）
_SELF_FWD_CH   = 5    # 正序自家前 9 张：Ch  5-22  (9槽×2 = 18通道)
_SELF_BWD_CH   = 23   # 倒序自家近28张：Ch 23-78  (28槽×2 = 56通道)
_SELF_DECAY_CH = 79   # 自家时序衰减：   Ch 79-82  (4通道)
_OPP_FWD_CH    = 83   # 正序对家前 9 张：Ch 83-100 (18通道)
_OPP_BWD_CH    = 101  # 倒序对家近28张：Ch101-156 (56通道)
_OPP_DECAY_CH  = 157  # 对家时序衰减：   Ch157-160 (4通道)

# 牌名（索引 0-18）
_CARD_TYPE_NAMES: list[str] = [
    "1万","2万","3万","4万","5万","6万","7万","8万","9万",
    "1条","2条","3条","4条","5条","6条","7条","8条","9条",
    "红中",
]


def _card_to_idx(card: int) -> int:
    """
    将牌字节映射到 0-18 的索引（与 GameLogic.lua CARD_INDEX 对齐）。

    编码规则（来自 GameLogic.lua INDEX_DATA）：
      0x11-0x19 → 万1-9  → 索引 0-8
      0x21-0x29 → 条1-9  → 索引 9-17
      0x35      → 红中   → 索引 18
    无效牌（0x00 / 0xFF）返回 -1。
    """
    if card in (0x00, 0xFF):
        return -1
    suit  = (card >> 4) & 0x0F
    value = (card & 0x0F) - 1   # 0-based
    if suit == 0x1:              # 万
        return value
    if suit == 0x2:              # 条
        return 9 + value
    if card == 0x35:             # 红中
        return 18
    return -1


def _hand_to_ch0_3(hand: list[int]) -> np.ndarray:
    """
    将手牌字节列表转换为通道 0-3（形状 4×19）。

    通道语义（与 Mortal 风格对齐）：
      channels[k][i] = 1.0  当且仅当 牌类型 i 在手牌中至少有 (k+1) 张
    """
    out = np.zeros((4, CARD_TYPES), dtype=np.float32)
    cnt = [0] * CARD_TYPES
    for c in hand:
        idx = _card_to_idx(c)
        if 0 <= idx < CARD_TYPES:
            cnt[idx] += 1
    for i, n in enumerate(cnt):
        for k in range(min(n, 4)):
            out[k, i] = 1.0
    return out


def _one_hot_card(card: int) -> np.ndarray:
    """牌字节 → 19 维 one-hot 向量（无效牌返回全零）。"""
    v = np.zeros(CARD_TYPES, dtype=np.float32)
    idx = _card_to_idx(card)
    if 0 <= idx < CARD_TYPES:
        v[idx] = 1.0
    return v


# 河牌条目类型：(gang_card, discard_card, taken)
#   gang_card   : 本巡发生杠的牌字节（0 = 无杠）
#   discard_card: 弃出的牌字节（用于 Ch1 和时序衰减，取原始值）
#   taken       : True = 被对方碰/直杠走（Ch1 置全零）
KawaEntry = tuple[int, int, bool]

# 副露条目类型：(card_byte, num_cards, type_value)
#   card_byte : 副露中牌的字节（全部同类型，取任意一张即可）
#   num_cards : 3=碰，4=杠
#   type_value: Ch4 广播值：碰=0.0, 直杠=0.33, 加杠=0.67, 暗杠=1.0
MeldEntry = tuple[int, int, float]

# 副露类型广播值（与 wiki Ch4 定义对齐）
_MELD_PENG    = 0.0    # 碰
_MELD_ZHIGANG = 0.33   # 直杠/刮风
_MELD_JIAGANG = 0.67   # 加杠/面下杠
_MELD_ANGANG  = 1.0    # 暗杠/下雨


def _fill_kawa_channels(
    channels: np.ndarray,
    kawa: list[KawaEntry],
    ch_fwd: int,
    ch_bwd: int,
    ch_decay: int,
) -> None:
    """
    填充一个玩家的河牌三段通道（in-place 修改）。

    正序段（ch_fwd 起，占 18 通道）：
      从河牌开头取最多 9 张，每张占 2 通道：
        Ch+0 = gang_card one-hot （本巡是否发生杠，及杠的是哪张牌）
        Ch+1 = discard one-hot   （弃牌；被碰/直杠走则全0）

    倒序段（ch_bwd 起，占 56 通道）：
      从河牌末尾取最多 28 张，逆序（最新在前），每张占 2 通道：
        同上

    时序衰减段（ch_decay 起，占 4 通道）：
      v = exp(-0.2 × 距当前的步数)
      同种牌可多次打出，4 个通道对应"第1次/2次/3次/4次打出"。
      被碰/直杠走的牌同样参与衰减。
    """
    _ZERO19 = np.zeros(CARD_TYPES, dtype=np.float32)

    # ── 正序前 9 张 ────────────────────────────────────────────
    for slot in range(min(9, len(kawa))):
        gang_c, discard_c, taken = kawa[slot]
        ch = ch_fwd + slot * 2
        channels[ch]     = _one_hot_card(gang_c)
        channels[ch + 1] = _ZERO19.copy() if taken else _one_hot_card(discard_c)

    # ── 倒序最近 28 张 ─────────────────────────────────────────
    recent = kawa[-28:]
    for slot, (gang_c, discard_c, taken) in enumerate(reversed(recent)):
        ch = ch_bwd + slot * 2
        channels[ch]     = _one_hot_card(gang_c)
        channels[ch + 1] = _ZERO19.copy() if taken else _one_hot_card(discard_c)

    # ── 时序衰减 ───────────────────────────────────────────────
    # 4个通道：同种牌第1/2/3/4次被打出各占一个通道
    decay_rows = np.zeros((4, CARD_TYPES), dtype=np.float32)
    occur = [0] * CARD_TYPES
    for step, (_, discard_c, _) in enumerate(reversed(kawa)):
        idx = _card_to_idx(discard_c)
        if 0 <= idx < CARD_TYPES and occur[idx] < 4:
            decay_rows[occur[idx], idx] = math.exp(-0.2 * step)
            occur[idx] += 1
    channels[ch_decay: ch_decay + 4] = decay_rows


def _fill_meld_channels(
    channels: np.ndarray,
    melds: list[MeldEntry],
    ch_start: int,
) -> None:
    """
    填充副露通道（4组×5通道，共 20 通道，in-place 修改）。

    每组占 5 个通道（ch_start + group*5）：
      Ch+0 ~ Ch+3 : 每张牌的 19 维 one-hot（碰时 Ch+3 全零）
      Ch+4        : 副露类型全广播（碰=0.0 / 直杠=0.33 / 加杠=0.67 / 暗杠=1.0）
    """
    for grp, (card, num_cards, type_val) in enumerate(melds[:4]):
        base = ch_start + grp * 5
        oh   = _one_hot_card(card)
        for pos in range(min(num_cards, 4)):
            channels[base + pos] = oh
        channels[base + 4, :] = type_val


def _std_shanten(cnt18: list[int], k: int) -> int:
    """
    标准形向听数（k 组合 + 1 将，不含万能牌）。

    cnt18: 长度 18 的计数数组（索引 0-8 = 万1-万9，9-17 = 条1-条9）
    k    : 还需凑成的组合数

    返回：向听数（-1=胡，0=听，1..= 差n张）

    算法：外层枚举将头位置，内层 DFS 最大化组合得分。

    公式：shanten = 2*k - best_score
    其中 best_score = 2*melds + min(partials, k-melds) [+ 1 若有将]

    关键正确性：将头只从"2张同牌"提取，不允许顺子等张占据将头槽。
    """
    def _max_meld_score(c: list[int]) -> int:
        """最大化：2*melds + min(partials, k - melds)，不含将头。"""
        best_s = [0]

        def dfs(i: int, m: int, q: int) -> None:
            while i < 18 and c[i] == 0:
                i += 1
            if i >= 18:
                sc = 2 * m + min(q, k - m)
                if sc > best_s[0]:
                    best_s[0] = sc
                return

            ci = c[i]

            # (A) 刻子
            if m < k and ci >= 3:
                c[i] -= 3; dfs(i, m + 1, q); c[i] += 3

            # (B) 顺子（同花色连续3张）
            if m < k and i % 9 <= 6 and c[i] >= 1 and c[i+1] >= 1 and c[i+2] >= 1:
                c[i] -= 1; c[i+1] -= 1; c[i+2] -= 1
                dfs(i, m + 1, q)
                c[i] += 1; c[i+1] += 1; c[i+2] += 1

            # 等张候选：仅当组合槽未满时（不占将头槽）
            if m + q < k:
                # (D) 碰待（2同张→待刻）
                if ci >= 2:
                    c[i] -= 2; dfs(i, m, q + 1); c[i] += 2

                # (E) 坎张（gap=1）
                if i % 9 <= 7 and c[i+1] >= 1:
                    c[i] -= 1; c[i+1] -= 1
                    dfs(i, m, q + 1)
                    c[i] += 1; c[i+1] += 1

                # (F) 嵌张（gap=2）
                if i % 9 <= 6 and c[i+2] >= 1:
                    c[i] -= 1; c[i+2] -= 1
                    dfs(i, m, q + 1)
                    c[i] += 1; c[i+2] += 1

            # (G) 孤立
            c[i] -= 1; dfs(i, m, q); c[i] += 1

        dfs(0, 0, 0)
        return best_s[0]

    # 无将：直接计算最大组合得分
    best = 2 * k - _max_meld_score(cnt18[:])

    # 枚举每个将头位置（必须是 2 张同牌）
    for i in range(18):
        if cnt18[i] >= 2:
            cnt18[i] -= 2
            score = _max_meld_score(cnt18[:]) + 1   # +1 = 将头贡献
            cnt18[i] += 2
            best = min(best, 2 * k - score)

    return best


def _shanten_std(hand: list[int]) -> int:
    """
    纯标准形向听数（标准形 + 预摸形，不含五対形）。

    用于 Ch203-205 弃牌比较，确保 11 张手牌与弃牌后 10 张手牌
    使用同一套标准形口径进行比较，避免五対形造成口径不一致。
    """
    cnt = [0] * 19
    for c in hand:
        idx = _card_to_idx(c)
        if 0 <= idx <= 18:
            cnt[idx] += 1

    magic = cnt[18]
    reg   = cnt[:18]
    n     = sum(reg) + magic
    best  = 8

    if n >= 2 and (n - 2) % 3 == 0:
        k = (n - 2) // 3
        best = min(best, max(-1, _std_shanten(reg, k) - magic))

    if n >= 4 and (n - 1) % 3 == 0:
        k_pre = (n - 1) // 3
        best = min(best, max(-1, _std_shanten(reg, k_pre) - magic))

    return best


def _shanten(hand: list[int]) -> int:
    """
    计算手牌向听数。红中（0x35）作为万能替代任意牌。

    返回：
      -1 = 已胡（完整手牌）
       0 = 听牌（差1张胡）
       1-6 = 差 n 张
       7   = 6向听以上（上限）

    支持形式：
      • 标准形：k 组合 + 1 将（(n-2)%3==0 时有效）
      • 预摸形：等待摸1张的状态（(n-1)%3==0 时有效）
      • 五対形：5 对子（仅 n==10 时额外计算）

    万能牌处理：
      先对普通牌计算向听 s，然后 max(-1, s - magic_count)。
      原理：每张万能牌可直接充当还差的那1张，节省1次摸牌。
    """
    cnt = [0] * 19  # 0-17 = 万1-条9，18 = 红中
    for c in hand:
        idx = _card_to_idx(c)
        if 0 <= idx <= 18:
            cnt[idx] += 1

    magic = cnt[18]
    reg   = cnt[:18]
    n     = sum(reg) + magic
    best  = 8

    # 标准形（(n-2)%3==0，如 n=11,8,5,2...）
    if n >= 2 and (n - 2) % 3 == 0:
        k = (n - 2) // 3
        s = _std_shanten(reg, k)
        best = min(best, max(-1, s - magic))

    # 预摸形（(n-1)%3==0，如 n=10,7,4...）
    # 代表"正等待摸1张"的状态，用下一档 k 计算距听牌的步数
    if n >= 4 and (n - 1) % 3 == 0:
        k_pre = (n - 1) // 3
        s = _std_shanten(reg, k_pre)
        best = min(best, max(-1, s - magic))

    # 五対形（10张完整形，或 11张预摸形）
    if n == 10 or n == 11:
        pairs   = sum(c // 2 for c in reg)
        singles = sum(c % 2 for c in reg)
        used_m  = min(magic, singles)
        pairs  += used_m + (magic - used_m) // 2
        pairs   = min(5, pairs)
        if n == 10:
            # 10 张：直接评估
            best = min(best, max(-1, 4 - pairs))
        else:
            # 11 张预摸：假设弃掉 1 张孤立牌（不破坏任何对子）
            # 若有孤立牌可弃，对数保持不变；否则必须拆对子，对数 -1
            has_single_or_magic = (singles > 0) or (magic > 0)
            if has_single_or_magic:
                best = min(best, max(-1, 4 - pairs))
            else:
                best = min(best, max(-1, 4 - (pairs - 1)))

    return best


# 副露通道起始偏移
_SELF_MELD_CH = 161   # 自家副露 Ch161-180（4组×5 = 20通道）
_OPP_MELD_CH  = 181   # 对家副露 Ch181-200


def _make_channels(
    hand: list[int],
    opp_hand_count: int = 0,
    self_kawa: list[KawaEntry] | None = None,
    opp_kawa: list[KawaEntry] | None = None,
    self_melds: list[MeldEntry] | None = None,
    opp_melds: list[MeldEntry] | None = None,
) -> np.ndarray:
    """
    初始化 (CHAN_TOTAL, CARD_TYPES) = (220, 19) 零矩阵，填充结构性通道：

    Ch   0-3  : 自家手牌（stacked one-hot）
    Ch   4    : 对家手牌数量（全广播）= opp_hand_count / 10
    Ch   5-82 : 自家河牌（正序9槽 + 倒序28槽 + 4通道时序衰减）
    Ch  83-160: 对家河牌（同上）
    Ch 161-180: 自家副露（4组×5通道）
    Ch 181-200: 对家副露（4组×5通道）

    Ch 201-220（cans/状态类）由 snap() 内联填充。
    """
    chs = np.zeros((CHAN_TOTAL, CARD_TYPES), dtype=np.float32)
    chs[0:4] = _hand_to_ch0_3(hand)
    chs[4, :] = opp_hand_count / 10.0
    if self_kawa:
        _fill_kawa_channels(chs, self_kawa, _SELF_FWD_CH, _SELF_BWD_CH, _SELF_DECAY_CH)
    if opp_kawa:
        _fill_kawa_channels(chs, opp_kawa, _OPP_FWD_CH, _OPP_BWD_CH, _OPP_DECAY_CH)
    if self_melds:
        _fill_meld_channels(chs, self_melds, _SELF_MELD_CH)
    if opp_melds:
        _fill_meld_channels(chs, opp_melds, _OPP_MELD_CH)
    return chs


def _print_sample_channels(sample: "TrainSample") -> None:
    """打印一个训练样本的通道信息（跳过全零通道）。"""
    hand_str = " ".join(
        _card_str(c) for c in sample.hand_snap if c not in (0x00, 0xFF)
    )
    print(f"\n{'='*70}")
    print(f"  事件  : {sample.event}")
    print(f"  包索引: {sample.pkt_idx}")
    print(f"  手牌  : {hand_str if hand_str else '(空)'}")
    print(f"  张数  : {len(sample.hand_snap)}")
    print(f"  通道形状: {sample.channels.shape}")
    print(f"  ── 非零通道 ──")
    has_nonzero = False
    for ch_idx in range(CHAN_TOTAL):
        row = sample.channels[ch_idx]
        if not np.any(row):
            continue
        has_nonzero = True
        parts = [
            f"{row[i]:.2f}"
            for i in range(CARD_TYPES)
        ]
        print(f"    Ch{ch_idx:03d}: {' | '.join(parts)}")
    if not has_nonzero:
        print("    (全为零)")
    print("="*70)


@dataclass
class TrainSample:
    """单个训练数据点。"""
    event:     str           # 决策点事件描述
    channels:  np.ndarray    # (220, 19) float32 通道矩阵
    pkt_idx:   int           # 触发该样本的包在 replay.packets 中的索引
    hand_snap: list[int]     # 该时刻手牌的原始字节快照


def generalChairTrainData(replayData: VideoReplay, chairId: int) -> list[TrainSample]:
    """
    以 chairId 为第一视角，扫描回放数据，在以下关键决策点各生成一个训练样本：

    起手阶段（游戏开始 → 庄家首次出牌之间）：
      case_a  游戏开始，无报胡阶段，庄家直接出牌。
              触发条件：chairId 的 GAME_START 包 bao_chair == 0xFFFF
                        且 chairId == banker_chair。
      case_b  游戏开始，有报胡阶段，自己无报胡选项，所有人决策完毕。
              触发条件：BAO_HU_NOTIFY current_chair == 0xFFFF
                        且 chairId 从未出现在 last_chair 中。
      case_c  轮到自己进行报胡决策。
              触发条件：GAME_START bao_chair == chairId（第一个）；
                        或 BAO_HU_NOTIFY current_chair == chairId（后续）。
      case_d  自己选择了报胡，且有多张打出后仍听牌的牌，需要弃牌决策。
              触发条件：BAO_HU_NOTIFY current_chair==0xFFFF 且
                        自己在 last_chair 时 bao_hu_flag==True。
              （TODO: 完整判断需要 GameLogic 向听计算，当前仅标记决策点）
      case_e  自己有报胡选项但放弃，所有人决策完毕。
              触发条件：BAO_HU_NOTIFY current_chair==0xFFFF 且
                        自己在 last_chair 时 bao_hu_flag==False。

    常规阶段：
      case_f  发送扑克给自己（抓牌）。
              触发条件：SEND_CARD current_chair == chairId。
      case_g  有操作提示（碰/杠/胡等）轮到自己。
              触发条件：OPERATE_NOTIFY resume_chair == chairId。

    当前仅填充通道 0-3（自家手牌）；其余通道留待后续补充。

    参数：
        replayData — parseVideoReplay() 返回的回放数据
        chairId    — 以该椅子号为第一视角

    返回：
        TrainSample 列表
    """
    samples: list[TrainSample] = []

    # ── 手牌状态追踪 ────────────────────────────────────────────
    hand: list[int] = []              # chairId 当前手牌（原始字节，动态更新）
    hand_counts: dict[int, int] = {}  # 所有椅子的当前手牌数（用于通道 4）

    # 对家椅号：2人局=另一位；4人局=(chairId+2)%4
    player_count  = replayData.user_count
    dui_jia_chair = (chairId + max(1, player_count // 2)) % player_count

    # ── 预扫描：提前收集所有玩家的起手手牌数 ──────────────────────
    # 目的：GAME_START 包按椅子顺序下发，自己的包先到就先生成样本；
    # 若不预扫描，生成 case_c/a 时对家手牌数尚未登记，Ch4 会错误为 0。
    for _pre_pkt in replayData.packets:
        if _pre_pkt.main_cmd != MDM_GF_GAME or _pre_pkt.sub_cmd != 100:
            continue
        _pre_buf = _Buf(_pre_pkt.payload)
        _pre_buf.read_int()    # sice_count
        _pre_buf.read_word()   # banker_chair
        _pre_buf.read_word()   # current_chair
        _pre_buf.read_word()   # bao_chair
        _pre_buf.read_byte()   # action_mask
        _pre_buf.read_byte()   # magic_card
        if _pre_buf.remaining() >= MAX_COUNT:
            _pre_hand = [_pre_buf.read_byte() for _ in range(MAX_COUNT)]
            hand_counts[_pre_pkt.chair_id] = len(
                [c for c in _pre_hand if c not in (0x00, 0xFF)]
            )

    # ── 河牌状态追踪 ────────────────────────────────────────────
    kawa: dict[int, list[KawaEntry]] = {}   # 各椅子弃牌序列
    pending_gang: dict[int, int] = {}        # 待写入下次弃牌 Ch0 的杠牌字节

    # ── 副露状态追踪 ────────────────────────────────────────────
    melds: dict[int, list[MeldEntry]] = {}   # 各椅子副露列表

    # ── 报胡状态 ────────────────────────────────────────────────
    bao_hu_flags: dict[int, bool] = {}       # 各椅子是否已报胡

    # ── 游戏开始阶段状态 ────────────────────────────────────────
    game_started       = False
    banker_chair       = _INVALID_CHAIR
    bao_chair_first    = _INVALID_CHAIR
    bao_phase_active   = False

    # 报胡阶段中 chairId 的状态
    self_bao_hui_turn  = False
    self_chose_bao_hu  = False

    def snap(
        event: str,
        idx: int,
        action_mask: int = 0,          # 当前帧的动作掩码（来自 SEND_CARD / OPERATE_NOTIFY）
        trigger_card: int = 0,         # 触发本决策的牌（对家打出，用于 Ch201）
        is_discard: bool = False,      # 是否处于弃牌决策阶段（控制 Ch202）
        bao_hu_dec: bool = False,      # 是否正在进行报胡决策（控制 Ch210/211）
    ) -> TrainSample:
        """构造当前状态的训练样本，填充全部已实现通道。"""
        opp_cnt = hand_counts.get(dui_jia_chair, 0)
        chs = _make_channels(
            hand, opp_cnt,
            kawa.get(chairId),
            kawa.get(dui_jia_chair),
            melds.get(chairId),
            melds.get(dui_jia_chair),
        )

        # ── Ch 201: 对家刚打出的牌（触发本决策的牌）──────────────
        if trigger_card:
            chs[201] = _one_hot_card(trigger_card)

        # ── Ch 202: 自家合法可打出的牌（弃牌阶段才有值）──────────
        # 红中规则：手里有其他普通牌时红中不可打出；手里全是红中时可以打出
        _HZ_IDX = 18  # 红中在通道坐标系中的索引
        _all_hz = all(c == 0x35 for c in hand)  # 手牌是否全为红中
        if is_discard:
            for c in hand:
                ci = _card_to_idx(c)
                if 0 <= ci < CARD_TYPES:
                    if ci == _HZ_IDX and not _all_hz:
                        continue  # 有其他牌时，红中不合法
                    chs[202, ci] = 1.0

        # ── Ch 203-205: 弃牌候选向听分析（仅弃牌阶段）──────────
        # ── Ch 203-204: 弃牌候选向听分析（仅弃牌阶段）──────────────
        # 基线：完整 _shanten（含五対预摸形），取最优策略
        # 数学性质：弃牌只能维持或增大向听数（单调不减），
        #           因此 sh_after < sh_now 不可能发生，Ch204 已删除。
        # 遵守红中弃牌规则：红中不可打出时跳过分析。
        sh_now = _shanten(hand)
        if is_discard and hand:
            seen_cidx: set[int] = set()
            for card in hand:
                ci = _card_to_idx(card)
                if ci < 0 or ci in seen_cidx:
                    continue
                if ci == _HZ_IDX and not _all_hz:
                    continue  # 红中不合法，跳过
                seen_cidx.add(ci)
                temp_hand = list(hand)
                temp_hand.remove(card)
                sh_after = _shanten(temp_hand)
                if sh_after == sh_now:
                    chs[203, ci] = 1.0        # 打出后向听数不变
                if sh_after == 0:
                    chs[204, ci] = 1.0        # 打出后直接听牌（原 Ch205）

        # 玩家已报胡后，所有弃牌阶段的合法出牌（Ch202）都收窄为"打出后仍听牌"的牌。
        # 报胡规则：报胡后每次弃牌必须维持听牌状态，否则报胡失去意义。
        if is_discard and bao_hu_flags.get(chairId, False):
            chs[202] = chs[203].copy()

        # ── Ch 205: 可以碰 ────────────────────────────────────────
        if action_mask & _WIK_PENG:
            chs[205, :] = 1.0
        # ── Ch 206: 可以杠 ────────────────────────────────────────
        if action_mask & (_WIK_GANG | _WIK_JIA_GANG):
            chs[206, :] = 1.0
        # ── Ch 207: 可以胡（吃胡/点炮/自摸均映射到此）────────────
        if action_mask & (0x40 | 0x80):   # WIK_CHI_HU | WIK_DIAN_PAO
            chs[207, :] = 1.0
        # ── Ch 208: 可以请胡 ──────────────────────────────────────
        if action_mask & 0x20:             # WIK_QING_HU
            chs[208, :] = 1.0
        # ── Ch 209: 可以报胡 ──────────────────────────────────────
        if bao_hu_dec or (action_mask & _WIK_BAO_HU):
            chs[209, :] = 1.0
        # ── Ch 210: 可以过（有其他操作选项时均可放弃）────────────
        if bao_hu_dec or trigger_card or (action_mask & (_WIK_PENG | _WIK_GANG | _WIK_JIA_GANG | 0x40 | 0x80)):
            chs[210, :] = 1.0

        # ── Ch 211: 自己已报胡 ────────────────────────────────────
        if bao_hu_flags.get(chairId, False):
            chs[211, :] = 1.0
        # ── Ch 212: 对家已报胡 ────────────────────────────────────
        if bao_hu_flags.get(dui_jia_chair, False):
            chs[212, :] = 1.0

        # ── Ch 213-218: 向听数 one-hot（全广播）────────────────────
        # 使用完整 _shanten（含五対形）以准确评估当前手牌质量
        if 1 <= sh_now <= 5:
            chs[212 + sh_now, :] = 1.0   # Ch213=1向听 … Ch217=5向听
        elif sh_now >= 6:
            chs[218, :] = 1.0            # Ch218=6向听以上

        # ── Ch 219: 红中数量归一化（0张=0, 1张=0.25, …, 4张=1.0）
        hz_cnt = sum(1 for c in hand if c == 0x35)
        chs[219, :] = hz_cnt / 4.0

        return TrainSample(event=event, channels=chs, pkt_idx=idx, hand_snap=list(hand))

    for i, pkt in enumerate(replayData.packets):
        if pkt.main_cmd != MDM_GF_GAME:
            continue
        sub = pkt.sub_cmd

        # ══════════════════════════════════════════════════════
        # 游戏开始 (sub=100)  —— 每个玩家各一包，pkt.chair_id 指示该包属于谁
        # ══════════════════════════════════════════════════════
        if sub == 100:
            buf = _Buf(pkt.payload)
            buf.read_int()                              # sice_count（骰子）
            banker_chair_now = buf.read_word()          # 庄家椅子
            _current         = buf.read_word()          # 当前行动椅子（通常=庄家）
            bao_chair_now    = buf.read_word()          # 首个报胡决策玩家
            _action_mask     = buf.read_byte()          # 此包所属玩家的动作掩码
            _magic           = buf.read_byte()          # 鬼牌
            # 手牌数据只在目标椅子的包中存在（至少需要 MAX_COUNT 字节）
            if buf.remaining() < MAX_COUNT:
                continue
            raw_hand         = [buf.read_byte() for _ in range(MAX_COUNT)]

            valid_hand = [c for c in raw_hand if c not in (0x00, 0xFF)]

            # 更新所有玩家的起手手牌数
            hand_counts[pkt.chair_id] = len(valid_hand)

            # 仅 chairId 的包负责初始化自家手牌和全局状态
            if pkt.chair_id == chairId:
                hand            = valid_hand
                banker_chair    = banker_chair_now
                bao_chair_first = bao_chair_now
                game_started    = True

                if bao_chair_first == _INVALID_CHAIR:
                    # ── Case a: 无报胡阶段，庄家直接出牌 ──────────────
                    if chairId == banker_chair:
                        samples.append(snap(
                            "case_a:无报胡阶段_庄家直接出牌", i,
                            is_discard=True,
                        ))
                else:
                    bao_phase_active = True
                    # ── Case c（第一个）: 首个报胡决策轮到 chairId ────
                    if bao_chair_first == chairId:
                        samples.append(snap(
                            "case_c:报胡决策(首位)", i,
                            bao_hu_dec=True,
                        ))

        # ══════════════════════════════════════════════════════
        # 报胡提醒 (sub=115)
        # ══════════════════════════════════════════════════════
        elif sub == 115 and game_started and bao_phase_active:
            buf = _Buf(pkt.payload)
            current_bao = buf.read_word()   # 下一个需要决策的玩家（0xFFFF = 全部结束）
            last_bao    = buf.read_word()   # 刚完成决策的玩家
            bao_flag    = buf.read_byte()   # 1=报胡 0=不报
            _card       = buf.read_byte()   # 相关牌面

            # 记录 chairId 的报胡决策；更新已报胡标志
            if last_bao == chairId:
                self_bao_hui_turn = True
                self_chose_bao_hu = bool(bao_flag)
            if bao_flag and last_bao != _INVALID_CHAIR:
                bao_hu_flags[last_bao] = True

            if current_bao == chairId:
                # ── Case c（后续）: 报胡决策轮到 chairId ──────────
                samples.append(snap("case_c:报胡决策", i, bao_hu_dec=True))

            elif current_bao == _INVALID_CHAIR:
                # ── 报胡阶段全部结束，庄家即将出牌 ─────────────────
                if self_bao_hui_turn:
                    if self_chose_bao_hu:
                        # ── Case d: 自己选择了报胡，需要弃牌 ─────────
                        samples.append(snap(
                            "case_d:已报胡_弃牌决策", i,
                            is_discard=True,
                        ))
                    else:
                        # ── Case e: 有报胡选项但放弃 ──────────────────
                        samples.append(snap(
                            "case_e:放弃报胡_全部决策完", i,
                            is_discard=True,
                        ))
                else:
                    # ── Case b: 自己无报胡选项 ─────────────────────────
                    samples.append(snap(
                        "case_b:无报胡选项_全部决策完", i,
                        is_discard=True,
                    ))

        # ══════════════════════════════════════════════════════
        # 发送扑克 (sub=102) —— 抓牌
        # ══════════════════════════════════════════════════════
        elif sub == 102 and game_started:
            buf = _Buf(pkt.payload)
            card          = buf.read_byte()
            act_mask_b    = buf.read_byte()
            current_chair = buf.read_word()

            # 所有玩家手牌数 +1
            if card not in (0x00, 0xFF):
                hand_counts[current_chair] = hand_counts.get(current_chair, 0) + 1

            if current_chair == chairId:
                # 先将牌加入手牌，再记录样本（样本反映加牌后的状态）
                if card not in (0x00, 0xFF):
                    hand.append(card)
                # ── Case f: 摸牌 ─────────────────────────────────────
                # 报胡后出牌由系统托管：只有可胡或可杠时才生成决策样本
                _HU_GANG_MASK = 0x04 | 0x08 | 0x40 | 0x80
                if bao_hu_flags.get(chairId, False):
                    if act_mask_b & _HU_GANG_MASK:
                        samples.append(snap(
                            "case_f:摸牌", i,
                            action_mask=act_mask_b,
                            is_discard=False,
                        ))
                    # 否则系统托管弃牌，不需要决策，跳过
                else:
                    samples.append(snap(
                        "case_f:摸牌", i,
                        action_mask=act_mask_b,
                        is_discard=True,
                    ))

        # ══════════════════════════════════════════════════════
        # 用户出牌 (sub=101) —— 更新手牌 + 创建河牌条目
        # ══════════════════════════════════════════════════════
        elif sub == 101 and game_started:
            buf = _Buf(pkt.payload)
            _trustee  = buf.read_byte()
            out_chair = buf.read_word()
            card      = buf.read_byte()

            if card not in (0x00, 0xFF):
                # 所有玩家手牌数 -1
                hand_counts[out_chair] = max(0, hand_counts.get(out_chair, 0) - 1)

                # 创建河牌条目（gang_card 取自本巡的杠，默认无杠）
                gang_c = pending_gang.pop(out_chair, 0)
                if out_chair not in kawa:
                    kawa[out_chair] = []
                kawa[out_chair].append((gang_c, card, False))

                # 更新 chairId 自家手牌
                if out_chair == chairId:
                    try:
                        hand.remove(card)
                    except ValueError:
                        pass  # 异常情况（不应发生）

        # ══════════════════════════════════════════════════════
        # 操作提示 (sub=104) —— 碰/杠/胡等决策
        # ══════════════════════════════════════════════════════
        elif sub == 104 and game_started:
            buf = _Buf(pkt.payload)
            resume_chair  = buf.read_word()
            act_mask_b    = buf.read_byte()
            act_card_b    = buf.read_byte()

            if resume_chair == chairId:
                # Ch201 仅用于"对家打牌后触发"的操作（碰/直杠/吃胡/点炮）。
                # 报胡（WIK_BAO_HU）：action_card 是报叫的牌，是自己的牌，不设 Ch201。
                # 加杠（WIK_JIA_GANG）：action_card 是自己摸的牌，扩展已有碰副露，不设 Ch201。
                _OPP_CARD_MASK = 0x02 | 0x04 | 0x40 | 0x80  # 碰|直杠|吃胡|点炮
                trigger = act_card_b if (act_mask_b & _OPP_CARD_MASK) else 0

                # 报胡 OPERATE_NOTIFY 是弃牌选择阶段：
                # 玩家需要从手牌中选一张打出（只有打出后仍听牌的才合法），
                # 因此设 is_discard=True 以填充 Ch202/203/204 弃牌候选通道。
                is_bao_hu = bool(act_mask_b & _WIK_BAO_HU)

                # OPERATE_NOTIFY WIK_BAO_HU 是服务器让玩家选择报叫牌的阶段，
                # 意味着该玩家已经提交了"报胡"意向（yes），在 snap 前标记已报胡。
                if is_bao_hu:
                    bao_hu_flags[chairId] = True

                # ── Case g: 操作提示轮到 chairId ─────────────────────
                samples.append(snap(
                    "case_g:操作提示", i,
                    action_mask=act_mask_b,
                    trigger_card=trigger,
                    is_discard=is_bao_hu,
                ))

        # ══════════════════════════════════════════════════════
        # 操作结果 (sub=105) —— 碰/杠：更新手牌 + 河牌 taken + pending_gang
        # ══════════════════════════════════════════════════════
        elif sub == 105 and game_started:
            buf = _Buf(pkt.payload)
            operate_chair = buf.read_word()
            provide_chair = buf.read_word()
            operate_code  = buf.read_byte()
            operate_cards = [buf.read_byte() for _ in range(3)]

            valid_op    = [c for c in operate_cards if c not in (0x00, 0xFF)]
            gang_card_b = valid_op[0] if valid_op else 0  # 副露牌（各张同类型）

            def _mark_last_taken(chair: int) -> None:
                """将 chair 的最后一条河牌标记为"被拿走"。"""
                river = kawa.get(chair)
                if river:
                    g, d, _ = river[-1]
                    river[-1] = (g, d, True)

            def _remove_from_hand(n: int) -> None:
                """从 chairId 手牌移除 n 张 valid_op 中的牌。"""
                removed = 0
                for c in valid_op:
                    if removed >= n:
                        break
                    try:
                        hand.remove(c)
                        removed += 1
                    except ValueError:
                        pass

            def _ensure_melds(chair: int) -> list[MeldEntry]:
                if chair not in melds:
                    melds[chair] = []
                return melds[chair]

            # ── 碰 ──────────────────────────────────────────────────
            if operate_code & _WIK_PENG:
                hand_counts[operate_chair] = max(0, hand_counts.get(operate_chair, 0) - 2)
                _mark_last_taken(provide_chair)
                _ensure_melds(operate_chair).append(
                    (gang_card_b, 3, _MELD_PENG)
                )
                if operate_chair == chairId:
                    _remove_from_hand(2)

            # ── 直杠（他人打出的牌被杠走）─────────────────────────
            elif operate_code & _WIK_GANG and provide_chair != operate_chair:
                hand_counts[operate_chair] = max(0, hand_counts.get(operate_chair, 0) - 3)
                _mark_last_taken(provide_chair)
                pending_gang[operate_chair] = gang_card_b
                _ensure_melds(operate_chair).append(
                    (gang_card_b, 4, _MELD_ZHIGANG)
                )
                if operate_chair == chairId:
                    _remove_from_hand(3)

            # ── 暗杠（4张全在手里）─────────────────────────────────
            elif operate_code & _WIK_GANG and provide_chair == operate_chair:
                hand_counts[operate_chair] = max(0, hand_counts.get(operate_chair, 0) - 4)
                pending_gang[operate_chair] = gang_card_b
                _ensure_melds(operate_chair).append(
                    (gang_card_b, 4, _MELD_ANGANG)
                )
                if operate_chair == chairId:
                    _remove_from_hand(4)

            # ── 加杠（将已有碰副露升级为杠）────────────────────────
            elif operate_code & _WIK_JIA_GANG:
                hand_counts[operate_chair] = max(0, hand_counts.get(operate_chair, 0) - 1)
                pending_gang[operate_chair] = gang_card_b
                # 将已有碰副露升级为加杠副露
                chair_melds = _ensure_melds(operate_chair)
                upgraded = False
                for mi, (mc, mn, mt) in enumerate(chair_melds):
                    if mc == gang_card_b and mt == _MELD_PENG:
                        chair_melds[mi] = (mc, 4, _MELD_JIAGANG)
                        upgraded = True
                        break
                if not upgraded:
                    chair_melds.append((gang_card_b, 4, _MELD_JIAGANG))
                if operate_chair == chairId:
                    _remove_from_hand(1)

    return samples


def generalTrainDataByVideo(replay: VideoReplay) -> list[TrainSample]:
    all_samples: list[TrainSample] = []

    for chair_id in range(replay.user_count):
        chair_samples = generalChairTrainData(replay, chair_id)
        all_samples.extend(chair_samples)

    return all_samples


# ──────────────────────────────────────────────
# 工具：查找含报胡操作的回放文件
# ──────────────────────────────────────────────

def findSpecialGangReplays(dir_path: str | Path) -> dict[str, list[str]]:
    """
    扫描目录下所有 .video 回放文件，查找含有「面下杠/刮风/下雨」的文件。

    判定标准：
      · 面下杠（加杠）: OPERATE_RESULT (sub=105) 中 operate_code & WIK_JIA_GANG (0x08)
      · 刮风（直杠）  : GAME_ACTION_NOTIFY (sub=112) 中 action_type == 0x01
      · 下雨（暗杠）  : GAME_ACTION_NOTIFY (sub=112) 中 action_type == 0x02

    参数：
        dir_path — 回放文件目录（递归搜索所有 .video 文件）

    返回：
        {"面下杠": [...], "刮风": [...], "下雨": [...]}
        每个键对应包含该操作的文件路径列表（三者可重叠）
    """
    dir_path = Path(dir_path)
    all_videos = sorted(dir_path.rglob("*.video"))
    total = len(all_videos)
    print(f"扫描目录：{dir_path}")
    print(f"共发现 {total} 个 .video 文件，开始检测面下杠/刮风/下雨...")
    print("-" * 60)

    found: dict[str, list[str]] = {"面下杠": [], "刮风": [], "下雨": []}

    for idx, fp in enumerate(all_videos, 1):
        try:
            replay = parseVideoReplay(fp)
            flags = {"面下杠": False, "刮风": False, "下雨": False}
            for pkt in replay.packets:
                if pkt.main_cmd != MDM_GF_GAME:
                    continue
                sub = pkt.sub_cmd
                payload = pkt.payload
                # 面下杠（加杠）: OPERATE_RESULT (105)，operate_code & WIK_JIA_GANG(0x08)
                # payload 布局：word operate_chair(2) + word provide_chair(2) + byte operate_code(1)
                if sub == 105 and len(payload) >= 5:
                    if payload[4] & 0x08:
                        flags["面下杠"] = True
                # 刮风/下雨: GAME_ACTION_NOTIFY (112)，payload[0]=action_type
                elif sub == 112 and len(payload) >= 1:
                    atype = payload[0]
                    if atype == 0x02:
                        flags["下雨"] = True
                if all(flags.values()):
                    break  # 三种都找到了，提前退出

            for key, hit in flags.items():
                if hit:
                    found[key].append(str(fp))

        except Exception as e:
            print(f"  [解析失败] {fp}: {e}", file=sys.stderr)

        if idx % 500 == 0:
            print(f"  进度：{idx}/{total}  面下杠={len(found['面下杠'])}  "
                  f"刮风={len(found['刮风'])}  下雨={len(found['下雨'])}")

    print("-" * 60)
    print(f"扫描完成！")
    print(f"  含面下杠（加杠）：{len(found['面下杠'])} 个文件")
    print(f"  含刮风（直杠）  ：{len(found['刮风'])} 个文件")
    print(f"  含下雨（暗杠）  ：{len(found['下雨'])} 个文件")
    for key, paths in found.items():
        if paths:
            print(f"\n── {key} ──")
            for p in paths:
                print(p)
    return found


def findBaoHuReplays(dir_path: str | Path) -> list[str]:
    """
    扫描目录下所有 .video 回放文件，打印并返回含有报胡操作的文件路径。

    "有报胡操作"的判定标准：
        回放中存在至少一个 BAO_HU_NOTIFY (sub_cmd=115) 数据包，
        且该包的 bao_hu_flag == 1（即有玩家实际选择了报胡，而非仅拒绝）。

    参数：
        dir_path — 回放文件目录（递归搜索所有 .video 文件）

    返回：
        匹配的文件路径字符串列表
    """
    dir_path = Path(dir_path)
    all_videos = sorted(dir_path.rglob("*.video"))
    total = len(all_videos)
    print(f"扫描目录：{dir_path}")
    print(f"共发现 {total} 个 .video 文件，开始检测报胡操作...")
    print("-" * 60)

    found: list[str] = []

    for idx, fp in enumerate(all_videos, 1):
        try:
            replay = parseVideoReplay(fp)
            has_bao_hu = False
            for pkt in replay.packets:
                if pkt.main_cmd != MDM_GF_GAME or pkt.sub_cmd != 115:
                    continue
                # 快速从原始 payload 读取 bao_hu_flag（偏移 4 字节处）
                # 协议：word current_chair(2) + word last_chair(2) + byte bao_hu_flag(1)
                if len(pkt.payload) >= 5 and pkt.payload[4] == 1:
                    has_bao_hu = True
                    break
            if has_bao_hu:
                found.append(str(fp))
                print(str(fp))
        except Exception as e:
            print(f"  [解析失败] {fp}: {e}", file=sys.stderr)

        if idx % 500 == 0:
            print(f"  进度：{idx}/{total}，已找到 {len(found)} 个含报胡文件...")

    print("-" * 60)
    print(f"扫描完成！共 {len(found)} 个文件含有报胡操作（bao_hu_flag=1）。")
    return found


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
def _extract_reward(replay: VideoReplay, chair_id: int) -> float:
    """
    从 GAME_END(108) 和 RULE_SETTING(113) 包提取归一化奖励。
    reward = game_score[chair_id] / (REWARD_NORM * cell_score)
    """
    cell_score = 1.0
    game_score = 0.0

    for pkt in replay.packets:
        if pkt.main_cmd != MDM_GF_GAME or not pkt.payload:
            continue
        sub = pkt.sub_cmd

        if sub == 113:   # RULE_SETTING
            try:
                cs = float(_Buf(pkt.payload).read_int64())
                if cs > 0:
                    cell_score = cs
            except Exception:
                pass

        elif sub == 108:   # GAME_END
            try:
                buf = _Buf(pkt.payload)
                cs  = float(buf.read_int64())   # cell_score
                if cs > 0:
                    cell_score = cs
                [buf.read_word()  for _ in range(4)]   # provide_chairs
                buf.read_word()                         # escape_chair
                buf.read_byte()                         # escape_fan
                [buf.read_byte()  for _ in range(4)]   # geng_count
                [buf.read_byte()  for _ in range(4)]   # chihu_order
                [buf.read_dword() for _ in range(4)]   # chihu_kind
                [[buf.read_dword(), buf.read_dword()] for _ in range(4)]  # chihu_right
                scores = [buf.read_int64() for _ in range(4)]
                if 0 <= chair_id < len(scores):
                    game_score = float(scores[chair_id])
            except Exception:
                pass
            break

    norm = 64.0 * cell_score
    return game_score / norm if norm != 0 else 0.0

if __name__ == "__main__":
    # 查找含报胡操作的回放文件
   # findBaoHuReplays("/Users/kebiaoy/Documents/MjTrainData")
    #findSpecialGangReplays("/Users/kebiaoy/Documents/MjTrainData")
    # 报胡回放
    # file_path="/Users/kebiaoy/Documents/MjTrainData/61263_20260618/09856202606188205401.video"
    file_path = "E:\Train\\61464_20260629\\09856202606290008202.video"
    testParseVideoReplay(file_path)
    replay = parseVideoReplay(file_path)
    print(_extract_reward(replay,0))
    # 检测活跃茶馆
    # checkActiveTea(61000,600,1000)
    #downloadTeaReplay(61464)

