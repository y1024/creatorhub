# -*- coding: utf-8 -*-
"""抖音网页版私信(IM)protobuf 解析。

网页版会话列表**没有**公开 JSON 接口:IM SDK 通过 `imapi.douyin.com/v1/message/get_message_by_init`
(cmd 2043) 一次性下发全部会话 + 每个会话的最后一条消息,响应体是 protobuf 二进制。
本模块**只**按已标定的字段号定向解析(不做类型推断) —— 通用 protobuf 猜测器会把
base64 串/conversation_id 偶发误判成子消息,必须锁定 schema 才稳定。

字段布局(实测标定,与抖音 IM 的 Request/Response.proto 对得上):
  envelope: {1:cmd, 2:seq, 4:"OK", 5:inbox_type, 6:body, 13:自己的 uid}
  body:     {2043: get_message_by_init_body}
  2043:     {1: [会话 wrapper ...]}          # repeated
  会话 wrapper:
    .1 = 会话核心(GetConversationInfoV2Response): {1:conv_id, 2:short_id, 3:type, 4:ticket}
    .2 = 最后一条消息(MessageBody): {6:msg_type, 7:sender_uid, 8:content_json, 14:sender_sec_uid}
  content_json 里文本在 .text;非文本消息(卡片/表情/转账)按 aweType 给占位。
"""
import json
from typing import Dict, List, Optional


def _read_varint(b: bytes, i: int):
    s = r = 0
    while True:
        x = b[i]; i += 1
        r |= (x & 0x7f) << s
        if not (x & 0x80):
            return r, i
        s += 7


def _get_fields(b: bytes) -> Dict[int, list]:
    """单层解析 protobuf -> {field_no: [values]}。len-delimited 一律保留原始 bytes,
    绝不自动递归(避免字符串被误判成子消息)。上层按需对已知消息字段再调本函数。"""
    out: Dict[int, list] = {}
    i, end = 0, len(b)
    while i < end:
        try:
            tag, i = _read_varint(b, i)
        except IndexError:
            break
        fn, wt = tag >> 3, tag & 7
        if fn == 0:
            break
        try:
            if wt == 0:
                v, i = _read_varint(b, i)
            elif wt == 1:
                v = b[i:i+8]; i += 8
            elif wt == 5:
                v = b[i:i+4]; i += 4
            elif wt == 2:
                ln, i = _read_varint(b, i)
                v = b[i:i+ln]; i += ln
            else:
                break
        except IndexError:
            break
        out.setdefault(fn, []).append(v)
    return out


def _first(d: Dict[int, list], k: int, default=None):
    v = d.get(k)
    return v[0] if v else default


def _s(v) -> str:
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except Exception:
            return ""
    return "" if v is None else str(v)


# 非文本消息按 aweType 给占位(会话列表预览用)
_AWE_LABEL = {507: "[表情]", 5: "[表情]", 11048: "[小程序卡片]",
              100157: "[系统通知]", 2702: "[小程序]"}


def _preview_text(content: bytes) -> str:
    """从消息 content(JSON 串)取会话列表预览文本。"""
    if not isinstance(content, bytes) or not content:
        return ""
    try:
        obj = json.loads(content.decode("utf-8"))
    except Exception:
        return ""
    if not isinstance(obj, dict):
        return ""
    if obj.get("text"):
        return str(obj["text"])
    if obj.get("trans_type") is not None:      # 转账
        return "[转账] " + str(obj.get("title") or "")
    lbl = _AWE_LABEL.get(obj.get("aweType"))
    if lbl:
        return lbl
    if obj.get("push_detail"):
        return str(obj["push_detail"])
    if obj.get("description"):
        return str(obj["description"])
    return ""


def peer_uid_from_conv_id(conv_id: str, self_uid: str) -> str:
    """单聊 conv_id 形如 `0:{type}:{uidA}:{uidB}`,两 uid 顺序不固定,
    对端 = 不是 self_uid 的那个。self_uid 未知则返回空(不猜)。"""
    if not conv_id or not self_uid or ":" not in conv_id:
        return ""
    uids = [x for x in conv_id.split(":") if x.isdigit() and len(x) >= 6]
    if self_uid not in uids:
        return ""
    return next((u for u in uids if u != self_uid), "")


def parse_conversations(raw: bytes) -> List[dict]:
    """解 get_message_by_init 响应体 -> 会话列表(仅单聊 type==1)。
    每项: {conv_id, conv_short_id, peer_uid, peer_sec_uid, ticket,
           last_text, last_msg_type, last_sender_uid, self_uid}。
    昵称/头像不在此包里,由上层用 user/info JSON 水合。"""
    if not raw:
        return []
    env = _get_fields(raw)
    self_uid = _s(_first(env, 13))
    body_raw = _first(env, 6)
    if not isinstance(body_raw, bytes):
        return []
    body = _get_fields(body_raw)
    b2043_raw = _first(body, 2043)
    if not isinstance(b2043_raw, bytes):
        return []
    b2043 = _get_fields(b2043_raw)

    out: List[dict] = []
    for cb in b2043.get(1, []):
        if not isinstance(cb, bytes):
            continue
        conv = _get_fields(cb)
        core = _get_fields(_first(conv, 1, b"") or b"")
        conv_id = _s(_first(core, 1, b""))
        if not conv_id:
            continue
        ctype = _first(core, 3)
        if ctype != 1:                     # 只要单聊,群聊/系统箱先跳过
            continue
        peer_uid = peer_uid_from_conv_id(conv_id, self_uid)
        if not peer_uid:
            continue
        msg = _get_fields(_first(conv, 2, b"") or b"")
        content = _first(msg, 8, b"")
        out.append({
            "conv_id": conv_id,
            "conv_short_id": _s(_first(core, 2)),
            "peer_uid": peer_uid,
            "peer_sec_uid": _s(_first(msg, 14, b"")) if _first(msg, 7) != int(self_uid or 0) else "",
            "ticket": _s(_first(core, 4, b"")),
            "last_text": _preview_text(content if isinstance(content, bytes) else b""),
            "last_msg_type": _first(msg, 6),
            "last_sender_uid": _s(_first(msg, 7)),
            "self_uid": self_uid,
        })
    return out
