#!/usr/bin/env python3
"""
Claude Code PreToolUse Hook
自动放行安全的 cd + 只读命令组合。
"""
import json
import sys
import re

# 只读命令白名单（匹配命令的开头）。
#
# 只收「无论带什么参数都不会写盘、也不会执行任意代码」的命令。以下几条
# **不得**加入：按前缀匹配时它们等于放行写操作——
#   sed   `sed -i` 原地改文件
#   awk   `awk 'BEGIN{system("rm -rf ...")}'` 任意命令执行
#   find  `find . -delete` / `find . -exec rm -rf {} ;`
READONLY_PREFIXES = [
    'cat', 'dir', 'echo', 'grep', 'head', 'ls', 'more', 'pwd',
    'tail', 'type', 'wc', 'which',
    'git status', 'git diff', 'git log', 'git show', 'git branch',
]

# 命令段里出现任一元字符就不再信任其前缀：它们能把只读命令变成写文件
# （> / <）或任意执行（|、反引号、$(）。`&` 另外挡住 `ls & rm -rf x` 这类
# 不经过分隔符拼接的写法。`grep "a|b" f` 会被误判，假阳性可接受——
# 代价只是回到正常权限询问，宁可不放行。
SHELL_METACHARS = ('>', '<', '|', '`', '$(', '&')

# `git branch` 只有纯列举形态才是只读的：位置参数表示「新建分支」，
# 下列参数会删除 / 移动 / 复制分支或改上游。
GIT_BRANCH_MUTATING_FLAGS = {
    '-d', '-D', '--delete',
    '-m', '-M', '--move',
    '-c', '-C', '--copy',
    '-u', '--set-upstream-to', '--unset-upstream',
    '--edit-description',
}


def has_shell_metachar(segment: str) -> bool:
    """命令段里是否出现能把只读命令变成写操作 / 任意执行的元字符"""
    return any(ch in segment for ch in SHELL_METACHARS)


def is_readonly_git_branch(segment: str) -> bool:
    """`git branch` 只认纯列举：不接受位置参数，也不接受会改分支的参数"""
    for token in segment.split()[2:]:  # 去掉 `git` `branch`
        if not token.startswith('-'):
            # 位置参数 = 新建分支，不是只读
            return False
        if token in GIT_BRANCH_MUTATING_FLAGS:
            return False
    return True


def is_readonly_segment(cmd: str) -> bool:
    """判断单段命令是否为只读"""
    cmd = cmd.strip()
    if not cmd:
        return True
    if has_shell_metachar(cmd):
        return False
    # cd 到任意路径视为安全（只读操作）
    if cmd.startswith('cd '):
        return True
    # 检查是否以只读命令开头
    for prefix in READONLY_PREFIXES:
        if cmd == prefix or cmd.startswith(prefix + ' '):
            if prefix == 'git branch':
                return is_readonly_git_branch(cmd)
            return True
    return False


def main():
    try:
        input_data = json.load(sys.stdin)
    except Exception:
        # 解析失败，放行让 Claude Code 自己判断
        print(json.dumps({"continue": True}))
        return

    if input_data.get('tool_name') != 'Bash':
        print(json.dumps({"continue": True}))
        return

    command = input_data.get('tool_input', {}).get('command', '')

    # 按 && / || / ; 和换行拆分复合命令。换行必须断开：不断开的话
    # `ls\nrm -rf x` 会因为整段前缀是 ls 而被放行。
    segments = re.split(r'\s*(?:&&|\|\||;)\s*|\n', command)

    # 如果所有段都是只读，直接放行
    if all(is_readonly_segment(seg) for seg in segments):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": "Auto-allowed read-only compound command"
            }
        }))
    else:
        # 含写操作，继续正常权限流程
        print(json.dumps({"continue": True}))


if __name__ == '__main__':
    main()
