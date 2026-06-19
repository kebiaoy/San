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
    return [item["RecordID"] for item in lst if item.get("RecordID")]


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

    base_dir = Path(__file__).parent
    save_dir = base_dir / "res" / "battleReplay" / f"{group_id}_{date_str}"
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
                 if item.get("RecordID")]
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


def testParseVideoReplay():
    import datetime as _dt
    from collections import Counter

    # 使用一个 KindID=150（红中断勾卡）的文件测试
    TEST_FILE = "res/battleReplay/61263_20260618/09856202606180269801.video"
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
                print(f"  [{i:3d}] {cmd_label}  庄={d.get('banker_chair')}  "
                      f"鬼牌={d.get('magic_card')}  "
                      f"手牌={d.get('hand_cards')}")
            elif p.sub_cmd == 101:  # 出牌
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('out_chair')}  "
                      f"牌={d.get('card')}  托管={d.get('trustee_out')}")
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
                print(f"  [{i:3d}] {cmd_label}  chair={d.get('current_chair')}  "
                      f"牌={d.get('card')}  flag={d.get('bao_hu_flag')}")
            else:
                print(f"  [{i:3d}] {cmd_label}  {d}")

    print("=" * 60)

# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    testParseVideoReplay()
