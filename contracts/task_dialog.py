"""Durable question and reply payloads for a single offline task stage."""

WAITING_INPUT = 'waiting_input'
UPSTREAM_BLOCKED = '上游依赖未完成；待修复配置/输入后重试。'
MAX_REPLY_LENGTH = 3000


def reply_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REPLY_LENGTH:
        raise ValueError(f'请输入 1–{MAX_REPLY_LENGTH} 字的回复')
    return value.strip()
