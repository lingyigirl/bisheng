from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    fail: _Node | None = None
    outputs: list[str] = field(default_factory=list)


@dataclass
class _ScanState:
    """Resumable scan position: automaton node + next global character position."""

    node: _Node
    next_pos: int


class ACAutomaton:
    def __init__(self, words: Iterable[str]) -> None:
        self.root = _Node()
        self.root.fail = self.root
        for word in words:
            self._insert(word)
        self._build_fail_links()

    def _insert(self, word: str) -> None:
        node = self.root
        for char in word:
            node = node.children.setdefault(char, _Node())
        if word not in node.outputs:
            node.outputs.append(word)

    def _build_fail_links(self) -> None:
        queue = deque()
        for child in self.root.children.values():
            child.fail = self.root
            queue.append(child)

        while queue:
            current = queue.popleft()
            for char, child in current.children.items():
                fail_node = current.fail
                while fail_node is not self.root and char not in fail_node.children:
                    fail_node = fail_node.fail
                child.fail = fail_node.children.get(char, self.root) if fail_node else self.root
                child.outputs.extend(child.fail.outputs if child.fail else [])
                queue.append(child)

    def in_progress(self, state: _ScanState | None) -> bool:
        """True when some suffix of the consumed text is still a live prefix of a
        longer pattern — callers must hold that suffix back (mask mode) instead
        of emitting it, or the completed word goes out in the clear.

        Walks the FAIL CHAIN, not just the current node. A node is a leaf exactly
        when a word ends there, so ``bool(node.children)`` answers "can THIS node
        extend" — but the text suffix can still be a live prefix through a
        SHORTER suffix. Measured, with the shipped word list:

            words  'jb' (a leaf) and 'bbc中文网'
            stream 'jbbc中文网' one character at a time

        After consuming 'jb' the scan sits on the 'jb' node, which has no
        children; its fail link points at the 'b' node, which does extend into
        'bbc中文网'. Checking only the current node returned False, the 'b' was
        emitted, and 5 of the 6 characters of the later match went out unmasked
        (1530 of the 2809 builtin words end at such a node).

        The fail chain from a node enumerates every suffix of the consumed text
        that is a prefix of some pattern, which is exactly the set that must be
        held back, so walking it is sufficient — not merely a heuristic.
        """
        node = state.node if state is not None else self.root
        while node is not None and node is not self.root:
            if node.children:
                return True
            node = node.fail
        return False

    def find_all(self, text: str) -> Counter[str]:
        _, matches, _ = self.find_all_matches(None, text)
        counter: Counter[str] = Counter()
        for _, word in matches:
            counter[word] += 1
        return counter

    def find_all_matches(
        self,
        state: _ScanState | None,
        text: str,
        capture_at: int | None = None,
    ) -> tuple[_ScanState, list[tuple[int, str]], _ScanState | None]:
        """Incrementally scan ``text`` for pattern matches, resuming from ``state`` (None = fresh start).

        Returns:
            - ``state_after``: resumable scan state after consuming ``text`` (pass it back
              as ``state`` to continue across chunks, e.g. streamed LLM output);
            - ``matches``: ``(end_position, word)`` pairs in scan order, where
              ``end_position`` is the global index of the word's last char (accumulated
              across calls through the state); overlapping patterns at the same position
              are all reported;
            - ``captured_state``: resumable state after consuming the char at global
              position ``capture_at`` (None when not requested / not consumed in this
              scan) — used by callers that emit only a prefix of the scanned text.
        """
        base = state.next_pos if state is not None else 0
        node = state.node if state is not None else self.root
        matches: list[tuple[int, str]] = []
        captured: _ScanState | None = None
        for offset, char in enumerate(text):
            while node is not self.root and char not in node.children:
                node = node.fail or self.root
            node = node.children.get(char, self.root)
            for word in node.outputs:
                matches.append((base + offset, word))
            if capture_at is not None and base + offset == capture_at:
                captured = _ScanState(node=node, next_pos=capture_at + 1)
        return _ScanState(node=node, next_pos=base + len(text)), matches, captured
